# System Architecture: SynapseGuard

## 1. High-Level Architecture Overview

SynapseGuard couples **FalkorDB's linear-algebra GraphBLAS engine** with an autonomous agent pipeline and an interactive Cytoscape.js SRE command dashboard.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Interactive Web Dashboard                       │
│      (Cytoscape.js Topology Canvas + Agent Decision Provenance Feed)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / REST
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Backend Engine                          │
│               (/api/investigate, /api/graph, /api/blast-radius)        │
└───────────────┬────────────────────────────────────────┬───────────────┘
                │                                        │
                ▼                                        ▼
┌──────────────────────────────┐        ┌────────────────────────────────┐
│     SynapseAgent Engine      │        │      Graph Traversal Engine    │
│  (Ollama Local LLaMA 3.1 8B  │◄───────┤ (Multi-Hop Root Cause,         │
│  + Deterministic Fallback)   │        │  Blast Radius, Min-Cut Breaker)│
└──────────────────────────────┘        └────────────────┬───────────────┘
                                                         │
                                                         ▼
                                        ┌────────────────────────────────┐
                                        │       FalkorDB Engine          │
                                        │  (GraphBLAS Sparse Matrix,     │
                                        │   High-Throughput Cypher)      │
                                        └────────────────────────────────┘
```

---

## 2. Component Breakdown

1. **FalkorDB Graph Layer (`synapseguard.core.db`)**:
   - Manages connection lifecycle and connection pool.
   - Creates uniqueness and lookup indices on `(Service.id)`, `(Incident.id)`, `(ConfigChange.id)`, `(Host.id)`.
   - Executes parameterized Cypher queries.

2. **Graph Traversal & Graph Algorithms (`synapseguard.engine.traversal`)**:
   - Multi-hop causal isolation algorithm (`O(V + E)` bounded depth BFS/DFS via Cypher path pattern matching).
   - Upstream blast radius reachability algorithm.
   - Min-cut circuit-breaker selection algorithm.

3. **Autonomous Agent (`synapseguard.engine.agent`)**:
   - Gathers graph-retrieved subgraphs and builds evidence chains.
   - Instructs local LLM (Ollama `llama3.1:8b`) to format structured JSON root-cause explanations and prioritized rollback steps.
   - Provides deterministic zero-cost fallback when LLMs or networks are offline.

4. **REST API Service (`synapseguard.api.server`)**:
   - Exposes clean REST endpoints adhering to OpenAPI specifications.
   - Converts FalkorDB nodes and relationships into Cytoscape.js graph JSON.

5. **Frontend Canvas (`synapseguard.frontend.index.html`)**:
   - Visualizes live service topologies, status colors, and active incidents.
   - Displays node-by-node evidence provenance trails in real time.

