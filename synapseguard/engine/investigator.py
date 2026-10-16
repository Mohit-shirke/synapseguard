"""The SynapseGuard investigation agent.

Runs a tool plan over FalkorDB and records every step (tool, purpose,
duration, outcome) in a trace returned to the UI. The plan adapts to what the
graph shows:

* nothing degraded is reachable      -> abstain, no actions
* root is a host                     -> blast radius over all its services, drain
* top confidence is low              -> flag the runner-up for verification
* entrypoints are exposed            -> min-cut breaker plan

``mode="flat"`` is the graph-removed mode: it reads only per-node rows (no
relationship traversal) and runs the strongest flat baseline, so the UI can
show side by side what is lost without the graph.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from synapseguard.core.graph import GraphStore
from synapseguard.data.scenarios import ANOMALY_THRESHOLD, Scenario
from synapseguard.engine import actions as A
from synapseguard.engine import baselines as B
from synapseguard.engine import containment as C
from synapseguard.engine import recall as M
from synapseguard.engine.narrator import narrate
from synapseguard.engine.rca import ALGORITHM_VERSION, CausalFrontierRCA
from synapseguard.engine.tools import Tools, existing_entities

# Tuned on validation seeds by bench/eval_accuracy.py (see bench/results).
FLAT_WEIGHTS = {"onset": 0.25, "change": 1.5, "severity": 0.5, "common": 1.5}


class Trace:
    def __init__(self):
        self.steps: list[dict] = []

    def step(self, tool: str, purpose: str):
        trace = self

        class _Step:
            def __enter__(self_inner):
                self_inner.t0 = time.perf_counter()
                self_inner.rec = {"tool": tool, "purpose": purpose}
                return self_inner.rec

            def __exit__(self_inner, exc_type, exc, tb):
                self_inner.rec["ms"] = round((time.perf_counter() - self_inner.t0) * 1000, 2)
                if exc is not None:
                    self_inner.rec["error"] = f"{exc_type.__name__}: {exc}"
                trace.steps.append(self_inner.rec)
                return False

        return _Step()


class Investigator:
    def __init__(self, store: GraphStore, rca: CausalFrontierRCA | None = None):
        self.store = store
        self.rca = rca or CausalFrontierRCA(store)

    def investigate(self, incident_id: str, mode: str = "graph", persist: bool = True) -> dict[str, Any]:
        if mode == "flat":
            return self.investigate_flat(incident_id)
        t0 = time.perf_counter()
        inv_id = f"inv-{uuid.uuid4().hex[:10]}"
        tr = Trace()
        with tr.step("get_incident", "Load the incident and the service it was reported on") as s:
            inc = self.rca.incident(incident_id)
            s["result"] = f"reported on {inc['service']}"
        with tr.step("extract_anomalous_subgraph",
                     "Follow synchronous CALLS downstream through degraded/unknown services") as s:
            sub = self.rca.extract_subgraph(inc["service"])
            s["result"] = (f"{len(sub.nodes)} services, {len(sub.edges)} calls, "
                           f"{len(sub.degraded_ids())} degraded, {len(sub.blind_spots)} blind spots, "
                           f"{sub.query_count} Cypher queries")
        with tr.step("score_candidates", "Score frontier/coverage/onset/change/host/lag evidence") as s:
            cands = self.rca.rank(self.rca.features(sub))
            for c in cands[:3]:
                self.rca.explain(sub, c)
            s["result"] = (", ".join(f"{c.id} {c.confidence:.0%}" for c in cands[:3]) or "no candidates") +                 f" (profile: {self.rca.last_profile})"
        top = cands[0] if cands else None
        runner = cands[1] if len(cands) > 1 else None
        abstain = None if top else ("The reported service is not degraded and nothing degraded is reachable "
                                    "from it through synchronous calls.")
        blast = breakers = page = None
        similar: list[dict] = []
        acts: list[dict] = []
        if top:
            with tr.step("recall_similar_incidents", "Look for past incidents with overlapping impact") as s:
                if persist:
                    M.record_investigation(self.store, inv_id, incident_id, cands, sub.degraded_ids(),
                                           ALGORITHM_VERSION)
                similar = M.similar_incidents(self.store, incident_id, top.id)
                s["result"] = f"{len(similar)} similar past incidents"
            with tr.step("blast_radius", "Upstream BFS from the root over CALLS") as s:
                roots = C.root_services(self.store, top.id, top.label)
                blast = C.blast_radius(self.store, roots)
                s["result"] = (f"{len(blast['impacted'])} impacted, entrypoints {blast['entrypoints'] or 'none'}")
            if blast["entrypoints"]:
                with tr.step("breaker_min_cut", "algo.maxFlow over cut_cost, then residual min-cut") as s:
                    breakers = C.breaker_plan(self.store, roots, blast)
                    s["result"] = (f"{len(breakers['cut'])} breakers, cost {breakers.get('cut_cost')}"
                                   if breakers["status"] == "ok" else breakers["status"])
            with tr.step("who_to_page", "On-call owner, change authors and people with history (ON_CALL, AUTHORED, WORKED_ON)") as s:
                page = Tools(self.store).who_to_page(top.id, at=inc["opened_at"])
                s["result"] = page["answer"]
            with tr.step("propose_actions", "Derive actions from the evidence; mutating ones need approval") as s:
                acts = A.propose(inv_id, inc, top, runner, sub, blast, breakers, page)
                if persist:
                    A.persist(self.store, inv_id, acts)
                s["result"] = f"{sum(a['requires_approval'] for a in acts)} need approval, " \
                              f"{sum(not a['requires_approval'] for a in acts)} advisories"
        with tr.step("narrate", "Summarise from evidence (LLM optional, grounding-checked)") as s:
            store = self.store
            story = narrate(inc, top, blast, abstain, lambda text: existing_entities(store, text))
            s["result"] = story["source"]
        return {
            "investigation_id": inv_id,
            "mode": "graph",
            "algorithm": ALGORITHM_VERSION,
            "scoring_profile": self.rca.last_profile,
            "incident": inc,
            "abstain_reason": abstain,
            "candidates": [_cand(c) for c in cands[:5]],
            "root_cause": _cand(top) if top else None,
            "subgraph": {
                "nodes": [_node(n) for n in sub.nodes.values()],
                "edges": [{"src": e["src"], "dst": e["dst"], "has_fallback": e.get("has_fallback")} for e in sub.edges],
                "blind_spots": sub.blind_spots,
            },
            "blast_radius": blast,
            "breakers": breakers,
            "who_to_page": page and {"outcome": page["outcome"], "answer": page["answer"],
                                     "people": page.get("people", [])},
            "actions": acts,
            "similar_incidents": similar,
            "narrative": story,
            "trace": tr.steps,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def investigate_flat(self, incident_id: str) -> dict[str, Any]:
        """Graph-removed mode: per-node rows only, no relationship traversal."""
        t0 = time.perf_counter()
        inc = self.rca.incident(incident_id)
        sc = flat_snapshot(self.store, inc)
        feats = B.flat_features(sc)
        ranked = B.rank_features(feats, FLAT_WEIGHTS)
        scores = {cid: round(sum(FLAT_WEIGHTS.get(k, 0) * v for k, v in f.items()), 4) for cid, f in feats}
        return {
            "mode": "flat",
            "incident": inc,
            "candidates": [{"id": c, "score": scores[c]} for c in ranked[:5]],
            "root_cause": {"id": ranked[0], "score": scores[ranked[0]]} if ranked else None,
            "unavailable": [
                "causal path from symptom to root (needs CALLS traversal)",
                "pruning of degraded services that cannot reach the symptom",
                "blast radius (needs upstream traversal)",
                "circuit-breaker placement (needs max-flow over CALLS)",
                "predicted recovery set",
            ],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        }


def flat_snapshot(store: GraphStore, inc: dict) -> Scenario:
    """Per-node rows as a relational table would hold them (host is a column)."""
    services = [r[0] | {"host": r[1]} for r in store.rows(
        "MATCH (s:Service) OPTIONAL MATCH (s)-[:RUNS_ON]->(h:Host) RETURN properties(s), h.id")]
    hosts = [r[0] for r in store.rows("MATCH (h:Host) RETURN properties(h)")]
    changes = [r[0] | {"target": r[1], "target_label": r[2]} for r in store.rows(
        "MATCH (c:Change)-[:CHANGED]->(t) RETURN properties(c), t.id, labels(t)[0]")]
    for s in services:
        s.setdefault("anomaly", None)
        s.setdefault("onset", None)
    return Scenario(name="flat", seed=0, description="", services=services, edges=[], hosts=hosts,
                    changes=changes, incident={"id": inc["id"], "service": inc["service"]})


def _cand(c) -> dict:
    return {"id": c.id, "label": c.label, "name": c.name, "score": c.score, "confidence": c.confidence,
            "features": c.features, "evidence": c.evidence, "path": c.path,
            "predicted_recovery": c.predicted_recovery}


def _node(n) -> dict:
    return {"id": n.id, "name": n.name, "kind": n.kind, "anomaly": n.anomaly, "onset": n.onset,
            "degraded": n.anomaly is not None and n.anomaly >= ANOMALY_THRESHOLD, "host": n.host,
            "error_rate": n.error_rate, "p99_ms": n.p99_ms}
