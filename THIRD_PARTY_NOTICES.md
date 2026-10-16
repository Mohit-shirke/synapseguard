# Third-party notices

| Component | Use | License | Source |
|---|---|---|---|
| FalkorDB server (`falkordb/falkordb:6.0.2`) | graph database, run unmodified as a separate service | SSPL v1 | <https://github.com/FalkorDB/FalkorDB> |
| falkordb-py | Python client | MIT | <https://github.com/FalkorDB/falkordb-py> |
| MCP Python SDK (`mcp`) | MCP server/client | MIT | <https://github.com/modelcontextprotocol/python-sdk> |
| FastAPI, Uvicorn, Pydantic, httpx, NetworkX, NumPy, pandas, pyarrow | runtime / benchmarks | MIT / BSD | PyPI |
| Cytoscape.js 3.30.2 (cdnjs) | graph rendering in the UI | MIT | <https://github.com/cytoscape/cytoscape.js> |
| OpenTelemetry Demo | service names, call directions and feature-flag names used for the demo topology (`synapseguard/data/topology.py`); no code copied | Apache-2.0 | <https://opentelemetry.io/docs/demo/architecture/> |
| Online Boutique (GoogleCloudPlatform/microservices-demo) | call graph derived from service source for the RCAEval evaluation | Apache-2.0 | <https://github.com/GoogleCloudPlatform/microservices-demo> |
| Sock Shop (microservices-demo) | call graph derived from service source for the RCAEval evaluation | Apache-2.0 | <https://github.com/microservices-demo/microservices-demo> |
| Train Ticket (FudanSELab) | service call graph **learned from RCAEval RE2 Jaeger spans** (`bench/results/topology_train-ticket_from_traces.json`), used for evaluation | Apache-2.0 | <https://github.com/FudanSELab/train-ticket> |
| RCAEval datasets RE1, RE2 | evaluation only; downloaded at run time, **not redistributed** | CC-BY-4.0 (Zenodo), MIT (HF mirror) | <https://zenodo.org/records/14504481>, <https://huggingface.co/datasets/phamquiluan/RCAEval> |
| RCAEval code incl. BARO | run unmodified at commit `259ea41` as the reference baseline; not vendored | MIT | <https://github.com/phamquiluan/RCAEval> |

RCAEval citation: Luan Pham, Hongyu Zhang, Huong Ha, Flora Salim, Xiuzhen Zhang.
*RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with
Telemetry Data.* arXiv:2412.17015.
