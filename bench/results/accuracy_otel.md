# Root-cause accuracy: topology `otel`

Validation seeds 0:300 (weights tuned here), test seeds 1000:1500 (reported here). Fault mix in test: {'config_change': 79, 'deploy_bug': 106, 'feature_flag': 74, 'host_saturation': 92, 'retry_storm': 65, 'silent': 84}.

| Method | Uses | Top-1 | Top-3 | MRR |
|---|---|---:|---:|---:|
| ours, evidence profile | graph | 0.952 | 0.994 | 0.972 |
| ours, auto profile (product default) | graph | 0.952 | 0.994 | 0.972 |
| graph pruning + severity only | graph (ablation) | 0.300 | 0.626 | 0.507 |
| ours, no reachability pruning | graph (ablation) | 0.908 | 0.988 | 0.946 |
| ours without 'frontier' | graph (ablation) | 0.956 | 0.994 | 0.975 |
| ours without 'coverage' | graph (ablation) | 0.950 | 0.994 | 0.971 |
| ours without 'onset' | graph (ablation) | 0.950 | 0.992 | 0.971 |
| ours without 'change' | graph (ablation) | 0.882 | 0.986 | 0.934 |
| ours without 'severity' | graph (ablation) | 0.944 | 0.994 | 0.968 |
| ours without 'common' | graph (ablation) | 0.940 | 0.994 | 0.966 |
| ours without 'exposed' | graph (ablation) | 0.946 | 0.994 | 0.969 |
| ours without 'lag' | graph (ablation) | 0.884 | 0.984 | 0.934 |
| flat evidence (tuned, no relationships) | flat | 0.816 | 0.958 | 0.887 |
| symptom_blame | flat | 0.086 | 0.086 | 0.086 |
| flat_anomaly | flat | 0.160 | 0.454 | 0.348 |
| flat_change | flat | 0.652 | 0.830 | 0.762 |
| naive_deepest (old SynapseGuard) | graph | 0.254 | 0.586 | 0.443 |
| pagerank (MicroRCA-style) | graph | 0.464 | 0.716 | 0.598 |

Top-1 by fault kind:

| Method | config_change | deploy_bug | feature_flag | host_saturation | retry_storm | silent |
|---|---:|---:|---:|---:|---:|---:|
| ours, evidence profile | 0.97 | 0.97 | 1.00 | 0.99 | 0.92 | 0.84 |
| ours, auto profile (product default) | 0.97 | 0.97 | 1.00 | 0.99 | 0.92 | 0.84 |
| graph pruning + severity only | 0.33 | 0.28 | 0.34 | 0.25 | 0.18 | 0.41 |
| ours, no reachability pruning | 0.96 | 0.96 | 0.96 | 0.99 | 0.94 | 0.63 |
| ours without 'frontier' | 0.99 | 0.98 | 1.00 | 1.00 | 0.92 | 0.83 |
| ours without 'coverage' | 0.99 | 0.98 | 0.99 | 1.00 | 0.92 | 0.81 |
| ours without 'onset' | 0.99 | 0.99 | 1.00 | 1.00 | 0.91 | 0.80 |
| ours without 'change' | 0.82 | 0.88 | 0.91 | 1.00 | 0.72 | 0.92 |
| ours without 'severity' | 0.97 | 0.97 | 1.00 | 1.00 | 0.89 | 0.81 |
| ours without 'common' | 0.97 | 0.97 | 1.00 | 0.90 | 0.95 | 0.84 |
| ours without 'exposed' | 0.97 | 0.97 | 0.99 | 0.99 | 0.92 | 0.82 |
| ours without 'lag' | 0.96 | 0.95 | 0.99 | 0.99 | 0.86 | 0.54 |
| flat evidence (tuned, no relationships) | 0.91 | 0.93 | 0.99 | 0.96 | 0.91 | 0.21 |
| symptom_blame | 0.08 | 0.11 | 0.16 | 0.00 | 0.01 | 0.14 |
| flat_anomaly | 0.23 | 0.16 | 0.23 | 0.00 | 0.18 | 0.19 |
| flat_change | 0.91 | 0.90 | 0.92 | 0.25 | 0.88 | 0.13 |
| naive_deepest (old SynapseGuard) | 0.42 | 0.33 | 0.42 | 0.00 | 0.06 | 0.29 |
| pagerank (MicroRCA-style) | 0.66 | 0.65 | 0.72 | 0.00 | 0.08 | 0.63 |

Calibration of the top candidate's relative confidence (ours, test set):

| Confidence | n | Accuracy |
|---|---:|---:|
| 0.00-0.50 | 1 | 1.0 |
| 0.50-0.80 | 28 | 0.714 |
| 0.80-0.95 | 43 | 0.814 |
| 0.95-1.00 | 428 | 0.981 |

Confidently wrong (confidence >= 0.8 and wrong): **3.2%** of incidents.  
Abstaining below a threshold: >=0.0: answers 100%, 0.952 correct; >=0.5: answers 100%, 0.952 correct; >=0.8: answers 94%, 0.966 correct; >=0.9: answers 89%, 0.98 correct  

Tuned weights (ours): `{'frontier': 0.5, 'coverage': 1.0, 'onset': 2.0, 'change': 2.0, 'severity': 0.5, 'common': 2.0, 'exposed': 0.25, 'lag': 2.0}`  
Tuned weights (flat): `{'onset': 0.25, 'change': 1.5, 'severity': 0.5, 'common': 1.5}`  
Cypher traversal vs pure-Python reference mismatches: **0**  
Extract + score latency through FalkorDB: p50 13.2 ms, p95 18.4 ms, p99 23.5 ms (mean subgraph 6.6 nodes, 6.4 queries)  
Environment: {'python': '3.12.10', 'platform': 'Windows-11-10.0.26300-SP0', 'processor': 'Intel64 Family 6 Model 170 Stepping 4, GenuineIntel'}  
Wall time: 166.4 s

Scenarios come from our own fault model; see docs/evaluation.md for its assumptions and limits.
