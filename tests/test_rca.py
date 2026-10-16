"""Graph RCA behaviour against a live FalkorDB."""
import time

import pytest

from synapseguard.data.loader import load_scenario
from synapseguard.data.scenarios import generate
from synapseguard.data.topology import layered_topology
from synapseguard.engine.baselines import reference_subgraph
from synapseguard.engine.rca import CausalFrontierRCA


def _edges(sub):
    return {(e["src"], e["dst"]) for e in sub.edges}


@pytest.mark.parametrize("seed", range(25))
def test_cypher_traversal_equals_reference_otel(fresh, otel, seed):
    sc = generate(otel, seed)
    load_scenario(fresh, sc)
    sub = CausalFrontierRCA(fresh).extract_subgraph(sc.incident["service"])
    ref = reference_subgraph(sc)
    assert set(sub.nodes) == set(ref.nodes)
    assert _edges(sub) == _edges(ref)
    assert sub.depth == {k: v for k, v in ref.depth.items() if k in sub.depth}


@pytest.mark.parametrize("seed", range(5))
def test_cypher_traversal_equals_reference_layered(fresh, seed):
    sc = generate(layered_topology(250, seed=seed), seed)
    load_scenario(fresh, sc)
    sub = CausalFrontierRCA(fresh).extract_subgraph(sc.incident["service"])
    ref = reference_subgraph(sc)
    assert set(sub.nodes) == set(ref.nodes) and _edges(sub) == _edges(ref)


def test_depth_limit_respected(loaded):
    store, sc = loaded
    for k in (1, 2, 3):
        sub = CausalFrontierRCA(store, max_depth=k).extract_subgraph(sc.incident["service"])
        assert max(sub.depth.values()) <= k


def test_investigation_finds_root_with_grounded_evidence(loaded):
    store, sc = loaded
    r = CausalFrontierRCA(store).investigate(sc.incident["id"])
    top = r["candidates"][0]
    assert top.id == sc.truth["root_id"]
    assert abs(sum(c.confidence for c in r["candidates"]) - 1.0) < 1e-3
    existing = {row[0] for row in store.rows("MATCH (n) RETURN n.id")}
    for e in top.evidence:
        assert e["refs"] and all(ref in existing for ref in e["refs"]), e
    assert top.path[0] == sc.incident["service"] and top.path[-1] == top.id


def test_ranking_is_deterministic(loaded):
    store, sc = loaded
    rca = CausalFrontierRCA(store)
    a = [(c.id, c.score) for c in rca.investigate(sc.incident["id"])["candidates"]]
    b = [(c.id, c.score) for c in rca.investigate(sc.incident["id"])["candidates"]]
    assert a == b


def test_healthy_symptom_is_never_blamed_but_search_continues(loaded):
    """A user-reported incident may precede the metrics: search downstream anyway,
    but never blame a service whose own telemetry is healthy."""
    store, sc = loaded
    s = sc.incident["service"]
    store.query("MATCH (s:Service {id: $id}) SET s.anomaly = 0.0, s.degraded = false", {"id": s})
    r = CausalFrontierRCA(store).investigate(sc.incident["id"])
    assert all(c.id != s for c in r["candidates"])
    assert r["candidates"][0].id == sc.truth["root_id"]


def test_nothing_degraded_abstains(loaded):
    store, sc = loaded
    store.query("MATCH (s:Service) SET s.anomaly = 0.0, s.degraded = false")
    r = CausalFrontierRCA(store).investigate(sc.incident["id"])
    assert r["candidates"] == [] and r["abstain_reason"]


def test_isolated_failing_service_blames_itself(fresh):
    fresh.query("CREATE (:Host {id:'h'})<-[:RUNS_ON]-(s:Service {id:'cron', name:'cron', kind:'service', "
                "anomaly: 0.9, onset: 100, degraded: true})")
    fresh.query("MATCH (s:Service {id:'cron'}) CREATE (:Incident {id:'i1', status:'OPEN'})-[:REPORTED_ON]->(s)")
    r = CausalFrontierRCA(fresh).investigate("i1")
    assert r["candidates"][0].id == "cron"


def test_cycles_terminate_quickly(fresh):
    q = ("UNWIND range(0, 39) AS i CREATE (:Service {id: 's' + toString(i), name: 's', kind: 'service', "
         "anomaly: 0.8, onset: i, degraded: true})")
    fresh.query(q)
    fresh.query("MATCH (a:Service), (b:Service) WHERE a.id <> b.id CREATE (a)-[:CALLS {sync: true, rps: 1}]->(b)")
    fresh.query("MATCH (s:Service {id:'s0'}) CREATE (:Incident {id:'cyc', status:'OPEN'})-[:REPORTED_ON]->(s)")
    t0 = time.perf_counter()
    r = CausalFrontierRCA(fresh, max_depth=8).investigate("cyc")
    assert (time.perf_counter() - t0) < 5.0  # complete graph K40 with cycles everywhere
    assert len(r["subgraph"].nodes) == 40


def test_missing_telemetry_is_traversed_and_reported(fresh, otel):
    for seed in range(200):
        sc = generate(otel, seed, "deploy_bug", p_missing_telemetry=1.0)
        if any(s["anomaly"] is None for s in sc.services):
            break
    load_scenario(fresh, sc)
    r = CausalFrontierRCA(fresh).investigate(sc.incident["id"])
    missing = {s["id"] for s in sc.services if s["anomaly"] is None}
    assert set(r["subgraph"].blind_spots) <= missing
    assert all(c.id not in missing for c in r["candidates"]), "unknown nodes are never blamed"


def test_unknown_incident_raises(fresh):
    with pytest.raises(KeyError):
        CausalFrontierRCA(fresh).investigate("nope")


@pytest.mark.parametrize("seed", range(20))
def test_single_varlength_query_matches_bfs(fresh, otel, seed):
    """The benchmark's alternative (one variable-length Cypher query) returns the same
    node set as the hop-by-hop BFS before unknown-leaf pruning."""
    sc = generate(otel, seed)
    load_scenario(fresh, sc)
    rca = CausalFrontierRCA(fresh)
    sub = rca.extract_subgraph(sc.incident["service"])
    assert rca.extract_ids_pathenum(sc.incident["service"]) == set(sub.depth)


def test_name_resolution_beyond_resultset_cap(fresh):
    """Regression: resolution used to load all nodes and FalkorDB truncates results at
    RESULTSET_SIZE (10,000 by default), so services beyond it were reported unknown."""
    from synapseguard.engine.tools import Tools, existing_entities
    fresh.query("UNWIND range(0, 10499) AS i CREATE (:Service {id: 'svc-' + toString(i), name: 'svc-' + toString(i)})")
    t = Tools(fresh)
    assert t.resolve("svc-10400")["status"] == "found"
    assert t.resolve("SVC-10400")["id"] == "svc-10400"
    assert t.resolve("svc-99999")["status"] == "unknown"
    assert "svc-10450" in existing_entities(fresh, "blame svc-10450 please")
