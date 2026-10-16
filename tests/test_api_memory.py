"""End-to-end API journey, action lifecycle and memory."""
import pytest
from fastapi.testclient import TestClient

from synapseguard.api import app as app_module
from synapseguard.data.demo import DEMO_SCENARIOS, build_demo


@pytest.fixture
def client(fresh, monkeypatch):
    monkeypatch.setattr(app_module, "store", fresh)
    monkeypatch.setattr(app_module, "agent", app_module.Investigator(fresh))
    return TestClient(app_module.app)


def test_health_and_ui(client):
    assert client.get("/health").json()["falkordb"] is True
    r = client.get("/")
    assert r.status_code == 200 and "SynapseGuard" in r.text


@pytest.mark.parametrize("name", list(DEMO_SCENARIOS))
def test_every_demo_scenario_end_to_end(client, name):
    inc = client.post(f"/api/scenarios/{name}/load").json()["incident"]
    r = client.post(f"/api/incidents/{inc}/investigate").json()
    truth = build_demo(name).truth["root_id"]
    assert r["root_cause"]["id"] == truth
    assert r["trace"] and all("error" not in s for s in r["trace"])
    flat = client.post(f"/api/incidents/{inc}/investigate?mode=flat").json()
    assert flat["mode"] == "flat" and flat["unavailable"]


def test_action_lifecycle_and_memory(client):
    inc = client.post("/api/scenarios/payment-flag/load").json()["incident"]
    r = client.post(f"/api/incidents/{inc}/investigate").json()
    act = next(a for a in r["actions"] if a["requires_approval"])
    assert client.post(f"/api/actions/{act['id']}/execute").status_code == 400  # not approved yet
    assert client.post(f"/api/actions/{act['id']}/approve", json={"by": "test"}).json()["status"] == "APPROVED"
    assert client.post(f"/api/actions/{act['id']}/approve", json={"by": "test"}).status_code == 400
    assert client.post(f"/api/actions/{act['id']}/execute").json()["status"] == "EXECUTED_SIMULATED"
    after = client.post(f"/api/incidents/{inc}/investigate").json()
    degraded_before = sum(n["degraded"] for n in r["subgraph"]["nodes"])
    degraded_after = sum(n["degraded"] for n in after["subgraph"]["nodes"])
    assert degraded_after < degraded_before

    fb = client.post(f"/api/incidents/{inc}/feedback",
                     json={"root_id": "payment", "root_label": "Service", "resolved_action_ids": [act["id"]]})
    assert fb.status_code == 200
    # a second, similar incident recalls the confirmed one
    inc2 = client.post("/api/scenarios/payment-flag/load").json()["incident"]
    assert inc2 == inc  # same scenario id is re-opened
    hist = client.get("/api/incidents").json()
    assert any(h["confirmed_root"] == "payment" for h in hist)


def test_reinvestigation_does_not_duplicate_affected_edges(client, fresh):
    inc = client.post("/api/scenarios/silent-db/load").json()["incident"]
    client.post(f"/api/incidents/{inc}/investigate")
    n1 = fresh.rows("MATCH (:Incident)-[r:AFFECTED]->() RETURN count(r)")[0][0]
    client.post(f"/api/incidents/{inc}/investigate")
    n2 = fresh.rows("MATCH (:Incident)-[r:AFFECTED]->() RETURN count(r)")[0][0]
    assert n1 == n2 > 0


def test_memory_survives_scenario_reload(client, fresh):
    a = client.post("/api/scenarios/silent-db/load").json()["incident"]
    client.post(f"/api/incidents/{a}/investigate")
    client.post(f"/api/incidents/{a}/feedback", json={"root_id": "postgresql", "root_label": "Service"})
    b = client.post("/api/scenarios/catalog-lock/load").json()["incident"]
    sim = client.get(f"/api/incidents/{b}/similar").json()
    assert sim == []  # b not investigated yet, so it has no AFFECTED set
    client.post(f"/api/incidents/{b}/investigate")
    sim = client.get(f"/api/incidents/{b}/similar").json()
    assert any(s["incident"] == a and s["confirmed_root"] == "postgresql" for s in sim)


def test_error_codes(client, monkeypatch):
    assert client.post("/api/incidents/nope/investigate").status_code == 404
    assert client.post("/api/scenarios/nope/load").status_code == 404
    assert client.post("/api/reset").status_code == 400
    assert client.post("/api/incidents/x/investigate?mode=weird").status_code == 422
    bad = {"name": "x", "services": [{"id": "a b"}], "edges": [], "hosts": [], "incident": {"id": "i", "service": "a"}}
    assert client.post("/api/ingest", json=bad).status_code == 422
    monkeypatch.setenv("SG_API_KEY", "s3cret")
    assert client.post("/api/scenarios/silent-db/load").status_code == 401
    assert client.post("/api/scenarios/silent-db/load", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_memory_queries_survive_legacy_shaped_data(fresh):
    """Regression: on a graph with duplicate-id incidents and no Investigation label or
    CONFIRMED_ROOT_CAUSE type yet, the old history query panicked FalkorDB 6.0.2."""
    from synapseguard.engine import recall as M
    fresh.query("UNWIND range(1, 6) AS k CREATE (:Incident {id: 'inc-dup', title: 't', timestamp: toString(k)})")
    assert len(M.history(fresh)) == 6
    assert M.similar_incidents(fresh, "inc-dup", "x") == []
    assert fresh.ping()


def test_tool_catalogue_endpoints(client):
    client.post("/api/scenarios/payment-flag/load")
    assert "blast_radius" in client.get("/api/tools").json()
    shallow = client.post("/api/tools/blast_radius", json={"service": "payment", "depth": 1}).json()
    deep = client.post("/api/tools/blast_radius", json={"service": "payment", "depth": 4}).json()
    assert shallow["outcome"] == deep["outcome"] == "answered_with_path"
    assert len(deep["paths"]) > len(shallow["paths"])
    assert all(p["via"][-1] == "payment" for p in deep["paths"])
    assert client.post("/api/tools/blast_radius", json={"service": "payments-gateway"}).json()["outcome"] == "unknown_entity"
    assert client.post("/api/tools/blast_radius", json={"service": "frontend-proxy"}).json()["outcome"] == "not_in_graph"
    assert client.post("/api/tools/blast_radius", json={"service": "Postgres"}).json()["resolved"]["id"] == "postgresql"
    page = client.post("/api/tools/who_to_page", json={"service": "payment"}).json()
    assert page["people"][0]["role"] == "on-call owner" and page["people"][0]["path"][-1] == "payment"
    assert client.post("/api/tools/path_between", json={"source": "email", "target": "quote"}).json()["outcome"] == "not_in_graph"
    assert client.post("/api/tools/nope", json={}).status_code == 404
    assert client.post("/api/tools/blast_radius", json={"service": "x", "cypher": "MATCH (n) DELETE n"}).status_code == 422
    assert client.post("/api/tools/blast_radius", json={}).status_code == 422


def test_investigation_includes_who_to_page(client):
    inc = client.post("/api/scenarios/payment-flag/load").json()["incident"]
    r = client.post(f"/api/incidents/{inc}/investigate").json()
    assert r["who_to_page"]["people"][0]["name"] == "Alice Moreau"
    assert any(a["kind"] == "page" for a in r["actions"])


def test_low_confidence_answers_ask_for_verification(client):
    """silent-db is answered at ~71% relative confidence: the agent must flag the runner-up."""
    inc = client.post("/api/scenarios/silent-db/load").json()["incident"]
    r = client.post(f"/api/incidents/{inc}/investigate").json()
    assert r["root_cause"]["confidence"] < 0.9
    assert any(a["kind"] == "advisory" and "Low confidence" in a["summary"] for a in r["actions"])
