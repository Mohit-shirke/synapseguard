# Skeptical self-review (internal, not official scoring)

The organisers have **not published** a judging rubric or weights. This is our
own engineering scorecard, written as a skeptical judge would read the project.
Scale: 0–3 missing or broken · 4–6 works with notable gaps · 7–8 solid, minor
gaps · 9–10 exceptional and independently verified.

| Dimension | Score | Evidence | Main weakness |
|---|---:|---|---|
| Problem relevance | 8 | Incident RCA matches the organisers' own example "What breaks if this service goes down?" | Demo data is synthetic |
| Meaningful FalkorDB use | 9 | Pruned traversal, `algo.maxFlow`, `algo.SPpaths`, `algo.pageRank`, Cypher cost model, people/on-call graph, memory graph; graph-removed mode | At incident sizes networkx could compute the same traversals (we say so) |
| Technical depth | 8 | Evidence scoring with ablations, residual min-cut with max-flow = min-cut assertion, fixed MCP tool catalogue with name resolution and outcomes, LLM tool selection measured, two FalkorDB bugs isolated | Not a novel algorithm |
| Originality | 6 | Root cause (not just impact) with evidence model, breaker-constrained min-cut, real-world evaluation | The organisers' own demo covers impact + on-call + recent changes; ours must be shown to go beyond it |
| Correctness | 8 | 138 tests incl. Cypher-vs-reference equivalence on random graphs, max-flow invariants | Synthetic fault model shares assumptions with the scorer |
| Real-world validity | 6 | 645 RCAEval real fault-injection cases on 3 systems, held-out RE2, official BARO run, Train Ticket graph learned from real traces | Ranking only on par with a no-graph scorer on real metrics-only data |
| Reliability | 7 | DB-down 503, timeouts, validation before writes, LLM fallback, Docker healthchecks | No retry/backoff on transient DB errors; snapshot telemetry |
| Performance | 7 | 10k-service mesh: investigation p50 in `bench/results/scale.md`; index use verified | Single local machine; no load test beyond 8 threads |
| Security | 7 | Parameterised Cypher, server-enforced read-only tool, no approval tools for LLMs, optional API key, localhost-only ports | No auth/RBAC; API key optional |
| User experience | 7 | HIG-based UI, light/dark, timeline, trace, confirm dialog, accessible states | Graph layout dense; phone widths below ~500 px unverified |
| Reproducibility | 8 | Pinned image and packages, seeded generators, raw results, CI, full `docker compose up --build` verified | RCAEval data must be downloaded (scripted) |
| Demo clarity | 7 | Script with live functionality only | Video not recorded yet |
| Documentation | 8 | README, model, Cypher, algorithm, evaluation, known issues, walkthrough | — |

## Reasons this could lose, and what we did about each

* *"Thin wrapper around an LLM."* No: the core never needs an LLM; the optional LLM only picks human-written tools over MCP and rewords, under a grounding check.
* *"Same as the organisers' Blast Radius demo."* It covers impact, on-call and changes; SynapseGuard adds root-cause search with measured accuracy, min-cut containment and an approval lifecycle (docs/competition.md).
* *"The graph is decoration."* Measured: rows-only mode loses the path, blast radius and breakers, and drops synthetic top-1 from 0.952 to 0.816.
* *"Numbers are on your own synthetic data."* True; that's why we ran RCAEval with held-out data and published a mixed result.
* *"Can others reproduce it?"* Seeds, pinned versions, scripts and raw outputs are in the repo.
* *"Is the code yours?"* Heavy AI assistance, disclosed. The author must be able to explain it (docs/walkthrough.md).
