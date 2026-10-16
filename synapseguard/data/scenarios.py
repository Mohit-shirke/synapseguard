"""Seeded incident-scenario generator with ground truth.

A scenario is a snapshot of the system during an incident: per-service
telemetry (error rate, p99 latency, anomaly onset), host saturation, a change
log, and the incident report. The ground-truth root cause is kept in
``Scenario.truth`` and is **never** written to the graph, so no method can
read the answer.

Fault model (simplified, documented in docs/evaluation.md):

* A fault starts at a root (a service, or a host for saturation faults) at T0.
* It propagates to *callers* over synchronous call edges with probability
  ``p_prop`` (lower when the caller has a fallback), each hop adding a delay
  to the anomaly onset. Asynchronous edges do not propagate.
* Distractors: unrelated flaky services, benign changes (some deliberately
  timed close to T0), a decoy hot host, and missing telemetry on part of the
  causal chain.
"""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field

from synapseguard.data.topology import BASE_P99, OTEL_FLAGS, Topology

ANOMALY_THRESHOLD = 0.2  # anomaly >= 0.2  <=>  error +2pp or p99 doubled
FAULT_KINDS = ["deploy_bug", "config_change", "feature_flag", "host_saturation", "silent", "retry_storm"]
FAULT_WEIGHTS = [0.22, 0.18, 0.15, 0.15, 0.18, 0.12]
EPOCH0 = 1_792_000_000  # 2026-10-15T~UTC; fixed so runs are reproducible


def anomaly_score(error_rate: float | None, base_error: float, p99: float | None, base_p99: float) -> float | None:
    """Map raw telemetry to [0, 1]. ``None`` means telemetry is missing.

    s = max(error excess / 2pp, log2(p99 / baseline)); anomaly = min(1, s / 5).
    """
    if error_rate is None or p99 is None:
        return None
    s = max(max(0.0, error_rate - base_error) / 0.02, math.log2(max(p99, 1e-9) / base_p99))
    return round(max(0.0, min(1.0, s / 5.0)), 4)


@dataclass
class Scenario:
    name: str
    seed: int
    description: str
    services: list[dict]
    edges: list[dict]
    hosts: list[dict]
    changes: list[dict]
    incident: dict
    truth: dict = field(default_factory=dict)
    org: dict | None = None  # people layer; built by the loader when absent

    def anomalous_ids(self) -> list[str]:
        return [s["id"] for s in self.services if (s["anomaly"] or 0) >= ANOMALY_THRESHOLD]


def _pick_weighted(rng: random.Random, items, weights):
    return rng.choices(items, weights=weights, k=1)[0]


def generate(
    topology: Topology,
    seed: int,
    fault_kind: str | None = None,
    root: str | None = None,
    *,
    p_prop: float = 0.9,
    n_decoys: tuple[int, int] = (1, 4),
    n_noise_changes: tuple[int, int] = (2, 6),
    p_missing_telemetry: float = 0.3,
    p_decoy_hot_host: float = 0.2,
    red_herring: str | None = None,
    name: str | None = None,
    description: str = "",
) -> Scenario:
    rng = random.Random(seed)
    services = {s["id"]: copy.deepcopy(s) for s in topology.services}
    hosts = {h["id"]: copy.deepcopy(h) for h in topology.hosts}
    edges = copy.deepcopy(topology.edges)
    callers: dict[str, list[dict]] = {sid: [] for sid in services}
    callees: dict[str, list[dict]] = {sid: [] for sid in services}
    for e in edges:
        callers[e["dst"]].append(e)
        callees[e["src"]].append(e)
    by_host: dict[str, list[str]] = {h: [] for h in hosts}
    for s in services.values():
        by_host[s["host"]].append(s["id"])

    t0 = EPOCH0 + seed * 10_000
    for s in services.values():
        s["base_error_rate"] = round(rng.uniform(0.001, 0.01), 4)
        s["base_p99_ms"] = round(BASE_P99.get(s["kind"], 60.0) * rng.uniform(0.7, 1.4), 2)
    for h in hosts.values():
        h["cpu"] = round(rng.uniform(20, 70), 1)
        h["mem"] = round(rng.uniform(30, 75), 1)

    fault_kind = fault_kind or _pick_weighted(rng, FAULT_KINDS, FAULT_WEIGHTS)
    has_caller = [sid for sid in services if any(e["sync"] for e in callers[sid])]
    magnitude: dict[str, float] = {}
    onset: dict[str, int] = {}

    # ---- 1. inject the fault -------------------------------------------------
    truth: dict
    if fault_kind == "host_saturation":
        multi = [h for h, members in by_host.items() if len([m for m in members if m in has_caller]) >= 2]
        host = root if root in hosts else rng.choice(multi)
        hosts[host]["cpu"] = round(rng.uniform(94, 99.5), 1)
        hosts[host]["mem"] = round(rng.uniform(80, 97), 1)
        victims = [m for m in by_host[host] if m in has_caller] or by_host[host]
        for m in victims:
            magnitude[m] = rng.uniform(0.5, 1.0)
            onset[m] = t0 + rng.randint(0, 40)
        truth = {"root_id": host, "root_label": "Host", "fault_kind": fault_kind}
    elif fault_kind == "retry_storm":
        candidates = [s for s in has_caller if any(e["sync"] for e in callees[s])]
        r = root if root in services else rng.choice(candidates)
        callee = rng.choice([e["dst"] for e in callees[r] if e["sync"]])
        magnitude[r] = rng.uniform(0.5, 0.9)
        onset[r] = t0
        magnitude[callee] = rng.uniform(0.7, 1.0)  # overloaded by retries
        onset[callee] = t0 + rng.randint(5, 40)
        truth = {"root_id": r, "root_label": "Service", "fault_kind": fault_kind, "overloaded": callee}
    else:
        r = root if root in services else rng.choice(has_caller)
        magnitude[r] = rng.uniform(0.6, 1.0)
        onset[r] = t0
        truth = {"root_id": r, "root_label": "Service", "fault_kind": fault_kind}

    # ---- 2. propagate to callers over sync edges ----------------------------
    frontier = list(magnitude)
    while frontier:
        nxt = []
        for v in frontier:
            for e in callers[v]:
                u = e["src"]
                if not e["sync"] or u in magnitude:
                    continue
                p = p_prop * (0.4 if e["has_fallback"] else 1.0)
                if rng.random() < p:
                    magnitude[u] = max(0.3, magnitude[v] * rng.uniform(0.65, 0.95))
                    onset[u] = onset[v] + rng.randint(15, 90)
                    nxt.append(u)
        frontier = nxt
    causal = set(magnitude)

    # ---- 3. distractors -----------------------------------------------------
    healthy = [s for s in services if s not in magnitude]
    decoys = rng.sample(healthy, min(len(healthy), rng.randint(*n_decoys)))
    for d in decoys:
        magnitude[d] = rng.uniform(0.25, 0.7)
        onset[d] = t0 + rng.randint(-1800, 600)
    if rng.random() < p_decoy_hot_host:
        other = [h for h in hosts if h != truth["root_id"]]
        if other:
            hosts[rng.choice(other)]["cpu"] = round(rng.uniform(91, 96), 1)

    # ---- 4. materialise telemetry from fault magnitude ----------------------
    for sid, s in services.items():
        m = magnitude.get(sid)
        if m is None:
            s["error_rate"] = round(s["base_error_rate"] * rng.uniform(0.5, 1.5), 4)
            s["p99_ms"] = round(s["base_p99_ms"] * rng.uniform(0.85, 1.4), 2)
            s["onset"] = None
        else:
            sig = rng.random()
            err = s["base_error_rate"] + (m * rng.uniform(0.06, 0.5) if sig < 0.6 else rng.uniform(0, 0.01))
            p99 = s["base_p99_ms"] * ((1 + m * rng.uniform(3, 30)) if sig >= 0.3 else rng.uniform(0.9, 1.3))
            s["error_rate"] = round(min(err, 1.0), 4)
            s["p99_ms"] = round(p99, 2)
            s["onset"] = onset[sid]
        s["anomaly"] = anomaly_score(s["error_rate"], s["base_error_rate"], s["p99_ms"], s["base_p99_ms"])
        if m is not None and (s["anomaly"] or 0) < ANOMALY_THRESHOLD:  # keep injected faults visible
            s["error_rate"] = round(s["base_error_rate"] + 0.03 + 0.1 * m, 4)
            s["anomaly"] = anomaly_score(s["error_rate"], s["base_error_rate"], s["p99_ms"], s["base_p99_ms"])
        if s["anomaly"] is not None and s["anomaly"] < ANOMALY_THRESHOLD:
            s["onset"] = None

    # ---- 5. incident: the most upstream impacted service --------------------
    anomalous_causal = sorted(causal)  # sorted: set order varies with PYTHONHASHSEED
    entry_hit = [s for s in anomalous_causal if services[s]["entrypoint"]]
    if entry_hit:
        symptom = min(entry_hit)
    else:
        def upstream_depth(x: str) -> int:
            return onset[x]
        tops = [s for s in anomalous_causal if not any(e["src"] in causal and e["sync"] for e in callers[s])]
        symptom = max(tops or anomalous_causal, key=upstream_depth)
    opened_at = onset[symptom] + rng.randint(60, 180)

    # ---- 6. missing telemetry on an intermediate causal node ----------------
    if rng.random() < p_missing_telemetry:
        middle = [s for s in sorted(causal) if s not in (truth["root_id"], symptom) and services.get(s)
                  and truth.get("overloaded") != s]
        if middle:
            m = rng.choice(middle)
            services[m].update({"error_rate": None, "p99_ms": None, "anomaly": None, "onset": None})

    # ---- 7. change log ------------------------------------------------------
    changes: list[dict] = []
    cid = 0

    def add_change(kind: str, target: str, label: str, at: int, summary: str, **extra) -> None:
        nonlocal cid
        cid += 1
        changes.append({"id": f"chg-{seed}-{cid:02d}", "kind": kind, "target": target, "target_label": label,
                        "at": at, "summary": summary, "author": extra.pop("author", rng.choice(AUTHORS)),
                        "param": extra.get("param"), "old": extra.get("old"), "new": extra.get("new")})

    rid = truth["root_id"]
    if fault_kind in ("deploy_bug", "retry_storm"):
        v = f"v{rng.randint(1, 4)}.{rng.randint(0, 30)}.{rng.randint(0, 9)}"
        add_change("deploy", rid, "Service", t0 - rng.randint(30, 600), f"Deploy {rid} {v}")
    elif fault_kind == "config_change":
        param, old, new = rng.choice(CONFIG_PARAMS)
        add_change("config", rid, "Service", t0 - rng.randint(30, 600), f"Set {param} {old} -> {new} on {rid}",
                   param=param, old=old, new=new)
    elif fault_kind == "feature_flag":
        flag = rng.choice(OTEL_FLAGS.get(rid, [f"{rid}Experiment"]))
        add_change("feature_flag", rid, "Service", t0 - rng.randint(10, 120), f"Enable flag {flag}",
                   param=flag, old="off", new="on")
    elif fault_kind == "host_saturation" and rng.random() < 0.5:
        add_change("infra", rid, "Host", t0 - rng.randint(60, 600), f"Schedule batch job on {rid}",
                   param="cpu_limit", old="none", new="best-effort")

    noise_targets = [s for s in services if s != rid]
    for _ in range(rng.randint(*n_noise_changes)):
        tgt = rng.choice(noise_targets)
        at = t0 + (rng.randint(-600, 300) if rng.random() < 0.4 else rng.randint(-7200, -1800))
        kind = rng.choice(["deploy", "config", "feature_flag"])
        add_change(kind, tgt, "Service", at, f"Routine {kind} on {tgt}")
    if red_herring:
        add_change("deploy", red_herring, "Service", t0 - rng.randint(20, 90), f"Deploy {red_herring} (red herring)")

    incident = {
        "id": f"inc-{seed}",
        "title": f"Elevated errors/latency on {services[symptom]['name']}",
        "severity": "SEV2",
        "opened_at": opened_at,
        "service": symptom,
    }
    truth.update({"causal_set": sorted(causal), "decoys": sorted(decoys), "symptom": symptom})
    return Scenario(
        name=name or f"{fault_kind}-{seed}",
        seed=seed,
        description=description,
        services=list(services.values()),
        edges=edges,
        hosts=list(hosts.values()),
        changes=changes,
        incident=incident,
        truth=truth,
    )


AUTHORS = ["alice@astronomy.shop", "bob@astronomy.shop", "ci-bot", "sre-oncall", "platform-team"]
CONFIG_PARAMS = [
    ("max_connections", "500", "20"),
    ("pool_size", "64", "4"),
    ("timeout_ms", "3000", "200"),
    ("cache_ttl_s", "300", "0"),
    ("worker_threads", "32", "2"),
]
