# What a graph actually buys an incident agent (and what it doesn't)

*By Mohit Sachin Shirke (team Night Hunter). Draft for the Graph Hacks blog-post side prize; edit it into your own voice before publishing.*

When checkout breaks, the page fires on the front door: the proxy returns 5xx,
the frontend's latency spikes. The cause is usually somewhere those services
call, two or four hops down. I built SynapseGuard for Graph Hacks to see how
much a graph database helps an agent find that cause, and I measured it on real
data too.

## The setup

The agent's whole context lives in FalkorDB: the service call graph (I used the
OpenTelemetry Demo's real topology), per-service telemetry, host placement,
the change log (deploys, config, feature flags), and its own memory of past
incidents. When an incident arrives, it does three things a table can't:

1. **Prune by reachability.** It follows synchronous `CALLS` edges from the
   alerting service, but only into unhealthy services. A database that is broken
   but unreachable from the symptom isn't the cause of *this* symptom.
2. **Score with evidence.** Inside that subgraph: does anything below a candidate
   explain it? How much does it explain? Did it degrade first, and did its callers
   follow within minutes? Did a change land just before? Do several broken services
   share a saturated host?
3. **Act on the structure.** It computes the blast radius and runs FalkorDB's
   `algo.maxFlow` to place the cheapest circuit breakers that isolate users from the
   fault, given that only some clients can circuit-break.

An LLM is optional. The agent's tools are also exposed over MCP, with free-form
Cypher running through `GRAPH.RO_QUERY`, so Claude can investigate on its own
and the database itself refuses its writes.

## The results I expected, and the ones I got

On 500 held-out synthetic incidents (with decoys, red-herring deploys and missing
telemetry), the graph agent found the root cause first **95%** of the time.
The strongest rows-only baseline, with the same signals and the same tuning,
managed **82%**. The old version of my project, which picked the "deepest failing
service", managed 25%.

Then I ran it on **RCAEval**, a public benchmark of real fault injection: 645 cases
across Online Boutique, Sock Shop and Train Ticket, with metrics from real clusters and
no change logs or host placement. For Train Ticket I learned the call graph from
574,479 real Jaeger spans (normal period only); on Online Boutique the same method
confirmed every edge of my hand-written graph. The honest result: with my default
settings, ranking is **on par** with a strong no-graph scorer. It's better on some systems
and worse on others, including 4 points worse on held-out Train Ticket. With weights learned
on the *other* real systems, though, the graph scorer won on 4 of 6 held-out system-suites.
That's the direction I'd take next. The graph's ranking advantage comes from joining telemetry to changes and
placement; RCAEval doesn't have those joins, so there's nothing for it to exploit.

Real data also caught two bugs that synthetic data never would have. A severity
score saturated at 1.0 and stopped discriminating. Structural weights tuned on one
real system hurt on the other.

## Two FalkorDB bugs, found along the way

My tests and demo runs found two issues in FalkorDB 6.0.2: a query shape that panics the server,
and one that silently returns no rows unless you put a `WITH` between two
`MATCH … WHERE` clauses. Both have minimal reproductions in the repo. Also,
`WHERE n.id IN $list` uses the index, while `UNWIND … MATCH (n {id: x})` didn't;
switching cut latency about 3.6× at 1,000 services.

## Takeaways

* A graph earns its place in an agent when the answer depends on *joining*
  structure with operational context: calls with changes, placement and time.
* Measure on someone else's data. My synthetic benchmark flattered me.
* Give LLM agents graph tools, not graph write access. Read-only mode belongs
  in the database, not in a prompt.

Code, benchmarks and raw results: *(add repository link)*
