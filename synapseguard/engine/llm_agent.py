"""An LLM that answers questions by selecting SynapseGuard tools over MCP.

The model never sees Cypher. It connects to the SynapseGuard MCP server as an
MCP client (in-memory transport, same protocol Claude Desktop uses), picks tools
and fills arguments; the tools resolve names against FalkorDB and return outcomes
with paths. The model then writes a short answer, which is accepted only if
every service, host, person or change it names came back from a tool. Otherwise
an answer assembled from the tool outputs is returned instead.

Local model via Ollama (default llama3.1:8b); no data leaves the machine.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx
from mcp import Client

from synapseguard.core.config import Settings, get_settings
from synapseguard.core.graph import GraphStore

MAX_ROUNDS = 4
SYSTEM = (
    "You are SynapseGuard, an on-call assistant. Answer ONLY from tool results. Call tools to get facts; "
    "do not guess service or people names. If a tool returns outcome not_in_graph or unknown_entity, say so "
    "plainly. Final answer: at most 3 sentences, name the services/people/changes the tools returned."
)


def _tool_specs(tools) -> list[dict]:
    # human-only tools are never offered to the model
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description or "",
        "parameters": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object"}}}
        for t in tools if t.name != "record_root_cause_feedback"]


def _result_json(result) -> Any:
    sc = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    texts = [getattr(c, "text", "") for c in result.content]
    try:
        return json.loads("".join(texts))
    except json.JSONDecodeError:
        return {"text": "".join(texts)}


def compact(data: Any, limit: int = 2000) -> str:
    """What the model sees from a tool: outcome, answer and a few trimmed items (the UI gets everything).
    Full results made later rounds exceed the CPU timeout."""
    if not isinstance(data, dict):
        return json.dumps(data, default=str)[:limit]
    keep: dict[str, Any] = {k: data[k] for k in ("outcome", "answer", "error") if k in data}
    for k in ("paths", "people", "changes", "services", "memories", "candidates", "incidents", "path"):
        v = data.get(k)
        if isinstance(v, list) and v:
            keep[k] = [{kk: vv for kk, vv in x.items() if kk in ("service", "via", "hops", "name", "role", "reason",
                                                                  "change", "summary", "author", "target", "id",
                                                                  "confidence", "path", "status", "title", "pagerank")}
                       if isinstance(x, dict) else x for x in v[:5]]
    if isinstance(data.get("who_to_page"), dict):
        keep["who_to_page"] = data["who_to_page"].get("answer")
    return json.dumps(keep, default=str)[:limit]


def _identifiers(obj: Any, out: set[str]) -> set[str]:
    """Every string value in tool results (ids, names, paths) counts as grounded."""
    if isinstance(obj, dict):
        for v in obj.values():
            _identifiers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _identifiers(v, out)
    elif isinstance(obj, str):
        out.add(obj)
        out.update(re.findall(r"[A-Za-z0-9][A-Za-z0-9._\-]+", obj))
    return out


def universe(store: GraphStore, text: str) -> set[str]:
    """Graph entities the text mentions (bounded lookup; see tools.existing_entities)."""
    from synapseguard.engine.tools import existing_entities
    return existing_entities(store, text)


def check_grounding(text: str, grounded: set[str], universe_names: set[str]) -> list[str]:
    """Names from the graph that the text mentions but no tool returned."""
    low = text.lower()
    ok = {g.lower() for g in grounded}  # case-insensitive: "Payment" (display name) == "payment" (id)
    bad = []
    for name in universe_names:
        if len(name) < 3:
            continue
        if re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", low) and name.lower() not in ok:
            bad.append(name)
    return sorted(bad)


async def _ask(question: str, store: GraphStore, s: Settings) -> dict[str, Any]:
    from synapseguard import mcp_server
    mcp_server._store = store
    steps: list[dict] = []
    grounded: set[str] = set()
    results: list[dict] = []
    llm_ms = 0.0
    llm_error = None
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    async with Client(mcp_server.server) as client:
        specs = _tool_specs((await client.list_tools()).tools)
        final = ""
        async with httpx.AsyncClient(timeout=s.llm_timeout_s) as http:
            for _ in range(MAX_ROUNDS):
                t0 = time.perf_counter()
                try:
                    r = await http.post(f"{s.ollama_url}/api/chat", json={
                        "model": s.ollama_model, "messages": msgs, "tools": specs, "stream": False,
                        "options": {"temperature": 0, "seed": 7}})
                    r.raise_for_status()
                except httpx.HTTPError as exc:  # timeout or model error: keep the tool results we have
                    llm_error = f"llm_unavailable: {type(exc).__name__}"
                    llm_ms += (time.perf_counter() - t0) * 1000
                    break
                llm_ms += (time.perf_counter() - t0) * 1000
                msg = r.json()["message"]
                calls = msg.get("tool_calls") or []
                msgs.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
                if not calls:
                    final = (msg.get("content") or "").strip()
                    break
                for call in calls:
                    fn = call["function"]
                    name, args = fn["name"], fn.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    t1 = time.perf_counter()
                    try:
                        data = _result_json(await client.call_tool(name, args))
                    except Exception as exc:  # noqa: BLE001 - a bad call is reported back to the model
                        data = {"outcome": "error", "error": str(exc)}
                    steps.append({"tool": name, "args": args, "outcome": data.get("outcome") if isinstance(data, dict)
                                  else None, "ms": round((time.perf_counter() - t1) * 1000, 2)})
                    results.append(data if isinstance(data, dict) else {"result": data})
                    _identifiers(data, grounded)
                    msgs.append({"role": "tool", "content": compact(data)})
    fallback = " ".join(str(d.get("answer", "")) for d in results if isinstance(d, dict) and d.get("answer")) \
        or "No tool returned an answer."
    violations = check_grounding(final, grounded, universe(store, final)) if final else [llm_error or "empty answer"]
    if final and not steps:
        violations = ["answered without calling any tool"]
    accepted = bool(final) and bool(steps) and not violations
    source = (f"llm:{s.ollama_model} (grounding-verified)" if accepted
              else "tool outputs (LLM answer rejected)" if final else "tool outputs")
    return {"question": question, "answer": final if accepted else fallback, "source": source,
            "llm_answer": final, "violations": violations, "steps": steps,
            "outcomes": [st["outcome"] for st in steps], "llm_ms": round(llm_ms, 1)}


def ask(question: str, store: GraphStore, settings: Settings | None = None) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise ValueError("question must be 1-500 characters")
    s = settings or get_settings()
    try:
        return asyncio.run(_ask(question.strip(), store, s))
    except (httpx.HTTPError, ExceptionGroup) as exc:  # anyio task groups wrap errors in ExceptionGroup
        return {"question": question, "answer": "The local model or a tool failed; use the tool buttons instead.",
                "source": "error", "violations": [f"agent_error: {type(exc).__name__}"], "steps": [],
                "outcomes": [], "llm_ms": None}
