"""SynapseGuard MCP server: a fixed catalogue of FalkorDB graph tools for any MCP client.

    python -m synapseguard.mcp_server            # stdio transport

Claude Desktop / Claude Code config example: docs/mcp.md.

Design (follows the organisers' guidance that the agent should not write Cypher):
* A person wrote every query; the model picks a tool and fills typed arguments.
* Every name is resolved against the graph first. Results carry one outcome:
  answered_with_path | answered_from_memory | not_in_graph | unknown_entity.
* No tool can approve or execute an action; a human does that in the UI/API.
* Free-form Cypher is OFF by default. With SG_MCP_ALLOW_CYPHER=1 it is exposed
  through FalkorDB GRAPH.RO_QUERY (the database refuses writes), capped and timed out.
* record_root_cause_feedback is off unless SG_MCP_ALLOW_FEEDBACK=1.
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from synapseguard.core.config import get_settings
from synapseguard.core.graph import GraphStore
from synapseguard.engine import recall as M
from synapseguard.engine.investigator import Investigator
from synapseguard.engine.tools import Tools

server = MCPServer(
    name="synapseguard",
    instructions=(
        "Incident tools over a FalkorDB graph of services (CALLS, caller -> callee), hosts, teams, people, "
        "changes and past incidents. Use list_incidents then investigate_incident for an open incident; "
        "blast_radius for 'what breaks if X goes down'; who_to_page for 'who do I wake'; recent_changes; "
        "path_between for 'why is X connected to Y'; load_bearing; recall for past findings. "
        "Every result has an outcome. Say 'not in the graph' or 'unknown' when a tool says so; never invent "
        "names. Only cite services, people and changes returned by tools. Actions need human approval."
    ),
)
_store: GraphStore | None = None
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def store() -> GraphStore:
    global _store
    if _store is None:
        _store = GraphStore()
    return _store


def tools() -> Tools:
    return Tools(store())


@server.tool(annotations=RO)
def list_incidents(limit: int = 10) -> dict:
    """Recent incidents with status and any human-confirmed root cause."""
    return tools().list_incidents(limit)


def resolve_incident(incident_id: str) -> tuple[str | None, str]:
    """Exact incident id, or the most recent OPEN incident when the id is empty/'open'/not an id."""
    if incident_id and store().rows("MATCH (i:Incident {id: $id}) RETURN i.id", {"id": incident_id}):
        return incident_id, "exact"
    rows = store().rows("MATCH (i:Incident {status: 'OPEN'}) RETURN i.id ORDER BY i.opened_at DESC LIMIT 1")
    if rows and (not incident_id or "open" in incident_id.lower() or "current" in incident_id.lower()):
        return rows[0][0], "resolved to the open incident"
    return None, "unknown"


@server.tool()
def investigate_incident(incident_id: str = "open") -> dict:
    """Full investigation of an incident (default: the currently open one): ranked root causes with graph
    evidence, blast radius, min-cut circuit breakers, who to page, proposed actions (humans approve)."""
    resolved, how = resolve_incident(incident_id)
    if resolved is None:
        return {"outcome": "unknown_entity", "answer": f"incident {incident_id!r} is not in the graph; "
                                                       "call list_incidents to see valid ids"}
    r = Investigator(store()).investigate(resolved)
    return {
        "outcome": "answered_with_path" if r["root_cause"] else "not_in_graph",
        "investigation_id": r["investigation_id"],
        "incident_resolution": how,
        "answer": (f"Root cause of {resolved}: {r['root_cause']['id']} "
                   f"({r['root_cause']['confidence']:.0%} relative confidence)") if r["root_cause"] else r["abstain_reason"],
        "incident": r["incident"],
        "abstain_reason": r["abstain_reason"],
        "candidates": [{k: c[k] for k in ("id", "label", "score", "confidence", "path", "evidence")}
                       for c in r["candidates"][:3]],
        "blast_radius": r["blast_radius"] and {k: r["blast_radius"][k] for k in
                                               ("entrypoints", "sync_count", "async_only_count", "high_criticality")},
        "breakers": r["breakers"] and {k: r["breakers"].get(k) for k in ("status", "cut", "cut_cost", "naive")},
        "who_to_page": r.get("who_to_page"),
        "actions": [{k: a[k] for k in ("id", "kind", "summary", "risk", "status", "rationale")} for a in r["actions"]],
        "similar_incidents": r["similar_incidents"],
        "trace": r["trace"],
    }


@server.tool(annotations=RO)
def blast_radius(service: str, depth: int = 3) -> dict:
    """What breaks if a service or host goes down: every transitive caller, with its hop path. depth 1-6."""
    return tools().blast_radius(service, depth)


@server.tool(annotations=RO)
def who_to_page(service: str) -> dict:
    """Who to wake for a service or host: on-call owner, recent change authors, people who worked on it."""
    return tools().who_to_page(service)


@server.tool(annotations=RO)
def recent_changes(service: str, hours: int = 2, depth: int = 2) -> dict:
    """Deploys/config/flag changes in the last `hours` on a service and on what it calls (up to `depth` hops)."""
    return tools().recent_changes(service, hours, depth)


@server.tool(annotations=RO)
def path_between(source: str, target: str) -> dict:
    """Why two services are connected: cheapest call path (FalkorDB algo.SPpaths; sync calls cost 1, async 3)."""
    return tools().path_between(source, target)


@server.tool(annotations=RO)
def load_bearing(limit: int = 5) -> dict:
    """The services most others depend on (FalkorDB algo.pageRank over CALLS)."""
    return tools().load_bearing(limit)


@server.tool(annotations=RO)
def recall(service: str) -> dict:
    """What past investigations and human confirmations established about a service or host."""
    return tools().recall(service)


@server.tool()
def record_root_cause_feedback(incident_id: str, root_id: str, root_label: str = "Service",
                               confirmed_by: str = "mcp-user") -> dict:
    """Record a human-confirmed root cause (disabled unless SG_MCP_ALLOW_FEEDBACK=1)."""
    if os.getenv("SG_MCP_ALLOW_FEEDBACK") != "1":
        return {"error": "feedback via MCP is disabled; confirm in the SynapseGuard UI"}
    return M.record_feedback(store(), incident_id, root_id, root_label, confirmed_by)


def _plain(v: Any) -> Any:
    """Make FalkorDB result values JSON-friendly."""
    if hasattr(v, "labels") and hasattr(v, "properties"):
        return {"labels": list(v.labels), **v.properties}
    if hasattr(v, "relation") and hasattr(v, "properties"):
        return {"type": v.relation, **v.properties}
    if hasattr(v, "nodes") and callable(getattr(v, "nodes")):
        return [_plain(n) for n in v.nodes()]
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def run_readonly_cypher(query: str, params: dict | None = None) -> dict:
    """Run a read-only Cypher query (FalkorDB GRAPH.RO_QUERY rejects writes). Opt-in only."""
    if len(query) > 4000:
        return {"error": "query too long"}
    s = get_settings()
    try:
        res = store().ro_query(query, params or {}, timeout_ms=s.query_timeout_ms)
    except Exception as exc:  # noqa: BLE001 - surface the database's own message to the model
        return {"error": str(exc)}
    rows = res.result_set[: s.readonly_row_limit]
    return {"columns": [c[1] if isinstance(c, (list, tuple)) else c for c in (res.header or [])],
            "rows": _plain(rows), "truncated": len(res.result_set) > len(rows)}


if os.getenv("SG_MCP_ALLOW_CYPHER") == "1":
    server.tool(annotations=RO)(run_readonly_cypher)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
