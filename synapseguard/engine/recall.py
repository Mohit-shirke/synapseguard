"""Investigation memory stored in FalkorDB.

Schema (added on top of the live topology):

    (:Investigation)-[:INVESTIGATES]->(:Incident)
    (:Investigation)-[:SUSPECTS {rank, score, confidence}]->(:Service|:Host)
    (:Investigation)-[:PROPOSES]->(:Action)-[:TARGETS]->(:Change|:Service|:Host)
    (:Incident)-[:AFFECTED]->(:Service)            degraded services seen
    (:Incident)-[:CONFIRMED_ROOT_CAUSE {by, at}]->(:Service|:Host)   human only

Recall finds past incidents whose affected-service sets overlap the current
one (Jaccard similarity, computed in Cypher) or that a human confirmed had the
same root cause as the current top suspect. Memory is shown as context; it
does not change the ranking (we have no labelled data to justify a prior).
"""
from __future__ import annotations

from typing import Any

from synapseguard.core.graph import GraphStore

Q_RECORD = """
MATCH (i:Incident {id: $incident})
CREATE (v:Investigation {id: $id, at: timestamp(), algorithm: $algorithm, mode: $mode,
                         top_id: $top_id, top_confidence: $top_conf})
CREATE (v)-[:INVESTIGATES]->(i)
WITH v, i
UNWIND $suspects AS s
OPTIONAL MATCH (svc:Service {id: s.id})
OPTIONAL MATCH (h:Host {id: s.id})
WITH v, i, s, coalesce(svc, h) AS n
WHERE n IS NOT NULL
CREATE (v)-[:SUSPECTS {rank: s.rank, score: s.score, confidence: s.confidence}]->(n)
"""

Q_AFFECTED = """
MATCH (i:Incident {id: $incident})
WITH i
MATCH (s:Service) WHERE s.id IN $ids
MERGE (i)-[:AFFECTED]->(s)
"""

Q_SIMILAR = """
MATCH (cur:Incident {id: $id})-[:AFFECTED]->(s:Service)
WITH cur, collect(s.id) AS cur_ids
MATCH (past:Incident)-[:AFFECTED]->(ps:Service)
WHERE past.id <> cur.id
WITH cur_ids, past, collect(ps.id) AS past_ids
WITH past, past_ids, [x IN past_ids WHERE x IN cur_ids] AS shared, cur_ids
WITH past, size(shared) AS n_shared, size(cur_ids) + size(past_ids) - size(shared) AS n_union
OPTIONAL MATCH (past)<-[:INVESTIGATES]-(:Investigation)-[:PROPOSES]->(a:Action)
WHERE a.outcome = 'RESOLVED'
WITH past, toFloat(n_shared) / n_union AS jaccard, collect(DISTINCT a.summary) AS fixes
OPTIONAL MATCH (past)-[c:CONFIRMED_ROOT_CAUSE]->(rc)
WITH past, jaccard, fixes, rc.id AS root, c.by AS by, coalesce(rc.id = $top_id, false) AS same_root
WHERE jaccard >= $min_jaccard OR same_root
RETURN past.id AS id, past.title AS title, past.status AS status, jaccard, root, by, fixes, same_root
ORDER BY same_root DESC, jaccard DESC
LIMIT $limit
"""


def record_investigation(store: GraphStore, inv_id: str, incident_id: str, candidates: list, degraded: list[str],
                         algorithm: str, mode: str = "graph") -> None:
    top = candidates[0] if candidates else None
    store.query(Q_RECORD, {
        "id": inv_id, "incident": incident_id, "algorithm": algorithm, "mode": mode,
        "top_id": top.id if top else None, "top_conf": top.confidence if top else None,
        "suspects": [{"id": c.id, "rank": k + 1, "score": c.score, "confidence": c.confidence}
                     for k, c in enumerate(candidates[:3])],
    })
    if degraded:
        store.query(Q_AFFECTED, {"incident": incident_id, "ids": degraded})


def record_feedback(store: GraphStore, incident_id: str, root_id: str, root_label: str, by: str,
                    resolved_action_ids: list[str] | None = None) -> dict[str, Any]:
    if root_label not in ("Service", "Host"):
        raise ValueError("root_label must be Service or Host")
    rows = store.rows(f"""
        MATCH (i:Incident {{id: $incident}}), (n:{root_label} {{id: $root}})
        OPTIONAL MATCH (i)-[old:CONFIRMED_ROOT_CAUSE]->()
        DELETE old
        WITH DISTINCT i, n
        CREATE (i)-[:CONFIRMED_ROOT_CAUSE {{by: $by, at: timestamp()}}]->(n)
        SET i.status = 'RESOLVED'
        RETURN i.id
    """, {"incident": incident_id, "root": root_id, "by": by})
    if not rows:
        raise KeyError("incident or root node not found")
    if resolved_action_ids:
        store.query("""
            UNWIND $ids AS aid MATCH (a:Action {id: aid}) SET a.outcome = 'RESOLVED'
        """, {"ids": resolved_action_ids})
    return {"incident": incident_id, "confirmed_root": root_id, "by": by}


def similar_incidents(store: GraphStore, incident_id: str, top_id: str | None, min_jaccard: float = 0.3,
                      limit: int = 5) -> list[dict]:
    rows = store.rows(Q_SIMILAR, {"id": incident_id, "top_id": top_id or "", "min_jaccard": min_jaccard,
                                  "limit": limit})
    return [{"incident": r[0], "title": r[1], "status": r[2], "jaccard": round(r[3], 3), "confirmed_root": r[4],
             "confirmed_by": r[5], "resolved_by": r[6]} for r in rows]


def history(store: GraphStore, limit: int = 20) -> list[dict]:
    # Aggregate in WITH before the second OPTIONAL MATCH and order by an alias:
    # the single-RETURN form crashed FalkorDB 6.0.2 (see docs/known-issues.md).
    rows = store.rows("""
        MATCH (i:Incident)
        OPTIONAL MATCH (v:Investigation)-[:INVESTIGATES]->(i)
        WITH i, count(v) AS n
        OPTIONAL MATCH (i)-[:CONFIRMED_ROOT_CAUSE]->(rc)
        RETURN i.id AS id, i.title AS title, i.status AS status, i.scenario AS scenario,
               i.opened_at AS opened_at, rc.id AS root, n
        ORDER BY opened_at DESC LIMIT $limit
    """, {"limit": limit})
    return [{"id": r[0], "title": r[1], "status": r[2], "scenario": r[3], "opened_at": r[4],
             "confirmed_root": r[5], "investigations": r[6]} for r in rows]
