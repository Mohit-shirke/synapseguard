"""Security and failure-mode tests."""
import asyncio
import copy

import pytest

from synapseguard.core.config import Settings
from synapseguard.core.graph import GraphStore, GraphUnavailable
from synapseguard.data.loader import ScenarioValidationError, load_scenario, validate
from synapseguard.data.scenarios import generate
from synapseguard.engine.narrator import grounding_violations, narrate
from synapseguard.engine.rca import CausalFrontierRCA


@pytest.mark.parametrize("q", [
    "CREATE (:Pwned)",
    "MATCH (n) DETACH DELETE n",
    "MATCH (s:Service) SET s.anomaly = 0",
    "MERGE (x:Service {id: 'evil'})",
])
def test_readonly_query_rejects_writes(loaded, q):
    store, _ = loaded
    before = store.rows("MATCH (n) RETURN count(n)")[0][0]
    with pytest.raises(Exception):
        store.ro_query(q)
    assert store.rows("MATCH (n) RETURN count(n)")[0][0] == before


def test_mcp_readonly_tool_refuses_writes(loaded, monkeypatch):
    store, _ = loaded
    from synapseguard import mcp_server
    monkeypatch.setattr(mcp_server, "_store", store)
    out = mcp_server.run_readonly_cypher("MATCH (n) DETACH DELETE n")
    assert "error" in out
    ok = mcp_server.run_readonly_cypher("MATCH (s:Service) RETURN s.id LIMIT $n", {"n": 3})
    assert len(ok["rows"]) == 3


def test_mcp_lists_tools_and_never_exposes_approval():
    from synapseguard import mcp_server
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert {"investigate_incident", "blast_radius", "who_to_page", "recent_changes", "path_between",
            "load_bearing", "recall", "list_incidents"} <= names
    assert "run_readonly_cypher" not in names, "free-form Cypher must be opt-in (SG_MCP_ALLOW_CYPHER=1)"
    assert not any("approve" in n or "execute" in n for n in names)


def test_injection_strings_are_stored_as_data(fresh, otel):
    sc = generate(otel, 2)
    payload = "'}) MATCH (n) DETACH DELETE n //"
    sc.changes.append({"id": "chg-evil", "kind": "deploy", "target": sc.incident["service"],
                       "target_label": "Service", "at": 0, "summary": payload, "author": payload})
    load_scenario(fresh, sc)
    assert fresh.rows("MATCH (c:Change {id: 'chg-evil'}) RETURN c.summary")[0][0] == payload
    assert fresh.rows("MATCH (s:Service) RETURN count(s)")[0][0] == len(sc.services)


@pytest.mark.parametrize("mutate", [
    lambda sc: sc.services.append(dict(sc.services[0])),                      # duplicate id
    lambda sc: sc.services[0].__setitem__("id", "bad id with spaces"),        # invalid id
    lambda sc: sc.edges.append({"src": "ghost", "dst": sc.services[0]["id"], "protocol": "http", "sync": True,
                                "rps": 1, "has_fallback": False}),            # dangling edge
    lambda sc: sc.services[1].__setitem__("error_rate", 7.0),                 # out of range
    lambda sc: sc.incident.__setitem__("service", "nowhere"),                 # bad incident
])
def test_invalid_scenarios_rejected_before_any_write(fresh, otel, mutate):
    sc = copy.deepcopy(generate(otel, 4))
    mutate(sc)
    with pytest.raises(ScenarioValidationError):
        validate(sc)
    with pytest.raises(ScenarioValidationError):
        load_scenario(fresh, sc)
    assert fresh.rows("MATCH (n) RETURN count(n)")[0][0] == 0


def test_database_unavailable_raises_clean_error():
    s = GraphStore("x", settings=Settings(falkordb_host="127.0.0.1", falkordb_port=1))
    assert s.ping() is False
    with pytest.raises((GraphUnavailable, Exception)):
        s.query("RETURN 1")


def test_llm_unavailable_falls_back_to_template(loaded):
    store, sc = loaded
    r = CausalFrontierRCA(store).investigate(sc.incident["id"])
    top = r["candidates"][0]
    s = Settings(llm_enabled=True, ollama_url="http://127.0.0.1:1", llm_timeout_s=1)
    out = narrate(r["incident"], top, None, None, {top.id}, settings=s)
    assert out["source"] == "template" and out["violations"][0].startswith("llm_unavailable")
    assert top.id in out["text"]


def test_grounding_check_catches_invented_entities():
    universe = {"payment", "checkout", "frontend", "cart", "chg-1"}
    allowed = {"payment", "checkout"}
    assert grounding_violations("payment broke checkout", allowed, universe, "payment") == []
    v = grounding_violations("cart broke checkout after chg-1", allowed, universe, "payment")
    assert "cart" in v and "chg-1" in v and any("missing root cause" in x for x in v)
    # substring of a longer id is not a mention
    assert grounding_violations("payment-gateway", allowed, universe | {"payment-gateway"}, "payment")


def test_llm_grounding_is_case_insensitive_but_catches_inventions():
    from synapseguard.engine.llm_agent import check_grounding
    universe = {"payment", "Payment", "checkout", "Checkout", "cart", "Alice Moreau"}
    grounded = {"payment", "checkout", "Alice Moreau"}
    assert check_grounding("Payment breaks Checkout; wake Alice Moreau.", grounded, universe) == []
    assert check_grounding("Payment breaks cart.", grounded, universe) == ["cart"]


def test_llm_agent_degrades_to_tool_answer_when_model_unreachable(loaded, monkeypatch):
    """No model reachable: the agent returns a graceful error instead of raising."""
    from synapseguard.engine.llm_agent import ask
    store, _ = loaded
    r = ask("What breaks if payment goes down?", store,
            Settings(llm_enabled=True, ollama_url="http://127.0.0.1:1", llm_timeout_s=1))
    assert r["source"] in ("error", "tool outputs") and r["violations"]


def test_compact_tool_payload_is_bounded():
    from synapseguard.engine.llm_agent import compact
    big = {"outcome": "answered_with_path", "answer": "x", "paths": [{"service": f"s{i}", "via": ["a"] * 50,
                                                                        "junk": "y" * 500} for i in range(100)]}
    out = compact(big)
    assert len(out) <= 2000 and '"junk"' not in out and '"outcome"' in out
