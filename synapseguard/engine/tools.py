"""The agent's fixed tool catalogue.

A person wrote every query here; models (via MCP or the built-in LLM agent)
only choose a tool and fill typed arguments. Every name argument is resolved
against the graph before any traversal runs, and every result carries one of
four outcomes, with the graph paths that support it:

    answered_with_path    the graph held the relationships; paths are returned
    answered_from_memory  a past investigation or human confirmation answers it
    not_in_graph          the entity exists but nothing connects it as asked
    unknown_entity        the name does not resolve; nothing else runs
"""
from __future__ import annotations

import difflib
import re
import time
from typing import Any

from synapseguard.core.graph import GraphStore

OUTCOMES = ("answered_with_path", "answered_from_memory", "not_in_graph", "unknown_entity")
MAX_DEPTH = 6


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Tools:
    def __init__(self, store: GraphStore):
        self.store = store
        self.trace: list[dict] = []

    # ------------------------------------------------------------ plumbing --
    def _envelope(self, tool: str, args: dict, outcome: str, t0: float, **kw) -> dict:
        assert outcome in OUTCOMES
        rec = {"tool": tool, "args": args, "outcome": outcome, "ms": round((time.perf_counter() - t0) * 1000, 2),
               "rows": kw.pop("rows", None)}
        self.trace.append(rec)
        return {"outcome": outcome, "tool": tool, "args": args, **kw, "ms": rec["ms"]}

    def now(self) -> int:
        """Reference time: the open incident's time if there is one, else wall clock."""
        rows = self.store.rows("MATCH (i:Incident {status: 'OPEN'}) RETURN max(i.opened_at)")
        return int(rows[0][0]) if rows and rows[0][0] else int(time.time())

    def resolve(self, name: str, labels: tuple[str, ...] = ("Service", "Host")) -> dict:
        """Match a user/model-supplied name to a node id with bounded, index-friendly queries:
        exact id, then case-insensitive / separator-insensitive id or name, then a unique
        substring. Anything else is unknown, with suggestions. Never loads the whole graph
        (FalkorDB caps result sets at RESULTSET_SIZE, 10,000 rows by default)."""
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            return {"status": "unknown", "query": name, "suggestions": []}
        q = name.strip()
        lq, nq = q.lower(), _norm(q)
        for label in labels:  # 1. exact id via the index
            rows = self.store.ro_query(f"MATCH (n:{label}) WHERE n.id = $q RETURN n.id, coalesce(n.name, n.id)",
                                       {"q": q}).result_set
            if rows:
                return {"status": "found", "id": rows[0][0], "label": label, "name": rows[0][1], "query": name,
                        "exact": True}
        norm = "replace(replace(replace(toLower({x}), '-', ''), ' ', ''), '_', '')"
        hits: list = []
        for label in labels:  # 2. case/separator-insensitive id or name
            hits += [r + [label] for r in self.store.ro_query(
                f"MATCH (n:{label}) WHERE toLower(n.id) = $lq OR toLower(n.name) = $lq "
                f"OR {norm.format(x='n.id')} = $nq OR {norm.format(x='n.name')} = $nq "
                "RETURN n.id, coalesce(n.name, n.id) LIMIT 3", {"lq": lq, "nq": nq}).result_set]
        if len(hits) == 1:
            rid, rname, label = hits[0]
            return {"status": "found", "id": rid, "label": label, "name": rname, "query": name, "exact": False}
        if not hits and len(nq) >= 4:  # 3. unique substring
            for label in labels:
                hits += [r + [label] for r in self.store.ro_query(
                    f"MATCH (n:{label}) WHERE {norm.format(x='n.id')} CONTAINS $nq "
                    f"OR {norm.format(x='n.name')} CONTAINS $nq RETURN n.id, coalesce(n.name, n.id) LIMIT 6",
                    {"nq": nq}).result_set]
            if len(hits) == 1:
                rid, rname, label = hits[0]
                return {"status": "found", "id": rid, "label": label, "name": rname, "query": name, "exact": False}
        if hits:
            return {"status": "ambiguous", "query": name, "suggestions": [h[0] for h in hits][:5]}
        pool: list[str] = []  # 4. suggestions from names sharing a short prefix
        for label in labels:
            pool += [r[0] for r in self.store.ro_query(
                f"MATCH (n:{label}) WHERE toLower(n.id) STARTS WITH $p RETURN n.id LIMIT 200",
                {"p": lq[:2]}).result_set]
        return {"status": "unknown", "query": name,
                "suggestions": difflib.get_close_matches(lq, pool, n=3, cutoff=0.5)}

    def _unknown(self, tool: str, args: dict, t0: float, res: dict) -> dict:
        why = (f"'{res['query']}' matches several entities: {', '.join(res['suggestions'])}"
               if res["status"] == "ambiguous" else
               f"'{res['query']}' is not in the graph" + (f"; did you mean {', '.join(res['suggestions'])}?"
                                                           if res["suggestions"] else ""))
        return self._envelope(tool, args, "unknown_entity", t0, answer=why, resolved=res, rows=0)

    # --------------------------------------------------------------- tools --
    def blast_radius(self, service: str, depth: int = 3) -> dict:
        """Everything that (transitively) calls `service`, with the hop path for each."""
        t0, args = time.perf_counter(), {"service": service, "depth": depth}
        res = self.resolve(service)
        if res["status"] != "found":
            return self._unknown("blast_radius", args, t0, res)
        depth = max(1, min(int(depth), MAX_DEPTH))
        roots = [res["id"]] if res["label"] == "Service" else [
            r[0] for r in self.store.rows("MATCH (:Host {id: $id})<-[:RUNS_ON]-(s:Service) RETURN s.id", {"id": res["id"]})]
        parent: dict[str, str] = {}
        hops = {r: 0 for r in roots}
        sync = {r: True for r in roots}
        frontier = list(roots)
        for hop in range(1, depth + 1):
            if not frontier:
                break
            rows = self.store.rows("""
                MATCH (b:Service) WHERE b.id IN $f
                WITH b
                MATCH (a:Service)-[r:CALLS]->(b)
                RETURN a.id, b.id, r.sync""", {"f": frontier})
            nxt = []
            for a, b, s in rows:
                if a not in hops:
                    hops[a], parent[a], sync[a] = hop, b, bool(s) and sync.get(b, True)
                    nxt.append(a)
            frontier = nxt
        impacted = sorted((n for n in hops if n not in roots), key=lambda n: (hops[n], n))
        paths = []
        for n in impacted:
            p = [n]
            while p[-1] in parent:
                p.append(parent[p[-1]])
            paths.append({"service": n, "hops": hops[n], "sync": sync[n], "via": p})
        if not impacted:
            return self._envelope("blast_radius", args, "not_in_graph", t0, resolved=res, paths=[], rows=0,
                                  answer=f"Nothing in the graph calls {res['id']}. That means no dependency is "
                                         "modelled, not necessarily that nothing breaks.")
        return self._envelope("blast_radius", args, "answered_with_path", t0, resolved=res, paths=paths,
                              rows=len(paths), answer=f"{len(paths)} services depend on {res['id']} within "
                                                     f"{depth} hops: " + ", ".join(p["service"] for p in paths))

    def who_to_page(self, service: str, at: int | None = None) -> dict:
        """On-call owner, recent change authors and people with history on a service or host."""
        t0, args = time.perf_counter(), {"service": service}
        res = self.resolve(service)
        if res["status"] != "found":
            return self._unknown("who_to_page", args, t0, res)
        now = at or self.now()
        label, nid = res["label"], res["id"]
        people: list[dict] = []
        for team, tname, person, pname in self.store.rows(f"""
                MATCH (t:Team)-[:OWNS]->(x:{label} {{id: $id}})
                OPTIONAL MATCH (p:Person)-[o:ON_CALL]->(t) WHERE o.from <= $now AND o.to > $now
                RETURN t.id, t.name, p.id, p.name""", {"id": nid, "now": now}):
            if person:
                people.append({"person": person, "name": pname, "role": "on-call owner", "priority": 1,
                               "path": [person, "ON_CALL", team, "OWNS", nid],
                               "reason": f"on call for {tname}, which owns {nid}"})
        for person, pname, cid, summary, cat in self.store.rows(f"""
                MATCH (p:Person)-[:AUTHORED]->(c:Change)-[:CHANGED]->(x:{label} {{id: $id}})
                WHERE c.at <= $now AND c.at >= $now - 7200
                RETURN p.id, p.name, c.id, c.summary, c.at ORDER BY c.at DESC""", {"id": nid, "now": now}):
            people.append({"person": person, "name": pname, "role": "change author", "priority": 2,
                           "path": [person, "AUTHORED", cid, "CHANGED", nid],
                           "reason": f"authored {cid} ({summary}) {round((now - cat) / 60)} min before"})
        if label == "Service":
            for person, pname, commits in self.store.rows("""
                    MATCH (p:Person)-[w:WORKED_ON]->(:Service {id: $id})
                    RETURN p.id, p.name, w.commits ORDER BY w.commits DESC LIMIT 2""", {"id": nid}):
                people.append({"person": person, "name": pname, "role": "knows this service", "priority": 3,
                               "path": [person, "WORKED_ON", nid], "reason": f"{commits} commits on {nid}"})
        if not people:
            return self._envelope("who_to_page", args, "not_in_graph", t0, resolved=res, people=[], rows=0,
                                  answer=f"No owner, on-call rotation or recent author is recorded for {nid}.")
        people.sort(key=lambda p: p["priority"])
        roles: dict[str, list[str]] = {}
        for p in people:
            roles.setdefault(p["name"], []).append(p["role"])
        return self._envelope("who_to_page", args, "answered_with_path", t0, resolved=res, people=people,
                              rows=len(people), answer="Page " + "; ".join(
                                  f"{n} ({', '.join(r)})" for n, r in list(roles.items())[:3]))

    def recent_changes(self, service: str, hours: int = 2, depth: int = 2, at: int | None = None) -> dict:
        """Changes in the last `hours` on a service and on what it calls (a change below can break you)."""
        t0, args = time.perf_counter(), {"service": service, "hours": hours, "depth": depth}
        res = self.resolve(service)
        if res["status"] != "found":
            return self._unknown("recent_changes", args, t0, res)
        now, hours, depth = at or self.now(), max(1, min(int(hours), 168)), max(0, min(int(depth), 4))
        ids, parent, frontier = {res["id"]}, {}, [res["id"]]
        for _ in range(depth if res["label"] == "Service" else 0):
            rows = self.store.rows("""
                MATCH (a:Service) WHERE a.id IN $f
                WITH a
                MATCH (a)-[:CALLS]->(b:Service) RETURN a.id, b.id""", {"f": frontier})
            frontier = []
            for a, b in rows:
                if b not in ids:
                    ids.add(b)
                    parent[b] = a
                    frontier.append(b)
        rows = self.store.rows("""
            MATCH (c:Change)-[:CHANGED]->(t) WHERE t.id IN $ids AND c.at <= $now AND c.at >= $since
            OPTIONAL MATCH (p:Person)-[:AUTHORED]->(c)
            RETURN c.id, c.kind, c.summary, c.at, c.author, p.name, t.id ORDER BY c.at DESC""",
                               {"ids": sorted(ids), "now": now, "since": now - hours * 3600})
        changes = []
        for cid, kind, summary, cat, author, pname, target in rows:
            via = [target]
            while via[-1] in parent:
                via.append(parent[via[-1]])
            changes.append({"change": cid, "kind": kind, "summary": summary, "minutes_ago": round((now - cat) / 60),
                            "author": pname or author, "target": target, "via": list(reversed(via))})
        if not changes:
            return self._envelope("recent_changes", args, "not_in_graph", t0, resolved=res, changes=[], rows=0,
                                  answer=f"No change recorded on {res['id']} or its dependencies in the last {hours} h.")
        return self._envelope("recent_changes", args, "answered_with_path", t0, resolved=res, changes=changes,
                              rows=len(changes), answer=f"{len(changes)} changes: " + "; ".join(
                                  f"{c['change']} on {c['target']} ({c['summary']}, {c['minutes_ago']} min ago)"
                                  for c in changes[:4]))

    def path_between(self, source: str, target: str) -> dict:
        """Why are two services connected? Cheapest call path (sync calls weigh 1, async 3) via algo.SPpaths."""
        t0, args = time.perf_counter(), {"source": source, "target": target}
        a, b = self.resolve(source, ("Service",)), self.resolve(target, ("Service",))
        for r in (a, b):
            if r["status"] != "found":
                return self._unknown("path_between", args, t0, r)
        q = """
            MATCH (a:Service {id: $a}), (b:Service {id: $b})
            CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['CALLS'], relDirection: 'outgoing',
                               weightProp: 'weight', pathCount: 1})
            YIELD path, pathWeight
            RETURN [n IN nodes(path) | n.id], pathWeight"""
        for x, y, direction in ((a["id"], b["id"], "calls"), (b["id"], a["id"], "is called by")):
            rows = self.store.rows(q, {"a": x, "b": y})
            if rows:
                hops, weight = rows[0]
                return self._envelope("path_between", args, "answered_with_path", t0, path=hops, weight=weight,
                                      rows=1, answer=f"{x} depends on {y}: " + " -> ".join(hops))
        return self._envelope("path_between", args, "not_in_graph", t0, path=[], rows=0,
                              answer=f"No call path connects {a['id']} and {b['id']} in either direction.")

    def load_bearing(self, limit: int = 5) -> dict:
        """Services the most others depend on (PageRank over CALLS), with their direct dependents."""
        t0, args = time.perf_counter(), {"limit": limit}
        limit = max(1, min(int(limit), 20))
        rows = self.store.rows("""
            CALL algo.pageRank('Service', 'CALLS') YIELD node, score
            WITH node, score ORDER BY score DESC LIMIT $limit
            OPTIONAL MATCH (c:Service)-[:CALLS]->(node)
            RETURN node.id, score, collect(c.id)""", {"limit": limit})
        ranked = [{"service": s, "pagerank": round(sc, 4), "called_by": sorted(cb)} for s, sc, cb in rows]
        return self._envelope("load_bearing", args, "answered_with_path", t0, services=ranked, rows=len(ranked),
                              answer="Most load-bearing: " + ", ".join(r["service"] for r in ranked))

    def recall(self, service: str) -> dict:
        """What past investigations and humans established about a service or host, with dates."""
        t0, args = time.perf_counter(), {"service": service}
        res = self.resolve(service)
        if res["status"] != "found":
            return self._unknown("recall", args, t0, res)
        rows = self.store.rows("""
            MATCH (i:Incident)-[c:CONFIRMED_ROOT_CAUSE]->(n {id: $id})
            RETURN i.id, i.title, c.by, c.at ORDER BY c.at DESC LIMIT 5""", {"id": res["id"]})
        affected = self.store.rows("""
            MATCH (i:Incident)-[:AFFECTED]->(:Service {id: $id})
            RETURN count(DISTINCT i)""", {"id": res["id"]}) if res["label"] == "Service" else [[0]]
        memories = [{"incident": i, "title": t, "confirmed_by": by, "confirmed_at_ms": at} for i, t, by, at in rows]
        n_aff = affected[0][0] if affected else 0
        if not memories and not n_aff:
            return self._envelope("recall", args, "not_in_graph", t0, resolved=res, memories=[], rows=0,
                                  answer=f"No past investigation involved {res['id']}.")
        return self._envelope("recall", args, "answered_from_memory", t0, resolved=res, memories=memories,
                              incidents_affected=n_aff, rows=len(memories),
                              answer=f"{res['id']} was the confirmed root cause of {len(memories)} past incident(s) "
                                     f"and affected in {n_aff}.")

    def list_incidents(self, limit: int = 10) -> dict:
        from synapseguard.engine import recall as M
        t0, args = time.perf_counter(), {"limit": limit}
        rows = M.history(self.store, max(1, min(int(limit), 50)))
        return self._envelope("list_incidents", args, "answered_from_memory" if rows else "not_in_graph", t0,
                              incidents=rows, rows=len(rows),
                              answer=f"{sum(r['status'] == 'OPEN' for r in rows)} open, {len(rows)} total")


CATALOGUE = {
    "list_incidents": {"description": "Recent incidents and their status.", "params": {"limit": "integer"}},
    "blast_radius": {"description": "What breaks if a service or host goes down: everything that transitively "
                                    "calls it, with the hop path for each.",
                     "params": {"service": "string", "depth": "integer (1-6, default 3)"}, "required": ["service"]},
    "who_to_page": {"description": "Who to wake for a service or host: on-call owner, recent change authors, "
                                   "people with history on it.", "params": {"service": "string"},
                    "required": ["service"]},
    "recent_changes": {"description": "Deploys, config and flag changes in the last hours on a service and on "
                                      "what it calls.", "params": {"service": "string", "hours": "integer",
                                                                   "depth": "integer"}, "required": ["service"]},
    "path_between": {"description": "Why two services are connected: the cheapest call path between them.",
                     "params": {"source": "string", "target": "string"}, "required": ["source", "target"]},
    "load_bearing": {"description": "The services most others depend on (PageRank).", "params": {"limit": "integer"}},
    "recall": {"description": "What past investigations and humans established about a service or host.",
               "params": {"service": "string"}, "required": ["service"]},
}


def existing_entities(store: GraphStore, text: str) -> set[str]:
    """Ids/names of graph entities that a piece of text mentions, found with bounded queries
    over the text's own tokens (1-3 word windows) instead of loading every node name."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9._\-]*", text or "")
    grams = {w for w in words if len(w) >= 3}
    grams |= {" ".join(words[i:i + k]) for k in (2, 3) for i in range(len(words) - k + 1)}
    if not grams:
        return set()
    lowered = sorted({g.lower() for g in grams})
    out: set[str] = set()
    for label in ("Service", "Host", "Change", "Person", "Team"):
        for rid, rname in store.ro_query(
                f"MATCH (n:{label}) WHERE n.id IN $ids OR toLower(n.id) IN $low OR toLower(n.name) IN $low "
                "RETURN n.id, n.name LIMIT 500", {"ids": sorted(grams), "low": lowered}).result_set:
            out.update(x for x in (rid, rname) if x)
    return out
