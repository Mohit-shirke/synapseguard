# Root-cause accuracy: topology `layered:300`

Validation seeds 0:150 (weights tuned here), test seeds 2000:2300 (reported here). Fault mix in test: {'config_change': 52, 'deploy_bug': 68, 'feature_flag': 42, 'host_saturation': 61, 'retry_storm': 33, 'silent': 44}.

| Method | Uses | Top-1 | Top-3 | MRR |
|---|---|---:|---:|---:|
| ours, evidence profile | graph | 0.960 | 0.970 | 0.965 |
| ours, auto profile (product default) | graph | 0.960 | 0.970 | 0.965 |
| graph pruning + severity only | graph (ablation) | 0.270 | 0.603 | 0.462 |
| ours, no reachability pruning | graph (ablation) | 0.943 | 0.987 | 0.967 |
| ours without 'frontier' | graph (ablation) | 0.957 | 0.970 | 0.963 |
| ours without 'coverage' | graph (ablation) | 0.960 | 0.970 | 0.965 |
| ours without 'onset' | graph (ablation) | 0.963 | 0.970 | 0.967 |
| ours without 'change' | graph (ablation) | 0.940 | 0.963 | 0.951 |
| ours without 'severity' | graph (ablation) | 0.960 | 0.970 | 0.965 |
| ours without 'common' | graph (ablation) | 0.953 | 0.970 | 0.962 |
| ours without 'exposed' | graph (ablation) | 0.960 | 0.970 | 0.965 |
| ours without 'lag' | graph (ablation) | 0.953 | 0.970 | 0.962 |
| flat evidence (tuned, no relationships) | flat | 0.853 | 0.923 | 0.898 |
| symptom_blame | flat | 0.083 | 0.083 | 0.083 |
| flat_anomaly | flat | 0.127 | 0.393 | 0.302 |
| flat_change | flat | 0.760 | 0.830 | 0.810 |
| naive_deepest (old SynapseGuard) | graph | 0.277 | 0.603 | 0.451 |
| pagerank (MicroRCA-style) | graph | 0.603 | 0.730 | 0.669 |

Top-1 by fault kind:

| Method | config_change | deploy_bug | feature_flag | host_saturation | retry_storm | silent |
|---|---:|---:|---:|---:|---:|---:|
| ours, evidence profile | 1.00 | 1.00 | 1.00 | 1.00 | 0.67 | 0.98 |
| ours, auto profile (product default) | 1.00 | 1.00 | 1.00 | 1.00 | 0.67 | 0.98 |
| graph pruning + severity only | 0.27 | 0.31 | 0.38 | 0.23 | 0.06 | 0.32 |
| ours, no reachability pruning | 0.92 | 0.98 | 0.95 | 1.00 | 0.94 | 0.82 |
| ours without 'frontier' | 1.00 | 1.00 | 1.00 | 1.00 | 0.67 | 0.95 |
| ours without 'coverage' | 1.00 | 1.00 | 1.00 | 1.00 | 0.67 | 0.98 |
| ours without 'onset' | 1.00 | 1.00 | 1.00 | 1.00 | 0.67 | 1.00 |
| ours without 'change' | 0.98 | 0.98 | 1.00 | 1.00 | 0.55 | 0.98 |
| ours without 'severity' | 1.00 | 1.00 | 1.00 | 1.00 | 0.64 | 1.00 |
| ours without 'common' | 1.00 | 1.00 | 1.00 | 0.93 | 0.73 | 0.98 |
| ours without 'exposed' | 1.00 | 1.00 | 1.00 | 1.00 | 0.67 | 0.98 |
| ours without 'lag' | 1.00 | 1.00 | 1.00 | 1.00 | 0.64 | 0.95 |
| flat evidence (tuned, no relationships) | 1.00 | 0.98 | 1.00 | 0.98 | 0.85 | 0.16 |
| symptom_blame | 0.15 | 0.07 | 0.17 | 0.00 | 0.00 | 0.11 |
| flat_anomaly | 0.14 | 0.21 | 0.19 | 0.00 | 0.06 | 0.16 |
| flat_change | 1.00 | 1.00 | 0.98 | 0.51 | 0.88 | 0.16 |
| naive_deepest (old SynapseGuard) | 0.42 | 0.41 | 0.41 | 0.00 | 0.09 | 0.29 |
| pagerank (MicroRCA-style) | 0.73 | 0.91 | 0.98 | 0.00 | 0.00 | 0.91 |

Tuned weights (ours): `{'frontier': 0.5, 'coverage': 1.0, 'onset': 2.0, 'change': 2.0, 'severity': 0.5, 'common': 2.0, 'exposed': 0.25, 'lag': 2.0}`  
Tuned weights (flat): `{'onset': 0.0, 'change': 1.5, 'severity': 0.3, 'common': 1.5}`  
Cypher traversal vs pure-Python reference mismatches: **0**  
Extract + score latency through FalkorDB: p50 10.3 ms, p95 23.2 ms, p99 33.8 ms (mean subgraph 16.1 nodes, 7.4 queries)  
Environment: {'python': '3.12.10', 'platform': 'Windows-11-10.0.26300-SP0', 'processor': 'Intel64 Family 6 Model 170 Stepping 4, GenuineIntel'}  
Wall time: 58.8 s

Scenarios come from our own fault model; see docs/evaluation.md for its assumptions and limits.
