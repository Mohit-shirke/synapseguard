# MCP: tool selection over the graph

SynapseGuard exposes a **fixed catalogue of graph tools** over MCP
(`synapseguard/mcp_server.py`, stdio transport, official `mcp` Python SDK 2.3.0).
A person wrote every Cypher query; a model only **chooses a tool and fills typed
arguments**. This follows the organisers' guidance ("the agent never writes a
query") in their Graph Hacks article *How to Build an Agent That Reasons Over a Graph*.

Every name argument is **resolved against the graph first** (exact id, display
name, normalised form, unique substring: `"Postgres"` → `postgresql`). Every
result carries one of four outcomes, plus the graph paths behind it:

| Outcome | Meaning |
|---|---|
| `answered_with_path` | the graph held the relationships; hop paths are returned |
| `answered_from_memory` | past investigations or human confirmations answer it |
| `not_in_graph` | the entity exists but nothing connects it as asked ("no dependency modelled" is not "nothing breaks") |
| `unknown_entity` | the name does not resolve; nothing else runs; suggestions given |

## Tools

| Tool | FalkorDB feature | Answers |
|---|---|---|
| `list_incidents` | Cypher | what is open, what was confirmed |
| `investigate_incident` | BFS hops, `algo.maxFlow`, memory graph | ranked root cause + evidence, blast radius, breakers, who to page, actions (default: the open incident) |
| `blast_radius(service, depth)` | index-backed BFS | what breaks if X goes down, with every hop path |
| `who_to_page(service)` | `ON_CALL`, `OWNS`, `AUTHORED`, `WORKED_ON` | on-call owner, change author, people with history, each with its path |
| `recent_changes(service, hours, depth)` | `CHANGED` + downstream BFS | what changed on X or anything it calls |
| `path_between(source, target)` | `algo.SPpaths` (sync calls weigh 1, async 3) | why two services are connected |
| `load_bearing(limit)` | `algo.pageRank` | the services most others depend on |
| `recall(service)` | memory graph | past confirmed root causes and impact |
| `record_root_cause_feedback` | write | **off** unless `SG_MCP_ALLOW_FEEDBACK=1` |
| `run_readonly_cypher` | `GRAPH.RO_QUERY` | **not exposed** unless `SG_MCP_ALLOW_CYPHER=1` |

There is **no tool to approve or execute an action**.

## The built-in LLM agent (local, no cloud)

`synapseguard/engine/llm_agent.py` connects to this MCP server as an MCP client
(in-memory transport), gives the tool list to a local Ollama model
(`llama3.1:8b`), executes the tools it picks, and accepts its final sentence only
if every service, host, person or change it names came back from a tool.
Otherwise the answer is assembled from the tool outputs. In the UI: **Ask the graph → Ask LLM**.
API: `POST /api/ask {"question": "..."}`.

Measured on 12 questions (`bench/eval_llm_agent.py`, CPU only): see
`bench/results/llm_agent.json` and the README.

## Configure Claude Desktop

```json
{
  "mcpServers": {
    "synapseguard": {
      "command": "python",
      "args": ["-m", "synapseguard.mcp_server"],
      "cwd": "X:/path/to/repo",
      "env": {"PYTHONPATH": "X:/path/to/repo", "FALKORDB_HOST": "localhost", "SG_GRAPH": "sg_otel_demo"}
    }
  }
}
```

## Configure Claude Code

```bash
claude mcp add synapseguard -e PYTHONPATH=$PWD -e SG_GRAPH=sg_otel_demo -- python -m synapseguard.mcp_server
```

## Verify

With FalkorDB running and a scenario loaded:

```bash
python -m scripts.mcp_smoke
```
