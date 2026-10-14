# Official Demo Video Script (2 to 3 Minutes)

**Product:** SynapseGuard  
**Track:** Track 01 — Agents That Act on Connected Data  
**Goal:** Prove FalkorDB's indispensability, show live graph reasoning, and demonstrate an autonomous containment action.

---

## Act 1: The Problem (0:00 - 0:35)
- **Visual:** Open browser showing the frontend dashboard or incident alert: `"CRITICAL: HTTP 504 Timeouts on Checkout API"`.
- **Narration:**
  > *"Modern cloud applications consist of hundreds of interdependent services. When an outage strikes, alerts fire everywhere at once. Traditional monitoring and vector RAG search fail because they confuse symptoms with root causes—blaming the API Gateway because it has the most error logs. Today we introduce **SynapseGuard**, an autonomous SRE agent whose reasoning runs entirely on **FalkorDB**."*

---

## Act 2: Live FalkorDB Graph Inspection (0:35 - 1:15)
- **Visual:** Click **"Reset & Seed Topology"**. Watch Cytoscape render the 13-node live microservice graph with color-coded nodes (Green = Healthy, Red = Failing, Yellow = ConfigChange).
- **Narration:**
  > *"Here is our live service topology loaded directly from FalkorDB. Notice the synchronous call chain: `API-Gateway` calls `Order-Service`, which calls `Inventory-Service`, which depends on `MySQL-InventoryShard-01`. Moments ago, a DevOps engineer altered `max_connections` on the MySQL shard from 500 down to 20. The database began rejecting connections, cascading timeouts 3 hops upstream all the way to the frontend gateway."*

---

## Act 3: Autonomous Agent Execution & Evidence Trail (1:15 - 2:05)
- **Visual:** Click **"Run Autonomous Agent"**. The spinner appears for ~1.5s, then displays the **Identified Root Cause**, **Multi-Hop Evidence Trail**, and **Containment Plan**.
- **Narration:**
  > *"When we trigger SynapseGuard, the agent executes variable-length Cypher traversals across the graph. In less than 5 milliseconds, it isolates the true culprit: `MySQL-InventoryShard-01`, 3 hops deep. Look at the verifiable evidence trail: it traces the exact dependency edges and correlates the parameter mutation. It then computes the upstream blast radius to find all affected services and generates an autonomous containment plan: roll back the config change and trip the circuit breaker on inbound inventory calls."*

---

## Act 4: The Graph Removal Test & Conclusion (2:05 - 2:40)
- **Visual:** Highlight the **FalkorDB Graph Removal Benchmark** card at the bottom of the dashboard.
- **Narration:**
  > *"What happens without FalkorDB? We ran an automated ablation test: flat search sees 3 failing services at once with no directional awareness, falsely blaming the API Gateway with a 42% hallucination rate. With FalkorDB, causal accuracy is 100%. SynapseGuard proves that connected data is essential for reliable AI agents. Built with FalkorDB for Track 01 of Graph Hacks 2026. Thank you!"*

