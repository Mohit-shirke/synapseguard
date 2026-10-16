# Competition: verified rules and compliance

Source pages fetched 2026-10-15 (local curl):
<https://www.wemakedevs.org/hackathons/falkordb>,
<https://www.wemakedevs.org/hackathons/falkordb/rules>,
<https://www.wemakedevs.org/hackathons/falkordb/schedule>,
<https://www.wemakedevs.org/hackathons/falkordb/resources>.
The rules page notes that rules "may be updated before the hackathon begins".
Re-check before submitting.

**Event:** Graph Hacks: Context for AI Agents, by WeMakeDevs in collaboration with FalkorDB.
**Window:** October 15, 12:01 AM IST to **October 18, 11:59 PM IST** (2026). Submissions close at the end.
**Entry:** individual, team **Night Hunter** (Mohit Shirke). Rule 1 allows solo or teams of up to 4.
**Track entered:** **Track 01, Agents That Act on Connected Data** only (see "Track decision").
**Prizes (Rule 14):** iPhone 18 Pro (Track 01), PS5 with GTA VI (Track 02), M6 Mac Mini (Track 03);
side prizes: AirPods 5 for the best blog post, t-shirts for the top 50.
**Judging criteria:** **not published.** The Track 01 "key focus" list is used as the proxy:
multi-hop reasoning, graph queries and algorithms as agent tools, tool selection through MCP,
actions based on what the graph shows, explainable results.

## Track decision

Rule 5 says "You may enter any or all of them". The organisers' article (WeMakeDevs blog,
*How to Build an Agent That Reasons Over a Graph*, 2026-10-15, linked from the Resources
page on 2026-10-16) says "You enter one track, and one submission is judged in it."
Entering **only Track 01** is compliant under both readings. Track 03 (Company Brain)
asks for knowledge from documents, tickets and chats; SynapseGuard's people layer
(owners, on-call, authors) is a slice of that, not a company brain, so we do not enter it.

## Alignment with the organisers' guidance (same article)

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

## Compliance matrix

| Requirement | Official text (abridged) | Status | Evidence |
|---|---|---|---|
| R1 team size | solo or up to 4 | ✅ solo | — |
| R2 FalkorDB primary, central | "must support a central part of what the product does" | ✅ | every investigation step queries FalkorDB; `algo.maxFlow`; memory graph |
| R3 graph does real work | "if the product still works once FalkorDB is taken out, the graph is decoration" | ✅ | graph-removed mode loses path, pruning, blast radius, breakers; synthetic top-1 0.952 vs 0.816 flat. **Caveat:** on real metrics-only data ranking is on par with a no-graph baseline (docs/evaluation.md) |
| R4 how FalkorDB runs | Docker, self-hosted or Cloud | ✅ | `docker-compose.yml` pins `falkordb/falkordb:6.0.2`; Cloud via `FALKORDB_USERNAME/PASSWORD` (untested) |
| R5 name every track | — | ⏳ you | name Track 01 in the submission form |
| R6 allowed data | public, synthetic or own | ✅ | synthetic scenarios; topology from the Apache-2.0 OTel Demo; RCAEval (CC-BY-4.0) downloaded for evaluation, not committed |
| R7 window | Oct 15 00:01 – Oct 18 23:59 IST | ✅ | first commit 2026-10-15 00:47 IST |
| R8 main work after start | "main coding and design work must begin after the hackathon starts" | ✅ | git history |
| R9 libraries allowed | frameworks, OSS, public datasets | ✅ | THIRD_PARTY_NOTICES.md |
| R10 public repo | — | ⏳ you | push and make public |
| R10 README: setup + graph model | — | ✅ | README.md, docs/graph-model.md |
| R10 Cypher / algorithms | — | ✅ | docs/graph-model.md, docs/algorithm.md |
| R10 demo video | — | ⏳ you | script: docs/demo-script.md |
| R10 deployment or local setup | "live deployment preferred" | ✅ local; ⏳ live deployment optional | README quickstart |
| R10 track named | — | ⏳ you | form + README |
| R11 AI assistance disclosed | — | ✅ | README "AI assistance" section |
| R12 explain the code | must explain model, queries, architecture | ⏳ **you** | docs/walkthrough.md written to help |
| R13 not entirely AI-generated | meaningful contribution and understanding | ⚠ **risk** | see note below |
| R16–18 conduct | — | ✅ | — |

**Note on R12/R13.** This codebase was produced with heavy AI assistance (see the
README disclosure). Judges may ask you to explain any query or design decision.
Before submitting, read docs/walkthrough.md, run the tests and the demo yourself,
and make at least the decisions in "Things only you should decide" your own.
