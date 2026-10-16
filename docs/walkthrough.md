# Walkthrough for the author

Rules 12–13 say you must be able to explain the graph model, the queries, the
architecture and the decisions. This page is a 30-minute guided tour. Do each
"try it" step yourself.

## 1. The idea in one paragraph

When a user-facing service fails, the cause is usually somewhere it calls,
directly or several hops away. SynapseGuard keeps the service call graph,
telemetry, host placement and change log in FalkorDB. From the service where
the incident was reported, it follows synchronous calls only into unhealthy
services. That gives the "anomalous subgraph": the only places the fault can
be. It then scores each candidate on evidence: does nothing below it explain it,
how much does it explain, did it break first, did a change land just before,
do several broken services share a saturated host. It explains the answer
with graph paths, computes who else is affected, and uses max-flow/min-cut to
suggest the cheapest circuit breakers. A human approves actions; confirmed root
causes become memory.

## 2. Files to read, in order

1. `synapseguard/data/topology.py`: the OpenTelemetry Demo call graph (who calls whom).
2. `synapseguard/data/scenarios.py`: how a fault spreads to callers, and the distractors.
3. `synapseguard/data/loader.py`: how rows become nodes and edges (`UNWIND` batches) and the `cut_cost` query.
4. `synapseguard/engine/rca.py`: **the core**. Read `extract_subgraph`, `features`, `rank`, `explain`.
5. `synapseguard/engine/containment.py`: blast radius and `algo.maxFlow` → min cut.
6. `synapseguard/engine/investigator.py`: the agent: the order of tool calls and the trace.
7. `synapseguard/engine/actions.py`, `recall.py`: approval lifecycle and memory.
8. `synapseguard/data/org.py`: teams, people, on-call rotations, commit history (the "who to page" data).
9. `synapseguard/engine/tools.py`: **the fixed tool catalogue**: name resolution, four outcomes, paths.
10. `synapseguard/mcp_server.py`: the catalogue as MCP tools; free-form Cypher is opt-in.
11. `synapseguard/engine/llm_agent.py`: a local LLM picks tools over MCP; its sentence is grounding-checked.

## 3. Try it

```bash
docker compose up -d falkordb
pip install -r requirements-dev.txt
uvicorn synapseguard.api.app:app --port 8000      # open http://localhost:8000
```

* Load **payment-flag**, investigate. Hover the timeline: Payment degraded first,
  a feature flag landed 1m19s before. Compare "graph removed": it blames a decoy.
* Approve the rollback, execute it, investigate again: what is still degraded and why?
* Click the Ask chips **depth 1** then **depth 4**: count the services, read the 4-hop path.
  Click **Unknown: payments-gateway**. Type "Postgres is down, who do I wake?" and press **Ask LLM**.
* Load **quote-no-breaker**: why does the breaker action say "high risk"?
  (Answer: Quote's callers use plain HTTP clients without breakers, so the cheapest cut is at the proxy.)
* Open FalkorDB Browser at <http://localhost:3001> and run the queries in `docs/graph-model.md`.

## 4. Questions a judge may ask, with short answers

* **Why is the graph necessary?** Rows alone can't tell which degraded services can
  reach the symptom, can't produce the causal path, the blast radius or a
  breaker plan. Measured: 0.952 vs 0.816 top-1 on our synthetic benchmark.
* **Does it work on real data?** On RCAEval real fault injection (metrics only),
  ranking is on par with a strong no-graph scorer: better on RE1, slightly worse on
  held-out RE2. We report that openly; the advantage needs change logs and placement data.
* **Why BFS per hop instead of one variable-length query?** Bounded worst case. One
  query is faster on small graphs (measured) but enumerates paths.
* **Why `WITH` between MATCH clauses?** We found a FalkorDB 6.0.2 bug returning
  empty results without it (`docs/known-issues.md`).
* **What does the LLM do?** Two optional roles: (1) reword the investigation summary, (2) answer free-text
  questions by **choosing tools over MCP**. It never writes Cypher, every name it passes is resolved
  against the graph, and its sentence is rejected if it names anything the tools did not return.
  It cannot approve actions. Measured: `bench/results/llm_agent.json`.
* **Why no free-form Cypher for the model?** Generated queries can be subtly wrong or expensive and leave
  nothing to audit. A fixed catalogue of human-written, parameterised queries is checkable; it is also the
  organisers' own recommendation. Free-form read-only Cypher exists only behind `SG_MCP_ALLOW_CYPHER=1`.
* **What's the difference between not_in_graph and unknown_entity?** Unknown: the name doesn't resolve,
  nothing runs. Not in the graph: it exists but nothing connects it, which means "not modelled", not "safe".
* **Who to page: where does that come from?** `Person-[:ON_CALL {from,to}]->Team-[:OWNS]->Service` at the
  incident time, plus `Person-[:AUTHORED]->Change-[:CHANGED]->Service` within 2 h. Data is synthetic.
* **What is `cut_cost`?** The healthy traffic lost by opening a breaker on that call;
  uncuttable if the caller has no breaker. Max-flow = min-cut gives the cheapest isolation.
* **How did you pick weights?** Coordinate ascent on validation seeds; reported on separate test seeds.

## 5. Things only you should decide

* Confirm Track 01 only (see docs/competition.md, "Track decision").
* Whether to delete the legacy v1 files (listed in `docs/submission-checklist.md`).
* The project name and how you present the real-world results in the video.
