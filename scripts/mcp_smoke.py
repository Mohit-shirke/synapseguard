"""End-to-end MCP check over stdio, as an MCP client (e.g. Claude Desktop) would use it.

    python -m scripts.mcp_smoke

Spawns ``python -m synapseguard.mcp_server`` as a subprocess, lists its tools,
investigates the open incident, compares blast radius at depth 1 and 4, and
checks that an unknown name is refused and that no Cypher or approval tool is exposed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import Client, StdioServerParameters


def data(result):
    """Prefer structured output; fall back to parsing each text block."""
    sc = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    parts = [json.loads(c.text) for c in result.content if getattr(c, "text", None)]
    return parts[0] if len(parts) == 1 else parts


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "synapseguard.mcp_server"],
                                   env={**os.environ, "PYTHONPATH": os.getcwd()})
    async with Client(params) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools.tools)
        print("tools:", names)
        incidents = data(await client.call_tool("list_incidents", {"limit": 5}))["incidents"]
        open_inc = next((i for i in incidents if i["status"] == "OPEN"), None)
        if open_inc is None:
            print("no OPEN incident; load a scenario first (UI or POST /api/scenarios/<name>/load)")
            return 1
        inv = data(await client.call_tool("investigate_incident", {"incident_id": open_inc["id"]}))
        top = inv["candidates"][0]
        print(f"investigate_incident({open_inc['id']}): top={top['id']} confidence={top['confidence']} "
              f"actions={[a['kind'] for a in inv['actions']]}")
        d1 = data(await client.call_tool("blast_radius", {"service": top["id"], "depth": 1}))
        d4 = data(await client.call_tool("blast_radius", {"service": top["id"], "depth": 4}))
        print(f"blast_radius({top['id']}): depth 1 -> {len(d1.get('paths', []))}, depth 4 -> {len(d4.get('paths', []))}")
        unk = data(await client.call_tool("blast_radius", {"service": "payments-gateway"}))
        print("unknown name ->", unk["outcome"], "|", unk["answer"])
        ok = ("run_readonly_cypher" not in names and not any("approve" in n for n in names)
              and unk["outcome"] == "unknown_entity" and len(d4.get("paths", [])) >= len(d1.get("paths", [])))
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
