"""SynapseGuard HTTP API and web UI.

    uvicorn synapseguard.api.app:app --port 8000      then open http://localhost:8000

If ``SG_API_KEY`` is set, every mutating endpoint requires the header
``X-API-Key: <value>``. The UI is served from the same origin, so no CORS is
enabled.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from synapseguard.core.graph import GraphStore, GraphUnavailable
from synapseguard.data.demo import DEMO_SCENARIOS, build_demo
from synapseguard.data.loader import ScenarioValidationError, load_scenario
from synapseguard.data.scenarios import Scenario
from synapseguard.engine import actions as A
from synapseguard.engine import recall as M
from synapseguard.engine.investigator import Investigator
from synapseguard.engine.tools import CATALOGUE, Tools

log = logging.getLogger("synapseguard")
WEB = Path(__file__).resolve().parent.parent / "web"
VERSION = "2.0.0"

app = FastAPI(title="SynapseGuard", version=VERSION,
              description="Graph-grounded incident root-cause and containment agent on FalkorDB")
store = GraphStore()
agent = Investigator(store)


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("SG_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


@app.exception_handler(GraphUnavailable)
def _graph_down(_, exc: GraphUnavailable):
    return JSONResponse(status_code=503, content={"detail": "FalkorDB unavailable", "error": str(exc)})


@app.exception_handler(KeyError)
def _not_found(_, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc).strip("'\"")})


@app.exception_handler(ValueError)
def _bad_request(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# --------------------------------------------------------------------- ui --
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/health")
def health():
    ok = store.ping()
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ok" if ok else "degraded", "falkordb": ok,
                                 "graph": store.graph_name, "version": VERSION})


# -------------------------------------------------------------- scenarios --
@app.get("/api/scenarios")
def scenarios():
    return [{"name": k, "title": v["title"], "story": v["story"]} for k, v in DEMO_SCENARIOS.items()]


@app.post("/api/scenarios/{name}/load", dependencies=[Depends(require_key)])
def load(name: str):
    if name not in DEMO_SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}")
    sc = build_demo(name)
    counts = load_scenario(store, sc)
    return {"scenario": name, "incident": sc.incident["id"], "loaded": counts}


class IngestBody(BaseModel):
    name: str = Field(max_length=64)
    services: list[dict] = Field(max_length=20_000)
    edges: list[dict] = Field(max_length=200_000)
    hosts: list[dict] = Field(max_length=5_000)
    changes: list[dict] = Field(default_factory=list, max_length=50_000)
    incident: dict


@app.post("/api/ingest", dependencies=[Depends(require_key)])
def ingest(body: IngestBody):
    """Load your own topology + telemetry snapshot (validated before any write)."""
    sc = Scenario(name=body.name, seed=0, description="ingested", services=body.services, edges=body.edges,
                  hosts=body.hosts, changes=body.changes, incident=body.incident)
    try:
        counts = load_scenario(store, sc)
    except ScenarioValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"incident": sc.incident["id"], "loaded": counts}


@app.post("/api/reset", dependencies=[Depends(require_key)])
def reset(confirm: bool = Query(False)):
    """Delete the whole graph, including investigation memory."""
    if not confirm:
        raise HTTPException(status_code=400, detail="pass ?confirm=true to wipe the graph and its memory")
    store.drop()
    store.ensure_indexes()
    return {"status": "wiped", "graph": store.graph_name}


# ------------------------------------------------------------------ graph --
@app.get("/api/graph")
def graph(limit: int = Query(2000, ge=1, le=5000)):
    nodes = [{"id": r[0], "label": r[1], "name": r[2], "degraded": r[3], "anomaly": r[4], "kind": r[5],
              "cpu": r[6]} for r in store.rows("""
        MATCH (n) WHERE n:Service OR n:Host
        RETURN n.id, labels(n)[0], n.name, n.degraded, n.anomaly, n.kind, n.cpu LIMIT $limit""",
                                              {"limit": limit})]
    edges = [{"src": r[0], "dst": r[1], "type": r[2], "sync": r[3], "breaker_open": r[4]} for r in store.rows("""
        MATCH (a)-[r:CALLS|RUNS_ON]->(b) RETURN a.id, b.id, type(r), r.sync, r.breaker_open LIMIT $limit""",
                                                                                           {"limit": limit * 4})]
    changes = [{"id": r[0], "kind": r[1], "summary": r[2], "at": r[3], "target": r[4]} for r in store.rows(
        "MATCH (c:Change)-[:CHANGED]->(t) RETURN c.id, c.kind, c.summary, c.at, t.id ORDER BY c.at")]
    return {"nodes": nodes, "edges": edges, "changes": changes}


@app.get("/api/incidents")
def incidents(limit: int = Query(20, ge=1, le=200)):
    return M.history(store, limit)


@app.post("/api/incidents/{incident_id}/investigate")
def investigate(incident_id: str, mode: Literal["graph", "flat"] = "graph"):
    return agent.investigate(incident_id, mode=mode)


@app.get("/api/incidents/{incident_id}/similar")
def similar(incident_id: str, top_id: str | None = None):
    return M.similar_incidents(store, incident_id, top_id)


class Feedback(BaseModel):
    root_id: str = Field(max_length=128)
    root_label: Literal["Service", "Host"]
    by: str = Field(default="operator", max_length=64)
    resolved_action_ids: list[str] = Field(default_factory=list, max_length=20)


@app.post("/api/incidents/{incident_id}/feedback", dependencies=[Depends(require_key)])
def feedback(incident_id: str, body: Feedback):
    """A human confirms the root cause; this is what memory treats as ground truth."""
    return M.record_feedback(store, incident_id, body.root_id, body.root_label, body.by, body.resolved_action_ids)


# ---------------------------------------------------------------- actions --
class Decision(BaseModel):
    by: str = Field(default="operator", max_length=64)


@app.post("/api/actions/{action_id}/approve", dependencies=[Depends(require_key)])
def approve(action_id: str, body: Decision):
    return A.decide(store, action_id, True, body.by)


@app.post("/api/actions/{action_id}/reject", dependencies=[Depends(require_key)])
def reject(action_id: str, body: Decision):
    return A.decide(store, action_id, False, body.by)


@app.post("/api/actions/{action_id}/execute", dependencies=[Depends(require_key)])
def execute(action_id: str):
    """Simulated execution against the graph (no real infrastructure)."""
    return A.execute(store, action_id)


# ----------------------------------------------------------- tool catalogue --
_TOOL_PARAMS = {name: set(spec["params"]) for name, spec in CATALOGUE.items()}


@app.get("/api/tools")
def list_tools():
    """The fixed tool catalogue the agents choose from (same tools as the MCP server)."""
    return CATALOGUE


@app.post("/api/tools/{name}")
def call_tool(name: str, args: dict | None = None):
    """Run one catalogue tool with validated arguments. Unknown tools/arguments are rejected."""
    if name not in CATALOGUE:
        raise HTTPException(status_code=404, detail=f"unknown tool {name!r}")
    args = args or {}
    extra = set(args) - _TOOL_PARAMS[name]
    if extra:
        raise HTTPException(status_code=422, detail=f"unexpected arguments: {sorted(extra)}")
    missing = set(CATALOGUE[name].get("required", [])) - set(args)
    if missing:
        raise HTTPException(status_code=422, detail=f"missing arguments: {sorted(missing)}")
    try:
        return getattr(Tools(store), name)(**args)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.post("/api/ask")
def ask_llm(body: Question):
    """A local LLM (Ollama) picks catalogue tools over MCP; its answer is grounding-checked.
    Slow on CPU (tens of seconds); the tool buttons are the fast path."""
    from synapseguard.engine.llm_agent import ask
    return ask(body.question, store)
