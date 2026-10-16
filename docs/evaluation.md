# Evaluation

Everything here is reproducible from scripts in `bench/`; raw outputs are in
`bench/results/`. Hardware: Windows 11 laptop, Intel Core (family 6 model 170),
18 logical CPUs, 16 GB RAM, no GPU; FalkorDB 6.0.2 in local Docker; Python 3.12.10.

## 1. Synthetic incidents (`bench/eval_accuracy.py`)

**Data.** Seeded scenarios from `synapseguard/data/scenarios.py` on (a) the
OpenTelemetry Demo topology and (b) generated 300-service layered meshes. Six
fault kinds: deploy bug, config change, feature flag, host saturation, silent
(no change record), retry storm. Faults propagate to callers over synchronous
calls (p=0.9; 0.36 through fallbacks) with 15–90 s per hop. Distractors: 1–4
unrelated flaky services, 2–6 benign changes (40% within ±10 min of the fault),
a decoy hot host (20%), missing telemetry on a causal node (30%). The ground truth
is never written to the graph.

**Protocol.** Weights for our scorer *and* for the strongest flat baseline are
tuned by the same coordinate ascent on validation seeds (OTel 0–299; layered
0–149) and reported on disjoint test seeds (OTel 1000–1499; layered 2000–2299).
Every scenario is loaded into FalkorDB and features are extracted with Cypher,
as in the product; a pure-Python twin traversal checks the Cypher.

**Results, OTel topology, 500 test incidents** (`accuracy_otel.md`):

| Method | Top-1 | Top-3 | MRR |
|---|---:|---:|---:|
| SynapseGuard (product default) | **0.952** | **0.994** | **0.972** |
| ablation: no reachability pruning | 0.908 | 0.988 | 0.946 |
| ablation: without `frontier` | 0.956 | 0.994 | 0.975 |
| ablation: without `coverage` | 0.950 | 0.994 | 0.971 |
| ablation: without `onset` | 0.950 | 0.992 | 0.971 |
| ablation: without `change` | 0.882 | 0.986 | 0.934 |
| ablation: without `severity` | 0.944 | 0.994 | 0.968 |
| ablation: without `common` | 0.940 | 0.994 | 0.966 |
| ablation: without `exposed` | 0.946 | 0.994 | 0.969 |
| ablation: without `lag` | 0.884 | 0.984 | 0.934 |
| graph pruning + severity only | 0.300 | 0.626 | 0.507 |
| flat evidence, relationships removed (tuned) | 0.816 | 0.958 | 0.887 |
| flat change correlation | 0.652 | 0.830 | 0.762 |
| personalised PageRank (MicroRCA-style) | 0.464 | 0.716 | 0.598 |
| previous SynapseGuard heuristic (deepest failing) | 0.254 | 0.586 | 0.443 |
| blame the alerting service | 0.086 | 0.086 | 0.086 |

Reading the ablations: `change` (−7.0 points) and `lag` (−6.8) carry most of the signal and `common` matters for host faults (−1.2 overall). Removing `frontier` scores slightly *higher* on test (0.956); `coverage`, `onset`, `exposed` and `severity` move results by ≤1.2 points, within the noise of 500 cases (±~1 point standard error). We kept the validation-tuned weights rather than re-tuning on test data.

**Calibration** (`accuracy_otel.md`): top-candidate relative confidence ≥ 0.95 was right
**98.1%** of the time (428 of 500 incidents); 0.80–0.95: 81.4%; 0.50–0.80: 71.4%. Confidently
wrong (≥ 0.8 and wrong): **3.2%** of incidents. Abstaining below 0.9 would answer 89% of
incidents at 98.0% accuracy; the product instead answers and flags anything below 0.9 for
verification (`actions.VERIFY_BELOW`).

Layered 300-service meshes, 300 test incidents: **0.960** top-1 (`accuracy_layered300.md`).
Cypher vs reference mismatches: **0** across all 1,250 scenarios.

**Caveat.** The scorer encodes assumptions that the generator also makes
(bounded propagation lag, fallbacks dampen propagation). These numbers measure
accuracy *under our fault model*. That is why section 2 exists.

## 2. Real fault injection: RCAEval (`bench/eval_rcaeval.py`)

**Data.** RCAEval (Pham et al., arXiv:2412.17015): faults injected with stress-ng
and tc into Online Boutique and Sock Shop on Kubernetes; Prometheus/cAdvisor/Istio
metrics. RE1: 125 cases per system (CPU, MEM, DISK, DELAY, LOSS). RE2: 90 cases
per system, adds SOCKET, **different injected services**: used as held-out data.
No change log and no host placement exist in RCAEval. **Call graphs:** Online Boutique
and Sock Shop from service source code; **Train Ticket learned from real Jaeger spans**
(`bench/trace_topology.py`): 574,479 spans from the *normal period* (before injection) of
one RE2 case → 27 services, 55 edges. Its load generator calls many services directly, so a
virtual `external-client` node (no telemetry, never blamed) calls the 15 services that receive
≥1% of root spans. **Validation:** the same derivation on Online Boutique traces found 9 edges,
all 9 present in our source-derived graph (7 source edges involve services RCAEval did not trace).
Sock Shop has no traces in RE2.

**Protocol.** As RCAEval's `main.py`: drop `*_latency-50`, ffill, 10 min before
and after the given injection time; AC@k and Avg@5 on the root-cause service.
Per-service anomaly = max robust z over its metrics (median/IQR of the normal
window). Call graphs come from each system's source code (`CALL_GRAPHS`). The
incident is reported on the entrypoint. Threshold Z=10 was chosen on RE1 by
leave-one-system-out tuning and **fixed before running RE2**.

**Results (Avg@5)**, `rcaeval_re1.json`, `rcaeval_re2.json`, `official_baro_*.txt`:

| | RE1-OB | RE1-SS | RE1-TT | RE2-OB (held-out) | RE2-SS (held-out) | RE2-TT (held-out) |
|---|---:|---:|---:|---:|---:|---:|
| SynapseGuard, product default (graph pruning + severity on metrics-only data) | **0.845** | **0.875** | 0.624 | 0.904 | 0.938 | 0.698 |
| SynapseGuard, evidence profile with synthetic-tuned weights | 0.834 | 0.650 | 0.331 | 0.873 | 0.891 | 0.684 |
| No-graph max robust-z (same preprocessing) | 0.821 | 0.854 | **0.629** | **0.922** | **0.942** | **0.740** |
| Official BARO (RCAEval code @ 259ea41) | 0.90 | 0.74 | 0.58 | 0.60 | 0.63 | 0.73 |

Cases: RE1 125 per system, RE2 90 per system (645 total). True root cause reachable from the
entry through degraded calls: RE1 96–100%; RE2 OB 93%, SS 96%, TT 87%.

**Leave-one-system-out (LOSO):** evidence-profile weights (and Z) tuned on the *other two*
systems of the same suite, reported on the held-out system:

| Avg@5 | RE1-OB | RE1-SS | RE1-TT | RE2-OB | RE2-SS | RE2-TT |
|---|---:|---:|---:|---:|---:|---:|
| Evidence profile, LOSO weights | **0.872** | **0.893** | **0.653** | 0.904 | 0.933 | **0.773** |
| No-graph max robust-z | 0.821 | 0.854 | 0.629 | **0.922** | **0.942** | 0.740 |

With weights learned on other *real* systems, the graph-based evidence scorer beats the
no-graph scorer on 4 of 6 held-out system-suites (with OB and SS only as training systems, it
won on 1 of 2). The product default was fixed before this analysis and is **not** changed to
the LOSO weights here; doing that properly needs a fresh held-out set.

**What this means**

* On real metrics-only data, the graph's **ranking** is on par with a strong
  no-graph scorer: +2.4/+2.1 points on RE1, −1.8/−0.4 on held-out RE2. Not a win.
* Structural weights learned on synthetic data, or on one real system, **did not
  transfer** (Sock Shop's per-service databases are always "frontier" leaves).
  This is why the product uses the evidence profile only when the graph has change
  or placement data, and pruning plus severity otherwise.
* On RE2, pruning hurt because the root was not reachable from the entrypoint
  through degraded calls in 4–13% of cases (13% on Train Ticket). A soft reachability bonus
  instead of hard pruning may help; it would have to be tuned on RE1 and checked on RE2, and we
  have not done that.
* Our robust-z scorer beats the official BARO by a wide margin on RE2. We have not
  explained the gap; it is *not* BARO and we make no claim against BARO.
* The ranking value shown in section 1 depends on joining calls with change and
  placement data, which RCAEval does not contain. Evaluating that on real
  incidents needs a dataset we did not have.

**Bug found by this evaluation.** Severity was the raw anomaly score clipped to
1.0; on real telemetry many services saturate, so ties were broken alphabetically.
It is now a percentile rank. Both benchmarks were re-run after the fix.

## 3. Scale (`bench/bench_scale.py`)

See `bench/results/scale.md` for the table: ingestion rate, BFS vs
single-variable-length-query extraction, end-to-end investigation p50/p95/p99,
max-flow time, 1- and 8-thread throughput, graph memory. Notes:

* Work depends on the size of the anomalous region, not the mesh (8 nodes at 10k services).
* Investigation issues ~25 small queries (incl. who-to-page), so end-to-end latency is dominated by
  the FalkorDB round trip. `RETURN 1` measured 0.9 ms p50 for the published run; an earlier run the
  same day measured roughly 2.5× faster end to end. Treat absolute numbers as ±2× environment-dependent.
* At 10k services, name resolution originally loaded every node and hit FalkorDB's
  `RESULTSET_SIZE` cap (10,000 rows), so services beyond it were reported unknown. Fixed with bounded,
  index-friendly lookups; regression test `test_name_resolution_beyond_resultset_cap`.
* At 10k services, `algo.maxFlow` over the whole mesh dominates investigation time.
* The single variable-length query is faster than BFS at these sizes (one round trip)
  and returns the same node set (`tests/test_rca.py::test_single_varlength_query_matches_bfs`);
  BFS is kept for its bounded worst case.
* Switching lookups to `WHERE n.id IN $list` (index scan) cut 1k-service investigation
  p50 from 102.7 ms to 28.9 ms and made 10k-service blast radius finish instead of timing out.

## 4. Reproduce

```bash
python -m bench.eval_accuracy
python -m bench.eval_accuracy --topology layered:300 --val 0:150 --test 2000:2300
python -m bench.bench_scale --sizes 100 1000 10000 --reps 30
python -m scripts.prepare_rcaeval --out bench/data/rcaeval   # RE1 zip (~390 MB) + RE2 metrics (~30 MB)
python -m bench.eval_rcaeval --suite re1 --data bench/data/rcaeval
python -m bench.eval_rcaeval --suite re2 --data bench/data/rcaeval
```
