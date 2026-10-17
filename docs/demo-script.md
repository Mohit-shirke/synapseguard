# Demo video script (2:49)

Narration and on-screen action of the submitted demo video. Every product scene is a
continuous screen recording of the live app (light theme), and every number said is the one on
screen or in `bench/results/`. The scenarios use synthetic telemetry on the real OpenTelemetry
Demo topology.

| Time | On screen | Narration |
|---|---|---|
| 0:00 | Title: SynapseGuard · Mohit Sachin Shirke · team Night Hunter · Track 01 | "A smoke alarm tells you there is a fire, but never which room it started in. I am Mohit Sachin Shirke, from team Night Hunter. In the next two minutes, you'll see an agent that walks from the alarm to the room where it began, and shows every step. It's called SynapseGuard, built on FalkorDB." |
| 0:19 | Card: the problem | "Modern software is a building with hundreds of rooms. When checkout fails, the alert fires at the front door, while the cause sits a few calls away, and engineers lose precious minutes." |
| 0:29 | **paymentFailure flag** scenario → **Load scenario** | "An incident is reported on the frontend proxy. Everything in red is degraded. So which service actually started it?" |
| 0:38 | **Investigate** → root-cause banner, causal path on the graph | "First, find the cause. One click, and the agent walks the graph through unhealthy services only. Root cause: payment, at ninety-four percent confidence. A feature flag landed seventy-nine seconds before it degraded, so: page Alice, the on-call owner." |
| 0:53 | Incident timeline, Who to page | "Crucially, it shows its working. Payment degraded first, and its callers followed within a minute. Every answer comes with its path." |
| 1:02 | Ask the graph: **What breaks? depth 1** | "Second, ask the graph, through fixed, human-written tools. What breaks if payment goes down? One hop out, a single service." |
| 1:11 | **What breaks? depth 4** | "Four hops out, six services, including the chatbot, each with its path." |
| 1:15 | **Unknown: payments-gateway** | "Ask about a service that doesn't exist, and no query runs. It suggests the closest real one." |
| 1:21 | Blast radius and `algo.maxFlow` breaker plan → **Approve** → **Execute (simulated)** | "Third, contain it. The agent computes the blast radius and places circuit breakers with FalkorDB's max flow algorithm. Nothing runs without a human: approve, then execute." |
| 1:34 | **Investigate** again → mcp, 78%, "verify" | "After the approved rollback, payment is healthy again. A second, unrelated fault is flagged for verification at seventy-eight percent. It knows when it's guessing." |
| 1:44 | **Silent PostgreSQL** scenario → postgresql vs graph-removed *cache* (Disagrees) | "Now, the surprise. With no change record at all, the graph still finds the database. Remove the relationships, and the very same signals blame the wrong service. The graph is not decoration. It is the reasoning." |
| 1:59 | **Saturated Kubernetes node** scenario → node-c | "When a Kubernetes node is saturated, it blames the shared host, not each service on it." |
| 2:04 | Card: same tools over MCP | "The same tools are an MCP server. A local model chose the right tool twelve out of twelve times, and never writes a query or approves an action." |
| 2:15 | Card: measured, not claimed | "On five hundred held-out incidents, it ranks the true cause first ninety-five percent of the time, against eighty-two without the graph. On six hundred and forty-five real fault-injection cases, it is on par with a strong baseline, and the repository says so openly. Along the way, I found two FalkorDB bugs." |
| 2:35 | Closing: repository and recorded-demo links | "So, four contributions: a root cause you can verify; answers only from the graph; containment that waits for a human; and honest results. Every answer comes with its path. I'm Mohit Shirke, team Night Hunter." |

## Reproduce the scenes

```bash
docker compose up -d --build
curl -X POST "http://127.0.0.1:8000/api/reset?confirm=true"
```

Open <http://127.0.0.1:8000> and follow the "On screen" column. Sources of the numbers:
94% / 79 s / Alice Moreau, depth 1 vs 4, the unknown name and the 78% verify flag are live
results; 95.2% vs 81.6% is `bench/results/accuracy_otel.json`; 645 RCAEval cases are
`bench/results/rcaeval_re*.json`; 12 / 12 tool choices is `bench/results/llm_agent.json`;
the two FalkorDB bugs are in [upstream-issues.md](upstream-issues.md).
