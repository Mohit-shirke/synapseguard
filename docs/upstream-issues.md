# Draft issues for FalkorDB (not filed)

Ready to paste into <https://github.com/FalkorDB/FalkorDB/issues/new>. Both were
reproduced on 2026-10-16 with the steps below. Before filing, search existing issues
for "reply_compact" and for "consecutive MATCH WHERE" in case they are known.

---

## Issue 1 — Wrong (empty) result: second `MATCH ... WHERE NOT x IN $list` after `MATCH ... WHERE y IN $list`

**Environment**: `falkordb/falkordb:6.0.2` (module version 60002, image digest
`sha256:107455e862a824e45e8e9f5a7fbfdf5e00a08b14e13eae2577c72fd9e3f59ba6`),
falkordb-py 1.8.0, Docker Desktop on Windows 11.

**Reproduction** (4 nodes):

```cypher
CREATE (:S {id:'a'})-[:C]->(:S {id:'root'}), (:S {id:'b'})-[:C]->(:S {id:'root2'})
```

```python
g.query("""MATCH (b:S) WHERE b.id IN $roots
           MATCH (a:S)-[:C]->(b) WHERE NOT a.id IN $roots
           RETURN a.id""", {"roots": ["root"]}).result_set
```

**Expected**: `[['a']]`  **Actual**: `[]` (same with an index on `S(id)`).

**Plan**: `Project / Conditional Traverse / Filter / Node By Label Scan`.

**Works**: inserting `WITH b` between the two `MATCH` clauses returns `[['a']]`;
so does dropping the second `WHERE`. It looks like the filters of the two
`MATCH` clauses are merged and applied to the wrong alias.

---

## Issue 2 — Server panic in `reply_compact` (`Option::unwrap()` on `None`)

**Environment**: as above.

**What happened**: this query terminated the server process:

```cypher
MATCH (i:Incident)
OPTIONAL MATCH (i)-[:CONFIRMED_ROOT_CAUSE]->(rc)
OPTIONAL MATCH (v:Investigation)-[:INVESTIGATES]->(i)
RETURN i.id, i.title, i.status, i.scenario, i.opened_at, rc.id, count(v)
ORDER BY i.opened_at DESC LIMIT 20
```

Log: `FalkorDB panic: panicked at src/reply.rs:655:58: called Option::unwrap() on a None value`
(backtrace through `falkordb::reply::reply_compact` and `graph_core::execute_query`).

**Data shape when it happened**: six `Incident` nodes with the same `id` value and
properties `{id, timestamp, title, severity, target_service_id}` (no `status`,
`scenario`, `opened_at`); the graph had **no** `Investigation` label and **no**
`CONFIRMED_ROOT_CAUSE` relationship type. Running the same query on a
`GRAPH.COPY` of that graph crashed it again; simpler variants (one OPTIONAL MATCH,
or without ORDER BY, or fewer projected columns) did not.

**Note**: we did not reduce this to a from-scratch `CREATE` script; a two-node
`(:I {id})` graph with the same query shape did not crash. Attaching an RDB dump
of the affected graph would be the most useful next step for maintainers.

**Workaround**: aggregate in a `WITH` before the second `OPTIONAL MATCH` and order by an alias.

---

## Optional — Documentation note: variable-length bound as a parameter

`MATCH p=(u)-[:R*1..$depth]->(s)` returns
`Encountered unhandled type in inlined properties` on 6.0.2. The WeMakeDevs Graph Hacks
article *How to Build an Agent That Reasons Over a Graph* uses this form. Either
support it or document that the bound must be a literal.
