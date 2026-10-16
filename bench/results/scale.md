# Scale benchmark

Environment: {'python': '3.12.10', 'platform': 'Windows-11-10.0.26300-SP0', 'processor': 'Intel64 Family 6 Model 170 Stepping 4, GenuineIntel', 'falkordb_module': '6.0.2 (Docker, local)'}

| Services | Calls | Ingest rows/s | Subgraph | Extract BFS p50/p95 ms | Var-length p50 ms | Investigate p50/p95/p99 ms | Max-flow p50 ms | 8-thread inv/s | Graph MB |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 234 | 907 | 7 | 13.59/21.38 | 5.44 | 46.51/60.26/65.03 | 7.12 | 59.9 | 0.0 |
| 1000 | 2450 | 3289 | 11 | 24.68/27.73 | 20.88 | 69.59/87.19/90.97 | 23.32 | 34.0 | 0.0 |
| 10000 | 24656 | 3667 | 8 | 19.83/27.63 | 14.53 | 260.42/288.11/329.62 | 195.55 | 4.6 | 15.0 |
