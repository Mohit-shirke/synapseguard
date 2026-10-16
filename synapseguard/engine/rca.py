"""Causal Frontier RCA: evidence-ranked root-cause search over FalkorDB.

Pipeline (see docs/algorithm.md for the full write-up and complexity):

1. **Anomalous propagation subgraph** ``A``. Starting at the incident's
   service ``S``, follow *synchronous* ``CALLS`` edges downstream (caller ->
   callee), but only into services that are anomalous or whose telemetry is
   missing. Faults propagate upstream through synchronous calls, so the root
   of the user-visible symptom must be reachable from ``S`` through a chain of
   unhealthy services. Anomalous services that are *not* reachable this way
   (flaky decoys, async consumers) are pruned. Done hop-by-hop in FalkorDB
   (one bounded Cypher query per hop), O(V_A + E_A) work.
2. **Context fetch** for nodes in ``A``: telemetry, host placement, co-located
   services and the change log (deploys, config, feature flags, infra).
3. **Features** per candidate (services in ``A`` with known telemetry, plus
   saturated hosts that run them):

   ========== ===============================================================
   frontier   1 if none of its downstream dependencies in ``A`` is degraded
   coverage   share of degraded services in ``A`` that sit upstream of it
   onset      share of degraded services in ``A`` that degraded no earlier
   change     a change on it in the window before its anomaly onset
   severity   percentile rank of its anomaly among degraded services (service)
              or CPU saturation level (host)
   common     hosts only: several degraded services share this host
   exposed    reachable from the symptom without crossing a call that has a
              fallback (fallbacks absorb downstream failures)
   lag        its nearest degraded callers degraded 0-300 s *after* it, as a
              fault spreading up a synchronous call chain would
   ========== ===============================================================

4. **Rank** by a weighted sum (weights tuned on validation seeds only),
   convert scores to relative confidences with a softmax, and attach the
   evidence (graph paths, timestamps, change records) behind each feature.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from synapseguard.core.graph import GraphStore
from synapseguard.data.scenarios import ANOMALY_THRESHOLD

ALGORITHM_VERSION = "causal-frontier/1.1"
FEATURES = ("frontier", "coverage", "onset", "change", "severity", "common", "exposed", "lag")
# Tuned on validation seeds 0-299 by bench/eval_accuracy.py (coordinate ascent); see
# docs/evaluation.md. Changing these requires re-running the evaluation.
DEFAULT_WEIGHTS: dict[str, float] = {
    "frontier": 0.5, "coverage": 1.0, "onset": 2.0, "change": 2.0, "severity": 0.5, "common": 2.0,
    "exposed": 0.25, "lag": 2.0,
}
# Used when the subgraph carries no change or host evidence (e.g. metrics-only
# telemetry). On real RCAEval RE1 data, the structural weights above did not
# transfer between systems, while pruning + severity improved on both
# (docs/evaluation.md). FalkorDB still does the pruning.
SEVERITY_WEIGHTS: dict[str, float] = {"severity": 1.0}
CHANGE_WINDOW_S = 1800
HOST_SATURATION_CPU = 90.0
SOFTMAX_TEMPERATURE = 0.35
# Callers of a true cause degrade within this many seconds of it (fixed, not tuned).
PROPAGATION_LAG_MAX_S = 300

Q_INCIDENT = """
MATCH (i:Incident {id: $id})-[:REPORTED_ON]->(s:Service)
RETURN i.id, i.title, i.severity, i.opened_at, i.status, s.id
"""

# One hop of the downstream anomalous-subgraph BFS. `WHERE a.id IN $list` is used
# throughout because FalkorDB 6.0.2 plans it as an index scan, while
# `UNWIND $list AS x MATCH (a {id: x})` was planned as a full traverse (GRAPH.EXPLAIN).
# The WITH between MATCH clauses is required: without it FalkorDB 6.0.2 can return
# wrong (empty) results when both MATCHes carry WHERE filters (docs/known-issues.md).
Q_HOP_DOWN = """
MATCH (a:Service) WHERE a.id IN $frontier
WITH a
MATCH (a)-[r:CALLS]->(b:Service)
WHERE r.sync = true AND (b.anomaly IS NULL OR b.anomaly >= $tau)
RETURN a.id, b.id, r.protocol, r.rps, r.has_fallback
"""

# Single-query alternative kept for the benchmark comparison: lets FalkorDB
# enumerate variable-length paths and filter them afterwards.
Q_PATHENUM = """
MATCH p = (s:Service {id: $sid})-[:CALLS*0..%d {sync: true}]->(x:Service)
WHERE all(n IN nodes(p) WHERE n.anomaly IS NULL OR n.anomaly >= $tau)
RETURN DISTINCT x.id
"""

Q_NODES = """
MATCH (s:Service) WHERE s.id IN $ids
OPTIONAL MATCH (s)-[:RUNS_ON]->(h:Host)
RETURN s.id, s.name, s.kind, s.anomaly, s.onset, s.error_rate, s.p99_ms,
       s.base_error_rate, s.base_p99_ms, s.criticality, s.entrypoint, h.id, h.cpu, h.mem
"""

Q_COLOCATED = """
MATCH (h:Host) WHERE h.id IN $hids
WITH h
MATCH (h)<-[:RUNS_ON]-(s:Service)
RETURN h.id, h.cpu, h.mem, collect([s.id, s.anomaly, s.onset])
"""

Q_CHANGES = """
MATCH (c:Change)-[:CHANGED]->(t)
WHERE (t:Service AND t.id IN $sids) OR (t:Host AND t.id IN $hids)
RETURN c.id, c.kind, c.summary, c.at, c.author, c.param, c.old, c.new, t.id, labels(t)[0]
"""


@dataclass
class NodeInfo:
    id: str
    name: str
    kind: str
    anomaly: float | None
    onset: int | None
    error_rate: float | None
    p99_ms: float | None
    base_error_rate: float | None
    base_p99_ms: float | None
    criticality: str | None
    entrypoint: bool
    host: str | None
    host_cpu: float | None

    @property
    def known(self) -> bool:
        return self.anomaly is not None

    @property
    def degraded(self) -> bool:
        return self.anomaly is not None and self.anomaly >= ANOMALY_THRESHOLD


@dataclass
class Subgraph:
    symptom: str
    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)  # src, dst, protocol, rps
    depth: dict[str, int] = field(default_factory=dict)
    parent: dict[str, str] = field(default_factory=dict)
    hosts: dict[str, dict] = field(default_factory=dict)  # hid -> {cpu, mem, services:[(id, anomaly, onset)]}
    changes: list[dict] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)  # nodes on the path with missing telemetry
    timings_ms: dict[str, float] = field(default_factory=dict)
    query_count: int = 0

    def degraded_ids(self) -> list[str]:
        return [n for n, info in self.nodes.items() if info.degraded]


@dataclass
class Candidate:
    id: str
    label: str  # "Service" | "Host"
    name: str
    features: dict[str, float]
    score: float = 0.0
    confidence: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    predicted_recovery: list[str] = field(default_factory=list)


class CausalFrontierRCA:
    def __init__(self, store: GraphStore, *, max_depth: int = 8, tau: float = ANOMALY_THRESHOLD,
                 weights: dict[str, float] | None = None, profile: str = "auto"):
        if profile not in ("auto", "evidence", "severity"):
            raise ValueError("profile must be auto, evidence or severity")
        self.profile = profile
        self.last_profile = profile
        self._rich_context = True
        self.store = store
        self.max_depth = max_depth
        self.tau = tau
        self.weights = dict(weights or DEFAULT_WEIGHTS)

    # ------------------------------------------------------------------ io --
    def incident(self, incident_id: str) -> dict:
        rows = self.store.rows(Q_INCIDENT, {"id": incident_id})
        if not rows:
            raise KeyError(f"incident {incident_id!r} not found")
        iid, title, sev, opened, status, sid = rows[0]
        return {"id": iid, "title": title, "severity": sev, "opened_at": opened, "status": status, "service": sid}

    def extract_subgraph(self, symptom_id: str) -> Subgraph:
        sub = Subgraph(symptom=symptom_id)
        t0 = time.perf_counter()
        sub.depth[symptom_id] = 0
        frontier = [symptom_id]
        edges: list[dict] = []
        for hop in range(1, self.max_depth + 1):
            if not frontier:
                break
            rows = self.store.rows(Q_HOP_DOWN, {"frontier": frontier, "tau": self.tau})
            sub.query_count += 1
            nxt = []
            for a, b, proto, rps, fallback in rows:
                edges.append({"src": a, "dst": b, "protocol": proto, "rps": rps, "has_fallback": bool(fallback)})
                if b not in sub.depth:
                    sub.depth[b] = hop
                    sub.parent[b] = a
                    nxt.append(b)
            frontier = nxt
        sub.timings_ms["traverse"] = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        ids = list(sub.depth)
        for row in self.store.rows(Q_NODES, {"ids": ids}):
            (sid, name, kind, anomaly, onset, err, p99, berr, bp99, crit, entry, hid, hcpu, _hmem) = row
            sub.nodes[sid] = NodeInfo(sid, name, kind, anomaly, onset, err, p99, berr, bp99, crit, bool(entry), hid, hcpu)
        sub.query_count += 1

        # Drop unknown-telemetry nodes that lead to no degraded service: they
        # cannot explain anything and would only widen the search.
        down: dict[str, set[str]] = {n: set() for n in sub.nodes}
        for e in edges:
            if e["src"] in down and e["dst"] in sub.nodes:
                down[e["src"]].add(e["dst"])
        reach_degraded = _reaches(down, {n for n, i in sub.nodes.items() if i.degraded})
        keep = {n for n, i in sub.nodes.items() if i.known or n in reach_degraded or n == symptom_id}
        sub.nodes = {n: i for n, i in sub.nodes.items() if n in keep}
        sub.edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
        sub.blind_spots = sorted(n for n, i in sub.nodes.items() if not i.known)

        hids = sorted({i.host for i in sub.nodes.values() if i.host})
        for hid, cpu, mem, members in self.store.rows(Q_COLOCATED, {"hids": hids}):
            sub.hosts[hid] = {"cpu": cpu, "mem": mem, "services": [tuple(m) for m in members]}
        for row in self.store.rows(Q_CHANGES, {"sids": list(sub.nodes), "hids": hids}):
            cid, kind, summary, at, author, param, old, new, target, label = row
            sub.changes.append({"id": cid, "kind": kind, "summary": summary, "at": at, "author": author,
                                "param": param, "old": old, "new": new, "target": target, "target_label": label})
        sub.query_count += 2
        sub.timings_ms["context"] = (time.perf_counter() - t1) * 1000
        return sub

    def extract_ids_pathenum(self, symptom_id: str) -> set[str]:
        """Benchmark-only: same node set via one variable-length Cypher query."""
        rows = self.store.rows(Q_PATHENUM % self.max_depth, {"sid": symptom_id, "tau": self.tau})
        return {r[0] for r in rows}

    # ------------------------------------------------------------ features --
    def features(self, sub: Subgraph) -> list[Candidate]:
        self._rich_context = bool(sub.changes or sub.hosts)
        down: dict[str, set[str]] = {n: set() for n in sub.nodes}
        up: dict[str, set[str]] = {n: set() for n in sub.nodes}
        for e in sub.edges:
            down[e["src"]].add(e["dst"])
            up[e["dst"]].add(e["src"])
        degraded = [n for n, i in sub.nodes.items() if i.degraded]
        deg_set = set(degraded)
        n_deg = len(degraded)
        denom = max(1, n_deg - 1)
        onsets = {n: sub.nodes[n].onset for n in degraded}

        def onset_share(t: int | None, exclude: set[str]) -> float:
            if t is None:
                return 0.5
            others = [onsets[v] for v in degraded if v not in exclude]
            if not others:
                return 1.0
            return sum(1 for o in others if o is None or o >= t) / len(others)

        anc_cache: dict[str, set[str]] = {}
        desc_cache: dict[str, set[str]] = {}

        def anc(n: str) -> set[str]:
            if n not in anc_cache:
                anc_cache[n] = _closure(up, n)
            return anc_cache[n]

        def desc(n: str) -> set[str]:
            if n not in desc_cache:
                desc_cache[n] = _closure(down, n)
            return desc_cache[n]

        # services reachable from the symptom without crossing a fallback-protected call
        unprotected: dict[str, set[str]] = {n: set() for n in sub.nodes}
        for e in sub.edges:
            if not e.get("has_fallback"):
                unprotected[e["src"]].add(e["dst"])
        exposed = _closure(unprotected, sub.symptom) | {sub.symptom}

        def lag_fit(n: str) -> float:
            t = sub.nodes[n].onset
            callers = _nearest_degraded_up(up, sub.nodes, n)
            first = min((sub.nodes[u].onset for u in callers if sub.nodes[u].onset is not None), default=None)
            if t is None or first is None:
                return 0.5
            return 1.0 if 0 <= first - t <= PROPAGATION_LAG_MAX_S else 0.0

        # severity = percentile rank of anomaly among degraded services (scale-free;
        # raw scores saturate at 1.0 on real telemetry and stop discriminating)
        anomalies = sorted(float(sub.nodes[n].anomaly) for n in degraded)

        def severity_rank(a: float) -> float:
            if len(anomalies) <= 1:
                return 1.0
            below = sum(1 for x in anomalies if x < a)
            equal = sum(1 for x in anomalies if x == a)
            return (below + (equal - 1) / 2) / (len(anomalies) - 1)

        changes_by_target: dict[str, list[dict]] = {}
        for c in sub.changes:
            changes_by_target.setdefault(c["target"], []).append(c)

        cands: list[Candidate] = []
        for n in degraded:
            info = sub.nodes[n]
            ch, _ = best_change(changes_by_target.get(n, []), info.onset)
            cands.append(Candidate(
                id=n, label="Service", name=info.name,
                features={
                    "frontier": 0.0 if (desc(n) & deg_set) else 1.0,
                    "coverage": len(anc(n) & deg_set) / denom,
                    "onset": onset_share(info.onset, {n}),
                    "change": ch,
                    "severity": severity_rank(float(info.anomaly)),
                    "common": 0.0,
                    "exposed": 1.0 if n in exposed else 0.0,
                    "lag": lag_fit(n),
                },
            ))

        for hid, h in sub.hosts.items():
            cpu = h["cpu"] or 0.0
            if cpu < HOST_SATURATION_CPU:
                continue
            victims_all = [s for s in h["services"] if s[1] is not None and s[1] >= self.tau]
            victims_a = [s[0] for s in victims_all if s[0] in deg_set]
            if not victims_a:
                continue
            explained = set(victims_a)
            for v in victims_a:
                explained |= anc(v) & deg_set
            first = min((s[2] for s in victims_all if s[2] is not None), default=None)
            ch, _ = best_change(changes_by_target.get(hid, []), first)
            cands.append(Candidate(
                id=hid, label="Host", name=hid,
                features={
                    "frontier": max(0.0 if (desc(v) & deg_set) else 1.0 for v in victims_a),
                    "coverage": min(1.0, max(0, len(explained) - 1) / denom),
                    "onset": onset_share(first, set()),
                    "change": ch,
                    "severity": max(0.0, min(1.0, (cpu - HOST_SATURATION_CPU) / 10.0)),
                    "common": min(1.0, (len(victims_all) - 1) / 2.0),
                    "exposed": 1.0 if any(v in exposed for v in victims_a) else 0.0,
                    "lag": max(lag_fit(v) for v in victims_a) if _spread(
                        [s[2] for s in victims_all]) <= PROPAGATION_LAG_MAX_S else 0.0,
                },
            ))
        return cands

    # ------------------------------------------------------------- ranking --
    def choose_profile(self, cands: list[Candidate]) -> str:
        """auto: the evidence profile when the graph holds operational context (a change
        log or host placement for the services involved); pruning + severity for
        metrics-only sources, where the structural weights did not transfer on real data."""
        if self.profile != "auto":
            return self.profile
        return "evidence" if self._rich_context else "severity"

    def rank(self, cands: list[Candidate], weights: dict[str, float] | None = None) -> list[Candidate]:
        if weights is None:
            self.last_profile = self.choose_profile(cands)
            weights = self.weights if self.last_profile == "evidence" else SEVERITY_WEIGHTS
        w = weights
        for c in cands:
            c.score = round(sum(w.get(f, 0.0) * c.features.get(f, 0.0) for f in FEATURES), 4)
        cands.sort(key=lambda c: (-c.score, c.id))
        if cands:
            m = max(c.score for c in cands)
            exps = [math.exp((c.score - m) / SOFTMAX_TEMPERATURE) for c in cands]
            z = sum(exps)
            for c, e in zip(cands, exps):
                c.confidence = round(e / z, 4)
        return cands

    # ------------------------------------------------------------ evidence --
    def explain(self, sub: Subgraph, cand: Candidate) -> Candidate:
        """Attach graph-grounded evidence. Every item cites node ids."""
        ev: list[dict] = []
        targets = [cand.id] if cand.label == "Service" else [
            s[0] for s in sub.hosts[cand.id]["services"] if s[0] in sub.nodes and sub.nodes[s[0]].degraded]
        anchor = min(targets, key=lambda t: sub.depth.get(t, 99))
        path = _path_to(sub.parent, sub.symptom, anchor)
        cand.path = path + ([cand.id] if cand.label == "Host" else [])
        for a, b in zip(path, path[1:]):
            na, nb = sub.nodes[a], sub.nodes[b]
            ev.append({
                "kind": "call_path",
                "statement": f"{na.name} calls {nb.name} synchronously; {_state(na)} -> {_state(nb)}",
                "refs": [a, b],
            })
        for b in sub.blind_spots:
            if b in path:
                ev.append({"kind": "blind_spot", "refs": [b],
                           "statement": f"No telemetry for {sub.nodes[b].name}; traversed through it as unknown"})
        f = cand.features
        if cand.label == "Service":
            info = sub.nodes[cand.id]
            ev.append({"kind": "telemetry", "refs": [cand.id], "statement":
                       f"{info.name}: error rate {_pct(info.error_rate)} (baseline {_pct(info.base_error_rate)}), "
                       f"p99 {info.p99_ms} ms (baseline {info.base_p99_ms} ms), anomaly {info.anomaly}"})
            deg_down = [e["dst"] for e in sub.edges if e["src"] == cand.id and sub.nodes[e["dst"]].degraded]
            ev.append({"kind": "frontier", "refs": [cand.id] + deg_down, "statement":
                       (f"None of {info.name}'s dependencies are degraded, so nothing downstream explains it"
                        if f["frontier"] else
                        f"{info.name} has degraded dependencies ({', '.join(deg_down) or 'via unknown nodes'}); "
                        "it may be a victim rather than the origin")})
            onset_ref = info.onset
        else:
            h = sub.hosts[cand.id]
            victims = [s for s in h["services"] if s[1] is not None and s[1] >= self.tau]
            ev.append({"kind": "host", "refs": [cand.id] + [v[0] for v in victims], "statement":
                       f"Host {cand.id} at {h['cpu']}% CPU / {h['mem']}% memory runs {len(victims)} degraded "
                       f"services: {', '.join(v[0] for v in victims)}"})
            onset_ref = min((v[2] for v in victims if v[2] is not None), default=None)
        if onset_ref is not None:
            ev.append({"kind": "onset", "refs": [cand.id], "statement":
                       f"Degraded no later than {round(f['onset'] * 100)}% of the other impacted services "
                       f"(onset t={onset_ref})"})
        ch_score, ch = best_change([c for c in sub.changes if c["target"] == cand.id], onset_ref)
        if ch:
            dt = (onset_ref or 0) - ch["at"]
            ev.append({"kind": "change", "refs": [ch["id"], cand.id], "statement":
                       f"Change {ch['id']} ({ch['kind']}: {ch['summary']}) by {ch['author']} "
                       f"landed {_dur(dt)} before the anomaly began"})
        if f["coverage"]:
            ev.append({"kind": "coverage", "refs": [cand.id], "statement":
                       f"Explains {round(f['coverage'] * 100)}% of the other degraded services on the "
                       "propagation path (they depend on it, directly or transitively)"})
        cand.evidence = ev
        cand.predicted_recovery = predicted_recovery(sub, cand)
        return cand

    # ------------------------------------------------------------- facade --
    def investigate(self, incident_id: str, top_k: int = 3) -> dict[str, Any]:
        t0 = time.perf_counter()
        inc = self.incident(incident_id)
        sub = self.extract_subgraph(inc["service"])
        cands = self.rank(self.features(sub))
        for c in cands[:top_k]:
            self.explain(sub, c)
        total = (time.perf_counter() - t0) * 1000
        abstain = None
        if not cands:
            abstain = ("The reported service shows no anomaly in its telemetry and nothing degraded is "
                       "reachable from it; no root cause can be supported by the graph.")
        return {
            "incident": inc,
            "subgraph": sub,
            "candidates": cands,
            "abstain_reason": abstain,
            "timings_ms": {**sub.timings_ms, "total": total},
            "query_count": sub.query_count,
            "algorithm": ALGORITHM_VERSION,
            "profile": self.last_profile,
            "weights": dict(self.weights),
        }


# ------------------------------------------------------------------ helpers --
def best_change(changes: list[dict], onset: int | None) -> tuple[float, dict | None]:
    """Score the most incriminating change before ``onset``.

    Changes after the onset (+60 s clock-skew allowance) cannot be the cause and
    score 0; changes in the 30-minute window before it score 0.5-1.0, closer is
    stronger.
    """
    if onset is None:
        return 0.0, None
    best, best_c = 0.0, None
    for c in changes:
        dt = onset - c["at"]
        if dt < -60 or dt > CHANGE_WINDOW_S:
            continue
        s = 0.8 if dt < 0 else 1.0 - 0.5 * dt / CHANGE_WINDOW_S
        if s > best:
            best, best_c = s, c
    return round(best, 4), best_c


def predicted_recovery(sub: Subgraph, cand: Candidate) -> list[str]:
    """Degraded services expected to recover if ``cand`` is fixed.

    A degraded service recovers if every degraded service it (transitively)
    depends on is either fixed or itself recovers, i.e. it has no path to a
    different degraded frontier service.
    """
    down: dict[str, set[str]] = {n: set() for n in sub.nodes}
    for e in sub.edges:
        down[e["src"]].add(e["dst"])
    fixed = {cand.id} if cand.label == "Service" else {
        s[0] for s in sub.hosts[cand.id]["services"] if s[0] in sub.nodes}
    degraded = {n for n, i in sub.nodes.items() if i.degraded}
    other_sources = {n for n in degraded if n not in fixed and not (_closure(down, n) & degraded)}
    still_bad = _reaches(down, other_sources) | other_sources
    return sorted(n for n in degraded if n not in still_bad)


def _closure(adj: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = list(adj.get(start, ()))
    while stack:
        n = stack.pop()
        if n in seen or n == start:
            continue
        seen.add(n)
        stack.extend(adj.get(n, ()))
    return seen


def _reaches(down: dict[str, set[str]], targets: set[str]) -> set[str]:
    """Nodes with a downstream path to any of ``targets``."""
    up: dict[str, set[str]] = {}
    for a, bs in down.items():
        for b in bs:
            up.setdefault(b, set()).add(a)
    seen: set[str] = set()
    stack = [p for t in targets for p in up.get(t, ())]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(up.get(n, ()))
    return seen


def _nearest_degraded_up(up: dict[str, set[str]], nodes: dict[str, NodeInfo], n: str) -> set[str]:
    """Closest degraded callers of ``n``, looking through unknown-telemetry nodes."""
    out: set[str] = set()
    seen: set[str] = set()
    stack = list(up.get(n, ()))
    while stack:
        u = stack.pop()
        if u in seen or u == n:
            continue
        seen.add(u)
        if nodes[u].degraded:
            out.add(u)
        elif not nodes[u].known:
            stack.extend(up.get(u, ()))
    return out


def _spread(ts: list[int | None]) -> float:
    ts = [t for t in ts if t is not None]
    return (max(ts) - min(ts)) if ts else 0.0


def _path_to(parent: dict[str, str], root: str, node: str) -> list[str]:
    path = [node]
    while path[-1] != root and path[-1] in parent:
        path.append(parent[path[-1]])
    return list(reversed(path))


def _state(n: NodeInfo) -> str:
    return "telemetry missing" if n.anomaly is None else ("degraded" if n.degraded else "healthy")


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _dur(seconds: int) -> str:
    if seconds < 0:
        return f"{-seconds}s after (within clock-skew allowance)"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"
