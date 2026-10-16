# Demo video script (about 3 minutes)

Record at 1440×900. Before recording: `docker compose up -d` (or uvicorn),
`curl -X POST "http://127.0.0.1:8000/api/reset?confirm=true"`, then open
**http://127.0.0.1:8000** (use 127.0.0.1: an old service worker from another
project may hijack `localhost:8000` in your browser). Everything shown is live;
say once that the scenarios use synthetic telemetry on the real OpenTelemetry
Demo topology. Optional pre-warm: ask the LLM one question before recording so
the model is loaded.

| Time | Show | Say |
|---|---|---|
| 0:00–0:15 | Graph | "When checkout breaks, the alert fires on the front door. The cause is usually hops away, and the first twenty minutes go into finding it and finding who owns it." |
| 0:15–0:40 | Load **payment-flag** → Investigate. Point at the **Root cause** banner | "SynapseGuard follows the call graph in FalkorDB from the alerting service, only into unhealthy services. Root cause: Payment, a paymentFailure flag that landed 79 seconds before Payment degraded. And it tells me who to page: Alice, on call for the owning team, who also authored the flag change. Every line has its graph path." |
| 0:40–1:00 | **Graph removed** tile, timeline | "Same signals without relationships blame a flapping decoy. The timeline shows why the graph answer holds: Payment broke first, its callers followed within a minute." |
| 1:00–1:25 | Ask chips: **depth 1**, then **depth 4** | "What breaks if Payment goes down? One hop: Checkout, something a join could tell you. Four hops: six services, including the chatbot, via agent, frontend, checkout. Every path is shown." |
| 1:25–1:40 | Chip **Unknown: payments-gateway** | "Ask about something that isn't in the graph and it says so, and suggests 'payment'. No query runs on a name that doesn't resolve." |
| 1:40–2:05 | Blast radius card, breakers; approve + execute rollback; Investigate again; Confirm | "It places circuit breakers with FalkorDB's max-flow algorithm. Actions need a human. After the simulated rollback, only unrelated noise remains, and confirming the root cause writes memory." |
| 2:05–2:35 | Type "Postgres is down, who do I wake?" → **Ask LLM** (or terminal `python -m scripts.mcp_smoke`) | "A local model picks from a fixed catalogue of human-written graph tools over MCP. It never writes Cypher; names are resolved against the graph; its sentence is rejected if it names anything the tools didn't return. 12 out of 12 tool choices correct in our test." |
| 2:35–3:00 | `docs/evaluation.md` table | "Measured: 95% top-1 on held-out synthetic incidents versus 82% without the graph. On real fault-injection data from RCAEval, ranking is on par with a strong baseline, and the repo says exactly that." |
