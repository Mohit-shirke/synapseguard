"""Load scenarios into FalkorDB with batched, parameterised UNWIND queries.

Snapshot semantics: loading a scenario upserts the topology, replaces live
telemetry and the change log, and archives open incidents. It does **not**
delete past ``Incident``/``Investigation``/``Action`` nodes, so the agent's
memory survives scenario reloads and process restarts.
"""
from __future__ import annotations

import re
from typing import Any

from synapseguard.core.graph import GraphStore
from synapseguard.data.org import build_org
from synapseguard.data.scenarios import ANOMALY_THRESHOLD, Scenario  # noqa: F401

BREAKER_INF = 1_000_000  # cut cost for edges whose client has no circuit breaker
HEALTHY_TARGET_PENALTY = 10  # cutting calls to a healthy service drops healthy work

# Cost of opening a breaker on (a)-[r]->(b): the healthy functionality it removes.
# rps * (1 + number of healthy services b calls), x10 if b itself is healthy.
# Edges whose client cannot circuit-break are effectively uncuttable.
Q_REFRESH_CUT_COSTS = f"""
MATCH (a:Service)-[r:CALLS]->(b:Service)
OPTIONAL MATCH (b)-[:CALLS]->(c:Service) WHERE c.degraded = false
WITH r, b, count(c) AS healthy_fanout
SET r.cut_cost = CASE WHEN r.breaker
    THEN toInteger(r.rps * (1 + healthy_fanout) * CASE WHEN b.degraded THEN 1 ELSE {HEALTHY_TARGET_PENALTY} END)
    ELSE {BREAKER_INF} END
"""


def refresh_cut_costs(store: GraphStore) -> None:
    store.query(Q_REFRESH_CUT_COSTS, timeout_ms=60_000)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")
MAX_SERVICES = 100_000
MAX_EDGES = 1_000_000


class ScenarioValidationError(ValueError):
    pass


def validate(sc: Scenario) -> None:
    errs: list[str] = []
    if len(sc.services) > MAX_SERVICES or len(sc.edges) > MAX_EDGES:
        errs.append("scenario too large")
    sids = [s.get("id") for s in sc.services]
    hids = [h.get("id") for h in sc.hosts]
    for i in sids + hids + [c.get("id") for c in sc.changes] + [sc.incident.get("id")]:
        if not isinstance(i, str) or not ID_RE.match(i):
            errs.append(f"invalid id {i!r}")
    if len(set(sids)) != len(sids):
        errs.append("duplicate service ids")
    if len(set(hids)) != len(hids):
        errs.append("duplicate host ids")
    sset, hset = set(sids), set(hids)
    for s in sc.services:
        if s.get("host") is not None and s["host"] not in hset:
            errs.append(f"service {s['id']} on unknown host {s['host']}")
        for k in ("error_rate", "base_error_rate"):
            v = s.get(k)
            if v is not None and not (isinstance(v, (int, float)) and 0 <= v <= 1):
                errs.append(f"{s['id']}.{k} must be in [0,1]")
        for k in ("p99_ms", "base_p99_ms"):
            v = s.get(k)
            if v is not None and not (isinstance(v, (int, float)) and v >= 0):
                errs.append(f"{s['id']}.{k} must be >= 0")
    for e in sc.edges:
        if e.get("src") not in sset or e.get("dst") not in sset:
            errs.append(f"edge {e.get('src')}->{e.get('dst')} references unknown service")
    for c in sc.changes:
        pool = hset if c.get("target_label") == "Host" else sset
        if c.get("target") not in pool:
            errs.append(f"change {c.get('id')} targets unknown {c.get('target_label')} {c.get('target')}")
    if sc.incident.get("service") not in sset:
        errs.append("incident references unknown service")
    if errs:
        raise ScenarioValidationError("; ".join(errs[:20]))


def _edge_rows(sc: Scenario) -> list[dict[str, Any]]:
    return [{**{k: e.get(k) for k in ("src", "dst", "protocol", "sync", "rps", "has_fallback")},
             "breaker": bool(e.get("breaker", e.get("protocol") == "grpc")),
             "weight": 1 if e.get("sync") else 3} for e in sc.edges]


def load_scenario(store: GraphStore, sc: Scenario, *, reset: bool = False) -> dict[str, int]:
    validate(sc)
    if reset:
        store.drop()
    store.ensure_indexes()
    # live state is replaced; history (incidents, investigations, actions) is kept
    store.query("MATCH ()-[r:CALLS]->() DELETE r", timeout_ms=60_000)
    store.query("MATCH ()-[r:RUNS_ON]->() DELETE r", timeout_ms=60_000)
    store.query("MATCH (c:Change) DETACH DELETE c", timeout_ms=60_000)
    store.query("MATCH (i:Incident {status: 'OPEN'}) SET i.status = 'ARCHIVED'")
    # services/hosts that are not part of the new topology are removed from the live view
    store.query("MATCH (s:Service) WHERE NOT s.id IN $ids DETACH DELETE s",
                {"ids": [s["id"] for s in sc.services]}, timeout_ms=60_000)
    store.query("MATCH (h:Host) WHERE NOT h.id IN $ids DETACH DELETE h",
                {"ids": [h["id"] for h in sc.hosts]}, timeout_ms=60_000)

    store.batch("""
        UNWIND $rows AS r
        MERGE (h:Host {id: r.id})
        SET h.name = r.name, h.zone = r.zone, h.cpu = r.cpu, h.mem = r.mem, h.saturated = r.cpu >= 90
    """, ({k: h.get(k) for k in ("id", "name", "zone", "cpu", "mem")} for h in sc.hosts))

    svc_keys = ("id", "name", "kind", "language", "entrypoint", "criticality", "base_error_rate", "base_p99_ms",
                "error_rate", "p99_ms", "anomaly", "onset", "host")
    store.batch("""
        UNWIND $rows AS r
        MERGE (s:Service {id: r.id})
        SET s.name = r.name, s.kind = r.kind, s.language = r.language, s.entrypoint = r.entrypoint,
            s.criticality = r.criticality, s.base_error_rate = r.base_error_rate, s.base_p99_ms = r.base_p99_ms,
            s.error_rate = r.error_rate, s.p99_ms = r.p99_ms, s.anomaly = r.anomaly, s.onset = r.onset,
            s.degraded = coalesce(r.anomaly, 0.0) >= $tau
        WITH s, r
        MATCH (h:Host {id: r.host})
        CREATE (s)-[:RUNS_ON]->(h)
    """.replace("$tau", str(ANOMALY_THRESHOLD)), ({k: s.get(k) for k in svc_keys} for s in sc.services))

    store.batch("""
        UNWIND $rows AS r
        MATCH (a:Service {id: r.src}), (b:Service {id: r.dst})
        CREATE (a)-[:CALLS {protocol: r.protocol, sync: r.sync, rps: r.rps, has_fallback: r.has_fallback,
                            breaker: r.breaker, weight: r.weight}]->(b)
    """, _edge_rows(sc))

    chg_keys = ("id", "kind", "summary", "at", "author", "param", "old", "new", "target")
    for label in ("Service", "Host"):
        store.batch(f"""
            UNWIND $rows AS r
            MATCH (t:{label} {{id: r.target}})
            CREATE (c:Change {{id: r.id, kind: r.kind, summary: r.summary, at: r.at, author: r.author,
                               param: r.param, old: r.old, new: r.new}})-[:CHANGED]->(t)
        """, ({k: c.get(k) for k in chg_keys} for c in sc.changes if c.get("target_label", "Service") == label))

    refresh_cut_costs(store)
    load_org(store, sc.org or build_org([s["id"] for s in sc.services], [h["id"] for h in sc.hosts]))

    inc = sc.incident
    store.query("""
        MATCH (s:Service {id: $service})
        MERGE (i:Incident {id: $id})
        SET i.title = $title, i.severity = $severity, i.opened_at = $opened_at, i.status = 'OPEN',
            i.scenario = $scenario
        MERGE (i)-[:REPORTED_ON]->(s)
    """, {**{k: inc[k] for k in ("id", "title", "severity", "opened_at", "service")}, "scenario": sc.name})
    return {"services": len(sc.services), "edges": len(sc.edges), "hosts": len(sc.hosts),
            "changes": len(sc.changes)}


def load_org(store: GraphStore, org: dict) -> dict[str, int]:
    """Upsert teams and people; replace ownership, rotations, history and authorship edges."""
    for rel in ("MEMBER_OF", "OWNS", "ON_CALL", "WORKED_ON", "AUTHORED"):
        store.query(f"MATCH ()-[r:{rel}]->() DELETE r", timeout_ms=60_000)
    store.query("MATCH (p:Person) WHERE NOT p.id IN $ids DETACH DELETE p", {"ids": [p["id"] for p in org["people"]]})
    store.query("MATCH (t:Team) WHERE NOT t.id IN $ids DETACH DELETE t", {"ids": [t["id"] for t in org["teams"]]})
    store.batch("UNWIND $rows AS r MERGE (t:Team {id: r.id}) SET t.name = r.name", org["teams"])
    store.batch("""
        UNWIND $rows AS r
        MERGE (p:Person {id: r.id}) SET p.name = r.name, p.email = r.email
        WITH p, r MATCH (t:Team {id: r.team}) CREATE (p)-[:MEMBER_OF]->(t)
    """, org["people"])
    for label in ("Service", "Host"):
        store.batch(f"""
            UNWIND $rows AS r
            MATCH (t:Team {{id: r.team}}), (x:{label} {{id: r.target}})
            CREATE (t)-[:OWNS]->(x)
        """, [o for o in org["owns"] if o["label"] == label])
    store.batch("""
        UNWIND $rows AS r
        MATCH (p:Person {id: r.person}), (t:Team {id: r.team})
        CREATE (p)-[:ON_CALL {from: r.from, to: r.to}]->(t)
    """, org["oncall"])
    store.batch("""
        UNWIND $rows AS r
        MATCH (p:Person {id: r.person}), (s:Service {id: r.service})
        CREATE (p)-[:WORKED_ON {commits: r.commits}]->(s)
    """, org["worked_on"])
    store.query("MATCH (c:Change), (p:Person) WHERE p.email = c.author CREATE (p)-[:AUTHORED]->(c)",
                timeout_ms=60_000)
    return {"teams": len(org["teams"]), "people": len(org["people"]), "oncall": len(org["oncall"])}
