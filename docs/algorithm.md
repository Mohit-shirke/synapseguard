# Causal Frontier RCA: algorithm

SynapseGuard does not claim a novel algorithm. It is an engineered combination
of established ideas (anomalous-subgraph extraction, temporal precedence,
change correlation, common-cause detection, max-flow/min-cut), each evaluated
by ablation. Prior art is listed at the end.

## Problem

**Input:** a directed call graph `G = (V, E)` in FalkorDB (`caller -[:CALLS]-> callee`,
edge properties `sync`, `has_fallback`, `breaker`, `rps`), per-service telemetry
(anomaly score `a(v) ∈ [0,1]` or *unknown*, anomaly onset `t(v)`), host
placement (`RUNS_ON`), a change log (`Change -[:CHANGED]-> Service|Host`) and an
incident reported on service `s`.

**Output:** a ranked list of root-cause candidates (services or hosts), each
with a relative confidence and evidence items that cite graph elements; the
blast radius of the top candidate; and a minimum-cost set of circuit breakers
isolating entrypoints from it.

**Assumptions:** faults propagate from callee to caller over *synchronous*
calls; fallbacks dampen propagation; a degraded service is one with
`a(v) ≥ τ = 0.2`.

## Steps

```
1  A ← BFS from s over CALLS where r.sync and (a(callee) ≥ τ or a(callee) unknown), depth ≤ K
     (one Cypher query per hop, index-backed: MATCH (a) WHERE a.id IN $frontier ...)
2  drop unknown-telemetry nodes in A that reach no degraded node
3  fetch telemetry, host placement, co-located services and changes for A (3 queries)
4  for each degraded v in A:
       frontier(v) = 1 if no degraded service is reachable from v inside A
       coverage(v) = |degraded ancestors of v in A| / (|degraded(A)| - 1)
       onset(v)    = share of other degraded services in A with onset ≥ t(v)
       change(v)   = 1 - 0.5·Δ/1800 for the latest change on v with Δ = t(v) - t_change ∈ [0, 1800] s
       severity(v) = percentile rank of a(v) among degraded services in A
       exposed(v)  = 1 if v is reachable from s without crossing a fallback-protected call
       lag(v)      = 1 if v's nearest degraded callers degraded 0–300 s after v
   for each host h of A with CPU ≥ 90 %: same features over its degraded services,
       plus common(h) = min(1, (#degraded services on h - 1) / 2)
5  profile = evidence if the subgraph has a change log or host placement else severity
   score = Σ_f w_f · f   (evidence weights tuned on validation seeds; severity profile = severity only)
6  confidence = softmax(score / 0.35) over candidates
7  evidence for top-3: call path (BFS parents), blind spots, telemetry, frontier,
   onset, change, coverage; predicted recovery set
8  blast radius: BFS over CALLS reversed from the root (or all services on a root host)
9  breakers: algo.maxFlow(entrypoints → root, capacity = cut_cost) in FalkorDB,
   then min cut = edges from the residual-reachable side; assert Σ cut_cost = maxFlow
```

`cut_cost(u→v)` is computed in Cypher: `rps · (1 + #healthy callees of v)`,
×10 if `v` is healthy, and 10⁶ if the caller cannot circuit-break. The cut is
therefore placed as close to the fault as breaker capability allows.

## Complexity

`V_A, E_A` = nodes/edges of the anomalous subgraph, `d` = max out-degree,
`K` = depth bound (8), `C` = candidates (≤ V_A), `V_B, E_B` = blast-radius subgraph.

| Operation | Time | Space | Round trips |
|---|---|---|---|
| Subgraph BFS (step 1) | O(V_A · d) index-backed expansions | O(V_A + E_A) | ≤ K |
| Context fetch (step 3) | O(V_A + changes on A) | O(V_A) | 3 |
| Features (step 4) | O(C · (V_A + E_A)) closures | O(V_A²) worst case for cached closures | 0 |
| Ranking + softmax | O(C log C) | O(C) | 0 |
| Blast radius | O(V_B · d) | O(V_B) | ≤ K |
| Breakers (`algo.maxFlow`) | O(V · E²) worst case (FalkorDB docs), capacity scaling in practice | O(V + E) | 1 + 2 |
| Cut-cost refresh (load time) | O(E) | O(1) extra | 1 |

Because only the *anomalous* region is expanded, the work depends on the size
of the incident, not of the mesh: on the 10,000-service mesh the anomalous
subgraph had 8 nodes. Measured latencies are in `bench/results/scale.md`.

## Measured design choices

* **Hop-by-hop BFS vs one variable-length query.** A single
  `MATCH p=(s)-[:CALLS*0..8 {sync:true}]->(x) WHERE all(n IN nodes(p) ...)`
  query returns the same node set (checked) and is faster at these sizes
  because it needs one round trip. It enumerates paths, so its worst case is
  exponential in dense meshes. We keep BFS for its bounded worst case and report
  both in `bench/results/scale.md`. This is a trade-off, not a win.
* **Reachability pruning** improves synthetic top-1 (0.908 → 0.952) and real
  RE1 Avg@5 (see evaluation), but hurts on held-out RE2, where the root was not
  reachable through degraded calls in 4–7 % of cases.
* **Rank-based severity** replaced a raw score that saturated at 1.0 on real
  telemetry; found during the real-world evaluation.

## Prior art

| Work | Idea | Relation to SynapseGuard |
|---|---|---|
| MonitorRank (Kim et al., SIGMETRICS 2013) | Random walk on call graph | We include a personalised-PageRank baseline (0.46 top-1 on our synthetic set) |
| MicroRCA (Wu et al., NOMS 2020) | Attributed graph + personalised PageRank | Same baseline family; our scorer uses explicit features instead of a walk |
| CloudRanger, Microscope (2018) | Causal graphs from metrics | We use the known call graph instead of learning causality |
| BARO (Pham et al., FSE 2024) | Bayesian change point + robust scoring | Run with its official code as the real-world reference |
| RCAEval (Pham et al., 2025) | Public benchmark, 15 baselines | Our real-world evaluation protocol |
| Max-flow/min-cut (Ford–Fulkerson; Edmonds–Karp) | Network cuts | Used for breaker placement via FalkorDB `algo.maxFlow` |

What we did not find in this combination elsewhere (to the best of a short
search, not a systematic review): change-log and host common-cause evidence
scored on the FalkorDB-pruned subgraph, explanation items that cite graph
elements, and min-cut breaker placement under breaker-capability constraints,
all exposed to LLM agents through read-only MCP tools.
