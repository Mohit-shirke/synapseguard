# Known issues and limitations

## FalkorDB 6.0.2 issues found while building this (with workarounds in our code)

Both were found by our own tests and verified on `falkordb/falkordb:6.0.2`
(module version 60002, image digest `sha256:107455e8…59ba6`). They are not
reported upstream yet; the reproductions below are ready to file.

### 1. Server panic: two OPTIONAL MATCH + aggregation + ORDER BY

On a graph containing several `Incident` nodes that share an `id`, with no
`Investigation` label and no `CONFIRMED_ROOT_CAUSE` relationship type present,
this query killed the server process (`FalkorDB panic: panicked at
src/reply.rs:655:58: called Option::unwrap() on a None value`):

```cypher
MATCH (i:Incident)
OPTIONAL MATCH (i)-[:CONFIRMED_ROOT_CAUSE]->(rc)
OPTIONAL MATCH (v:Investigation)-[:INVESTIGATES]->(i)
RETURN i.id, i.title, i.status, i.scenario, i.opened_at, rc.id, count(v)
ORDER BY i.opened_at DESC LIMIT 20
```

Each part alone (one OPTIONAL MATCH, no ORDER BY, fewer projected columns)
runs fine. Workaround used in `synapseguard/engine/recall.py`: aggregate in a
`WITH` before the second OPTIONAL MATCH and order by an alias. Regression test:
`tests/test_api_memory.py::test_memory_queries_survive_legacy_shaped_data`.

### 2. Wrong (empty) result: consecutive MATCH ... WHERE clauses

```cypher
CREATE (:S {id:'a'})-[:C]->(:S {id:'root'}), (:S {id:'b'})-[:C]->(:S {id:'root2'})
// with $roots = ['root']:
MATCH (b:S) WHERE b.id IN $roots
MATCH (a:S)-[:C]->(b) WHERE NOT a.id IN $roots
RETURN a.id            -- returned [] ; expected ['a']
```

The plan was `Project / Conditional Traverse / Filter / Node By Label Scan`.
Dropping the second WHERE, or inserting `WITH b` between the two MATCH clauses,
returns the correct row. Workaround: every multi-MATCH query in the engine has a
`WITH` between clauses. Regression test:
`tests/test_containment.py::test_naive_plan_sees_direct_callers`.

### 3. Planner note (not a bug): index use with UNWIND

`UNWIND $ids AS x MATCH (n:Service {id: x})` was planned as a full traverse
without the `Service(id)` index; `MATCH (n:Service) WHERE n.id IN $ids` uses a
`Node By Index Scan`. Switching cut the 1,000-service investigation p50 from
~103 ms to ~29 ms and made the 10,000-service blast radius finish instead of
timing out. Plans were checked with `GRAPH.EXPLAIN`.

### 4. Result sets are capped at RESULTSET_SIZE (10,000 by default)

Not a bug, but it bit us: the image starts with `RESULTSET_SIZE 10000`, so any query that
returns every node silently truncates on larger graphs. Our name resolution used to do that
and reported real services as unknown at 10k+ nodes. All lookups are now bounded.

### 5. Variable-length bound cannot be a parameter

`MATCH p=(u)-[:CALLS*1..$depth]->(s)` fails on 6.0.2 with "Encountered unhandled
type in inlined properties" (the organisers' article uses this form). SynapseGuard
expands hop by hop in Python with a bounded loop, so depth is an ordinary,
validated integer argument (1–6).

## Limitations of SynapseGuard

* **Root-cause ranking on real metrics-only data is on par with a strong
  no-graph baseline, not better.** See `docs/evaluation.md`. The graph's
  measured advantage (synthetic benchmark) depends on change logs and host
  placement, which the public real-world benchmark does not contain.
* **Synthetic benchmark bias.** The synthetic scenarios come from our own fault
  model; features that mirror its assumptions (bounded propagation lag,
  fallbacks absorbing failures) are partly rewarded by construction.
* **Predicted recovery assumes faults flow upstream.** In a retry storm the
  overloaded callee also recovers when the caller is fixed; we do not predict that.
* **Telemetry is a snapshot.** Each service holds one current anomaly score and
  onset; there is no time-series store or streaming ingestion.
* **Actions are simulated.** No integration with Kubernetes, Envoy or a
  feature-flag service; "execute" rewrites the graph.
* **Breaker capability and request rates in the OTel demo topology are
  synthetic assumptions** (documented in `synapseguard/data/topology.py`).
* **Confidence is relative** (softmax over candidates), not a calibrated probability.
* **No authentication beyond an optional API key**; intended for local use.
* **People data is synthetic** (fictional names, weekly rotations); it never feeds root-cause scoring.
* **The LLM grounding check verifies names, not claims.** A sentence can name only real, returned
  entities and still order or relate them differently than the tool did (seen once in 12 questions).
* **The LLM agent is slow on CPU** (tens of seconds per question with llama3.1:8b) and optional;
  the tool buttons and the deterministic investigation do not need it.
* **Train Ticket** (RCAEval) is not evaluated with the graph: no verified call graph.
* **Mobile layout** verified down to ~497 px wide (headless Chrome minimum);
  narrower phone widths were not rendered.
