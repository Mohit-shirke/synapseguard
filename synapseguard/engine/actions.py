"""Turn an investigation into proposed actions, and apply approved ones.

Every mutating action starts as PROPOSED and needs explicit human approval
(``approve``) before ``execute``. Execution is a **simulation** against the
graph: there is no real infrastructure behind this demo. A simulated rollback
resets the fixed services' telemetry to baseline so a re-investigation shows
what would remain broken.
"""
from __future__ import annotations

import time
from typing import Any

from synapseguard.core.graph import GraphStore
from synapseguard.data.loader import refresh_cut_costs

STATUSES = ("PROPOSED", "APPROVED", "REJECTED", "EXECUTED_SIMULATED")
# Below this relative confidence the agent asks for a second look. Calibrated on held-out
# synthetic incidents: >=0.95 was right 98.1% of the time, 0.80-0.95 only 81% (bench/results).
VERIFY_BELOW = 0.9


def propose(inv_id: str, incident: dict, top: Any, runner_up: Any | None, sub: Any,
            blast: dict | None, breakers: dict | None, page: dict | None = None) -> list[dict]:
    acts: list[dict] = []

    def add(kind, summary, target_id, target_label, risk, rationale, refs, mutating=True, **extra):
        acts.append({
            "id": f"{inv_id}-a{len(acts) + 1}", "kind": kind, "summary": summary, "target_id": target_id,
            "target_label": target_label, "risk": risk, "rationale": rationale, "refs": refs,
            "requires_approval": mutating, "status": "PROPOSED" if mutating else "ADVISORY", **extra,
        })

    blast_n = len(blast["impacted"]) if blast else 0
    risk = "high" if blast and blast["high_criticality"] else ("medium" if blast_n > 3 else "low")
    change_ev = next((e for e in top.evidence if e["kind"] == "change"), None)
    fixed = top.predicted_recovery
    if change_ev:
        cid = change_ev["refs"][0]
        add("rollback_change", f"Roll back {cid} on {top.id}", cid, "Change", "low",
            change_ev["statement"], [cid, top.id], expected_recovery=fixed, fix_target=top.id,
            fix_label=top.label)
    elif top.label == "Host":
        add("drain_host", f"Cordon and drain host {top.id}; reschedule its services", top.id, "Host", risk,
            next((e["statement"] for e in top.evidence if e["kind"] == "host"), ""), [top.id],
            expected_recovery=fixed, fix_target=top.id, fix_label="Host")
    else:
        add("restart_or_scale", f"Restart / scale out {top.id} (no recent change found to roll back)",
            top.id, "Service", risk, "Root candidate with no incriminating change; restore capacity first",
            [top.id], expected_recovery=fixed, fix_target=top.id, fix_label="Service")

    if breakers and breakers.get("status") == "ok" and breakers["cut"]:
        cut = breakers["cut"]
        naive = breakers["naive"]
        compare = ("same as breaking every direct caller" if naive["feasible"] and naive["cost"] == breakers["cut_cost"]
                   else "direct callers of the root cannot all be broken" if not naive["feasible"]
                   else f"cheaper than breaking every direct caller (cost {naive['cost']})")
        entry = set(blast["entrypoints"]) if blast else set()
        sheds_entry = any(e["src"] in entry for e in cut)
        brisk = "high" if sheds_entry else ("medium" if any(not e["target_degraded"] for e in cut) else "low")
        add("circuit_breakers",
            "Open circuit breakers on " + ", ".join(f"{e['src']}->{e['dst']}" for e in cut)
            + (" (sheds entrypoint traffic: prefer the rollback)" if sheds_entry else ""),
            top.id, top.label, brisk,
            f"Minimum cut from entrypoints to the root (FalkorDB algo.maxFlow): cost {breakers['cut_cost']}, "
            f"sheds {breakers['shed_rps']} rps; {compare}",
            [e["src"] for e in cut] + [e["dst"] for e in cut], edges=[[e["src"], e["dst"]] for e in cut])

    if page and page.get("people"):
        first = page["people"][0]
        add("page", f"Page {first['name']} ({first['role']} for {top.id})", first["person"], "Person", "none",
            "; ".join(f"{p['name']}: {p['reason']}" for p in page["people"][:3]),
            [first["person"], top.id], mutating=False)

    symptom = incident["service"]
    if symptom != top.id and symptom not in (top.path[-1:] if top.label == "Host" else []):
        add("advisory", f"Do not restart {symptom}",
            symptom, "Service", "none",
            f"{symptom} is degraded because of what it calls: path " + " -> ".join(top.path),
            top.path, mutating=False)
    if runner_up is not None and top.confidence < VERIFY_BELOW:
        add("advisory", f"Low confidence ({top.confidence:.0%}): also check {runner_up.id}",
            runner_up.id, runner_up.label, "none",
            f"Runner-up score {runner_up.score} vs {top.score}", [runner_up.id], mutating=False)
    return acts


def persist(store: GraphStore, inv_id: str, actions: list[dict]) -> None:
    rows = [{k: (v if not isinstance(v, (list, dict)) else None) for k, v in a.items()} | {
        "refs": [str(r) for r in a["refs"]], "expected_recovery": a.get("expected_recovery") or [],
        "edges": [f"{s}->{d}" for s, d in a.get("edges", [])]} for a in actions]
    store.query("""
        MATCH (v:Investigation {id: $inv})
        UNWIND $rows AS r
        CREATE (a:Action {id: r.id, kind: r.kind, summary: r.summary, risk: r.risk, rationale: r.rationale,
                          status: r.status, requires_approval: r.requires_approval, refs: r.refs,
                          expected_recovery: r.expected_recovery, edges: r.edges, target_id: r.target_id,
                          target_label: r.target_label, fix_target: r.fix_target, fix_label: r.fix_label,
                          created_at: timestamp()})
        CREATE (v)-[:PROPOSES]->(a)
        WITH a, r
        OPTIONAL MATCH (s:Service {id: r.target_id})
        OPTIONAL MATCH (h:Host {id: r.target_id})
        OPTIONAL MATCH (c:Change {id: r.target_id})
        WITH a, coalesce(c, h, s) AS t
        WHERE t IS NOT NULL
        CREATE (a)-[:TARGETS]->(t)
    """, {"inv": inv_id, "rows": rows})


def get(store: GraphStore, action_id: str) -> dict:
    rows = store.rows("MATCH (a:Action {id: $id}) RETURN properties(a)", {"id": action_id})
    if not rows:
        raise KeyError(f"action {action_id!r} not found")
    return rows[0][0]


def decide(store: GraphStore, action_id: str, approve: bool, by: str) -> dict:
    a = get(store, action_id)
    if a["status"] != "PROPOSED":
        raise ValueError(f"action is {a['status']}, only PROPOSED actions can be approved or rejected")
    store.query("MATCH (a:Action {id: $id}) SET a.status = $st, a.decided_by = $by, a.decided_at = timestamp()",
                {"id": action_id, "st": "APPROVED" if approve else "REJECTED", "by": by})
    return get(store, action_id)


def execute(store: GraphStore, action_id: str) -> dict:
    """Simulate an approved action against the graph."""
    a = get(store, action_id)
    if a["status"] != "APPROVED":
        raise ValueError("only APPROVED actions can be executed")
    t0 = time.perf_counter()
    if a["kind"] in ("rollback_change", "drain_host", "restart_or_scale"):
        heal = list(a.get("expected_recovery") or [])
        if a.get("fix_label") == "Host":
            store.query("MATCH (h:Host {id: $id}) SET h.cpu = 45.0, h.mem = 50.0, h.saturated = false",
                        {"id": a["fix_target"]})
        elif a.get("fix_target"):
            heal.append(a["fix_target"])
        store.query("""
            MATCH (s:Service) WHERE s.id IN $ids
            SET s.error_rate = s.base_error_rate, s.p99_ms = s.base_p99_ms, s.anomaly = 0.0,
                s.onset = null, s.degraded = false
        """, {"ids": heal})
        if a["kind"] == "rollback_change":
            store.query("MATCH (c:Change {id: $id}) SET c.rolled_back = true", {"id": a["target_id"]})
    elif a["kind"] == "circuit_breakers":
        pairs = [e.split("->") for e in a.get("edges") or []]
        store.query("""
            UNWIND $pairs AS p MATCH (:Service {id: p[0]})-[r:CALLS]->(:Service {id: p[1]})
            SET r.breaker_open = true
        """, {"pairs": pairs})
    refresh_cut_costs(store)
    store.query("MATCH (a:Action {id: $id}) SET a.status = 'EXECUTED_SIMULATED', a.executed_at = timestamp()",
                {"id": action_id})
    out = get(store, action_id)
    out["elapsed_ms"] = (time.perf_counter() - t0) * 1000
    return out
