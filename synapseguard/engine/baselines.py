"""Baselines for the evaluation and for the product's graph-removed mode.

"Flat" baselines see exactly what a relational table or log index would hold:
one row per service (telemetry + host column), one row per host, one row per
change, and the incident. They never use CALLS relationships. They are the
honest answer to "what if FalkorDB were taken out?".

Graph baselines use the call graph but not our evidence scoring:

* ``naive_deepest``: the previous SynapseGuard heuristic (deepest degraded
  service reachable from the symptom).
* ``pagerank``: personalised PageRank over the anomalous subgraph, in the
  spirit of MicroRCA (Wu et al., NOMS 2020) and MonitorRank (Kim et al.,
  SIGMETRICS 2013). A simplified re-implementation, not the original systems.
"""
from __future__ import annotations

import networkx as nx

from synapseguard.data.scenarios import ANOMALY_THRESHOLD, Scenario
from synapseguard.engine.rca import CHANGE_WINDOW_S, HOST_SATURATION_CPU, NodeInfo, Subgraph, best_change

FLAT_FEATURES = ("onset", "change", "severity", "common")
FLAT_DEFAULT_WEIGHTS = {"onset": 1.0, "change": 1.5, "severity": 0.3, "common": 1.5}


def _degraded(sc: Scenario) -> list[dict]:
    return [s for s in sc.services if s.get("anomaly") is not None and s["anomaly"] >= ANOMALY_THRESHOLD]


def symptom_blame(sc: Scenario) -> list[str]:
    return [sc.incident["service"]]


def flat_anomaly(sc: Scenario) -> list[str]:
    return [s["id"] for s in sorted(_degraded(sc), key=lambda s: (-s["anomaly"], s["id"]))]


def flat_features(sc: Scenario) -> list[tuple[str, dict[str, float]]]:
    """Per-candidate features computable without any relationship traversal."""
    deg = _degraded(sc)
    onsets = [s["onset"] for s in deg]

    def onset_share(t, own=True):
        if t is None:
            return 0.5
        others = list(onsets)
        if own and t in others:
            others.remove(t)
        return 1.0 if not others else sum(1 for o in others if o is None or o >= t) / len(others)

    by_target: dict[str, list[dict]] = {}
    for c in sc.changes:
        by_target.setdefault(c["target"], []).append(c)
    out = []
    for s in deg:
        ch, _ = best_change(by_target.get(s["id"], []), s["onset"])
        out.append((s["id"], {"onset": onset_share(s["onset"]), "change": ch,
                              "severity": s["anomaly"], "common": 0.0}))
    deg_ids = {s["id"] for s in deg}
    for h in sc.hosts:
        if (h.get("cpu") or 0) < HOST_SATURATION_CPU:
            continue
        victims = [s for s in sc.services if s.get("host") == h["id"] and s["id"] in deg_ids]
        if not victims:
            continue
        first = min((v["onset"] for v in victims if v["onset"] is not None), default=None)
        ch, _ = best_change(by_target.get(h["id"], []), first)
        out.append((h["id"], {"onset": onset_share(first, own=False), "change": ch,
                              "severity": max(0.0, min(1.0, (h["cpu"] - HOST_SATURATION_CPU) / 10)),
                              "common": min(1.0, (len(victims) - 1) / 2.0)}))
    return out


def rank_features(feats: list[tuple[str, dict[str, float]]], weights: dict[str, float]) -> list[str]:
    scored = [(sum(weights.get(k, 0) * v for k, v in f.items()), cid) for cid, f in feats]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [cid for _, cid in scored]


def flat_change(sc: Scenario) -> list[str]:
    return rank_features(flat_features(sc), {"change": 1.5, "severity": 1.0})


def flat_evidence(sc: Scenario, weights: dict[str, float] | None = None) -> list[str]:
    """Strongest flat baseline: same signals as ours minus relationships."""
    return rank_features(flat_features(sc), weights or FLAT_DEFAULT_WEIGHTS)


# ------------------------------------------------------------ graph baselines --
def _call_graph(sc: Scenario, sync_only: bool = False) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(s["id"] for s in sc.services)
    g.add_edges_from((e["src"], e["dst"]) for e in sc.edges if e["sync"] or not sync_only)
    return g


def naive_deepest(sc: Scenario, max_depth: int = 5) -> list[str]:
    """Previous SynapseGuard heuristic: deepest degraded node reachable from
    the symptom by any call path (ties: higher anomaly)."""
    g = _call_graph(sc)
    s0 = sc.incident["service"]
    anomaly = {s["id"]: s.get("anomaly") for s in sc.services}
    depth: dict[str, int] = {}
    # longest simple path depth up to max_depth via bounded DFS
    stack = [(s0, 0, {s0})]
    while stack:
        n, d, seen = stack.pop()
        if d > depth.get(n, -1):
            depth[n] = d
        if d == max_depth:
            continue
        for m in g.successors(n):
            if m not in seen:
                stack.append((m, d + 1, seen | {m}))
    cands = [n for n in depth if n != s0 and (anomaly[n] or 0) >= ANOMALY_THRESHOLD]
    cands.sort(key=lambda n: (-depth[n], -(anomaly[n] or 0), n))
    return cands or [s0]


def pagerank(sc: Scenario, sub: Subgraph | None = None, alpha: float = 0.85) -> list[str]:
    """Personalised PageRank on the anomalous propagation subgraph."""
    sub = sub or reference_subgraph(sc)
    g = nx.DiGraph()
    for n, info in sub.nodes.items():
        g.add_node(n)
    for e in sub.edges:
        w = sub.nodes[e["dst"]].anomaly
        g.add_edge(e["src"], e["dst"], weight=max(0.05, w if w is not None else 0.3))
    pers = {n: max(0.01, info.anomaly or 0.0) for n, info in sub.nodes.items()}
    pr = nx.pagerank(g, alpha=alpha, personalization=pers, weight="weight")
    ranked = sorted((n for n in pr if sub.nodes[n].degraded), key=lambda n: (-pr[n], n))
    return ranked or [sc.incident["service"]]


# ------------------------------------------------- in-memory reference model --
def reference_subgraph(sc: Scenario, max_depth: int = 8) -> Subgraph:
    """Pure-Python twin of ``CausalFrontierRCA.extract_subgraph``.

    Used by tests to check that the Cypher traversal returns exactly the same
    subgraph, and by ablations that must not touch the database.
    """
    svc = {s["id"]: s for s in sc.services}
    hosts = {h["id"]: h for h in sc.hosts}
    out: dict[str, list[dict]] = {s: [] for s in svc}
    for e in sc.edges:
        out[e["src"]].append(e)

    def passable(n):
        a = svc[n].get("anomaly")
        return a is None or a >= ANOMALY_THRESHOLD

    s0 = sc.incident["service"]
    sub = Subgraph(symptom=s0)
    sub.depth[s0] = 0
    frontier = [s0]
    edges = []
    for hop in range(1, max_depth + 1):
        nxt = []
        for a in frontier:
            for e in out[a]:
                b = e["dst"]
                if not e["sync"] or not passable(b):
                    continue
                edges.append({"src": a, "dst": b, "protocol": e["protocol"], "rps": e["rps"],
                              "has_fallback": bool(e["has_fallback"])})
                if b not in sub.depth:
                    sub.depth[b] = hop
                    sub.parent[b] = a
                    nxt.append(b)
        frontier = nxt
    for n in sub.depth:
        s = svc[n]
        h = hosts.get(s.get("host"), {})
        sub.nodes[n] = NodeInfo(n, s["name"], s["kind"], s.get("anomaly"), s.get("onset"), s.get("error_rate"),
                                s.get("p99_ms"), s.get("base_error_rate"), s.get("base_p99_ms"),
                                s.get("criticality"), bool(s.get("entrypoint")), s.get("host"), h.get("cpu"))
    from synapseguard.engine.rca import _reaches  # local import avoids a cycle at module load
    down: dict[str, set[str]] = {n: set() for n in sub.nodes}
    for e in edges:
        down[e["src"]].add(e["dst"])
    reach = _reaches(down, {n for n, i in sub.nodes.items() if i.degraded})
    keep = {n for n, i in sub.nodes.items() if i.known or n in reach or n == s0}
    sub.nodes = {n: i for n, i in sub.nodes.items() if n in keep}
    sub.edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
    sub.blind_spots = sorted(n for n, i in sub.nodes.items() if not i.known)
    for hid in sorted({i.host for i in sub.nodes.values() if i.host}):
        h = hosts[hid]
        members = [(s["id"], s.get("anomaly"), s.get("onset")) for s in sc.services if s.get("host") == hid]
        sub.hosts[hid] = {"cpu": h.get("cpu"), "mem": h.get("mem"), "services": members}
    hset = set(sub.hosts)
    sub.changes = [dict(c) for c in sc.changes
                   if (c["target_label"] == "Service" and c["target"] in sub.nodes)
                   or (c["target_label"] == "Host" and c["target"] in hset)]
    return sub


__all__ = [
    "symptom_blame", "flat_anomaly", "flat_change", "flat_evidence", "flat_features", "rank_features",
    "naive_deepest", "pagerank", "reference_subgraph", "FLAT_FEATURES", "FLAT_DEFAULT_WEIGHTS", "CHANGE_WINDOW_S",
]
