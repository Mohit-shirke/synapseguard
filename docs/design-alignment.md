# Design alignment

How SynapseGuard maps to the guidance in the organisers' Graph Hacks article
*How to Build an Agent That Reasons Over a Graph* (WeMakeDevs, 2026-10-15).

| Their recommendation | SynapseGuard |
|---|---|
| Graph does work the agent could not do without it | anomalous-subgraph pruning, evidence paths, blast radius, max-flow breakers; graph-removed mode measured |
| Fixed tool catalogue, agent never writes Cypher | 8 human-written tools (`engine/tools.py`), MCP server; free-form Cypher off by default |
| Resolve names against the database | every name argument resolved first; "Postgres" → `postgresql` |
| Four answer shapes incl. "not in the graph" vs "unknown" | `answered_with_path`, `answered_from_memory`, `not_in_graph`, `unknown_entity` |
| Algorithms as tools (SPpaths, PageRank, max flow) | `path_between` (SPpaths), `load_bearing` (PageRank), breaker min-cut (maxFlow) |
| Memory as nodes, read before reasoning | Investigation/Action/AFFECTED/CONFIRMED_ROOT_CAUSE; `recall`, similar-incident recall |
| Return the path with every answer | every tool returns hop paths; evidence items cite graph elements |
| Check the model's sentence; fall back to rows | grounding check (case-insensitive) in narrator and LLM agent |
| Demo: depth 1 vs depth 4; ask about something not in the graph | UI chips and `scripts/mcp_smoke.py` |
| `ro_query` for anything that is not a write | all read tools; optional Cypher via `GRAPH.RO_QUERY` |

**Beyond their Blast Radius demo** (impact, on-call, recent changes): SynapseGuard finds
the **root cause** with an evidence model evaluated on 1,250 synthetic and 645 real
fault-injection cases, places **circuit breakers by min-cut**, runs a human-approved
**action lifecycle**, and documents two FalkorDB bugs with minimal reproductions.
