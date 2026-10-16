"""Blast radius and circuit-breaker placement.

Blast radius: every service that (transitively) calls the root, found with a
hop-bounded upstream BFS in FalkorDB. Synchronous exposure (fails now) is
separated from asynchronous exposure (lags, backlog).

Breaker placement is a minimum s-t cut problem. Sources are the entrypoints
in the blast radius, sinks are the faulty services, and every CALLS edge
has a ``cut_cost`` computed in FalkorDB (``loader.refresh_cut_costs``):
the healthy functionality lost by opening a breaker on it,

* ``rps * (1 + healthy services the callee calls)``  if the caller can circuit-break
* ``x 10``  on top if the callee itself is healthy
* ``1e6``   if the caller has no breaker (cannot be cut)

so the cut lands as close to the fault as breaker capability allows instead of
severing a high-fan-out edge near the entrypoint.

FalkorDB's ``algo.maxFlow`` computes the max flow using ``cut_cost`` as capacity.
We rebuild the residual graph from the returned per-edge flows and read off the
min cut: edges from the residual-reachable side to the rest. By max-flow/min-cut,
the cut's total cost equals ``maxFlow``, and we assert that.
"""
from __future__ import annotations

import time
from typing import Any

from synapseguard.core.graph import GraphStore
from synapseguard.data.loader import BREAKER_INF

Q_HOP_UP = """
MATCH (b:Service) WHERE b.id IN $frontier
WITH b
MATCH (a:Service)-[r:CALLS]->(b)
RETURN b.id, a.id, r.sync, a.entrypoint, a.criticality, a.degraded
"""

Q_HOST_SERVICES = "MATCH (h:Host {id: $id})<-[:RUNS_ON]-(s:Service) RETURN s.id"

Q_EDGES_AMONG = """
MATCH (a:Service) WHERE a.id IN $ids
WITH a
MATCH (a)-[r:CALLS]->(b:Service) WHERE b.id IN $ids
RETURN a.id, b.id, r.cut_cost, r.rps, r.breaker, r.has_fallback, b.degraded
"""

Q_MAXFLOW = """
MATCH (s:Service) WHERE s.id IN $sources
WITH collect(s) AS src
MATCH (t:Service) WHERE t.id IN $sinks
WITH src, collect(t) AS dst
CALL algo.maxFlow({sourceNodes: src, targetNodes: dst, capacityProperty: 'cut_cost',
                   relationshipTypes: ['CALLS'], nodeLabels: ['Service']})
YIELD edges, edgeFlows, maxFlow
RETURN [e IN edges | [startNode(e).id, endNode(e).id]], edgeFlows, maxFlow
"""


def root_services(store: GraphStore, root_id: str, label: str) -> list[str]:
    if label == "Host":
        return [r[0] for r in store.rows(Q_HOST_SERVICES, {"id": root_id})]
    return [root_id]


def blast_radius(store: GraphStore, roots: list[str], max_depth: int = 8) -> dict[str, Any]:
    t0 = time.perf_counter()
    hops: dict[str, int] = {r: 0 for r in roots}
    sync_exposed: set[str] = set(roots)
    meta: dict[str, dict] = {}
    frontier = list(roots)
    for hop in range(1, max_depth + 1):
        if not frontier:
            break
        nxt = []
        for callee, caller, sync, entry, crit, degraded in store.rows(Q_HOP_UP, {"frontier": frontier}):
            meta.setdefault(caller, {"entrypoint": bool(entry), "criticality": crit, "degraded": bool(degraded)})
            if sync and callee in sync_exposed:
                sync_exposed.add(caller)
            if caller not in hops:
                hops[caller] = hop
                nxt.append(caller)
        frontier = nxt
    impacted = sorted((n for n in hops if n not in roots), key=lambda n: (hops[n], n))
    return {
        "roots": roots,
        "impacted": [{"id": n, "hops": hops[n], "sync": n in sync_exposed, **meta.get(n, {})} for n in impacted],
        "entrypoints": sorted(n for n in impacted if meta.get(n, {}).get("entrypoint")),
        "sync_count": sum(1 for n in impacted if n in sync_exposed),
        "async_only_count": sum(1 for n in impacted if n not in sync_exposed),
        "high_criticality": sorted(n for n in impacted if meta.get(n, {}).get("criticality") == "high"),
        "max_hops": max((hops[n] for n in impacted), default=0),
        "elapsed_ms": (time.perf_counter() - t0) * 1000,
    }


def breaker_plan(store: GraphStore, roots: list[str], blast: dict[str, Any]) -> dict[str, Any]:
    """Cheapest set of breaker-capable calls that isolates all entrypoints from ``roots``."""
    t0 = time.perf_counter()
    sources = [s for s in blast["entrypoints"] if s not in roots]
    naive = _naive_plan(store, roots)
    if not sources:
        return {"status": "no_entrypoint_exposed", "cut": [], "naive": naive,
                "elapsed_ms": (time.perf_counter() - t0) * 1000}
    rows = store.rows(Q_MAXFLOW, {"sources": sources, "sinks": roots})
    pairs, flows, max_flow = rows[0] if rows else ([], [], 0)
    flow = {(a, b): f for (a, b), f in zip(pairs, flows)}

    ids = sorted(set(roots) | {i["id"] for i in blast["impacted"]})
    cap: dict[tuple[str, str], dict] = {}
    for a, b, cost, rps, breaker, fallback, degraded in store.rows(Q_EDGES_AMONG, {"ids": ids}):
        cap[(a, b)] = {"cost": cost, "rps": rps, "breaker": bool(breaker), "has_fallback": bool(fallback),
                       "target_degraded": bool(degraded)}

    # residual graph reachability from the sources
    adj: dict[str, list[str]] = {}
    for (a, b), e in cap.items():
        f = flow.get((a, b), 0.0)
        if f < e["cost"] - 1e-9:
            adj.setdefault(a, []).append(b)
        if f > 1e-9:
            adj.setdefault(b, []).append(a)
    reach = set(sources)
    stack = list(sources)
    while stack:
        n = stack.pop()
        for m in adj.get(n, ()):
            if m not in reach:
                reach.add(m)
                stack.append(m)
    cut = [{"src": a, "dst": b, **e} for (a, b), e in sorted(cap.items()) if a in reach and b not in reach]
    cut_cost = sum(e["cost"] for e in cut)
    feasible = max_flow < BREAKER_INF
    assert abs(cut_cost - max_flow) < 1e-6 or not feasible, (cut_cost, max_flow)
    return {
        "status": "ok" if feasible else "no_breaker_cut",
        "max_flow": max_flow,
        "cut": cut if feasible else [],
        "cut_cost": cut_cost if feasible else None,
        "shed_rps": sum(e["rps"] for e in cut) if feasible else None,
        "naive": naive,
        "elapsed_ms": (time.perf_counter() - t0) * 1000,
    }


def _naive_plan(store: GraphStore, roots: list[str]) -> dict[str, Any]:
    """Baseline: break every direct call into the root, if possible."""
    rows = store.rows("""
        MATCH (b:Service) WHERE b.id IN $roots
        WITH b
        MATCH (a:Service)-[r:CALLS]->(b) WHERE NOT a.id IN $roots
        RETURN a.id, b.id, r.rps, r.breaker, r.cut_cost
    """, {"roots": roots})
    edges = [{"src": a, "dst": b, "rps": rps, "breaker": bool(br), "cost": cost} for a, b, rps, br, cost in rows]
    feasible = all(e["breaker"] for e in edges)
    return {"edges": edges, "feasible": feasible,
            "cost": sum(e["cost"] for e in edges) if feasible else None}
