"""Named, deterministic demo scenarios on the OpenTelemetry Demo topology.

Each is an ordinary generated scenario (same fault model as the benchmark)
with a fixed seed, root and fault kind. Seeds were picked with
scripts/find_demo_seeds.py so that each story shows something specific; the
selection criteria are printed by that script. Picking seeds for a demo is a
presentation choice and says nothing about accuracy: see bench/results for that.
"""
from __future__ import annotations

from synapseguard.data.scenarios import Scenario, generate
from synapseguard.data.topology import otel_demo_topology

DEMO_SCENARIOS: dict[str, dict] = {
    "payment-flag": {
        "title": "paymentFailure flag + red-herring frontend deploy",
        "story": "A feature flag makes Payment fail while a Frontend deploy lands minutes earlier as a red "
                 "herring, and unrelated services flap. Compare the graph answer with the graph-removed answer.",
        "args": {"seed": 5010, "fault_kind": "feature_flag", "root": "payment", "red_herring": "frontend"},
    },
    "silent-db": {
        "title": "Silent PostgreSQL degradation (no change record)",
        "story": "astronomy-db slows down with nothing in the change log. With no change to correlate, the "
                 "answer has to come from who calls whom and in which order things degraded.",
        "args": {"seed": 5000, "fault_kind": "silent", "root": "postgresql"},
    },
    "noisy-neighbour": {
        "title": "Saturated Kubernetes node (adHighCpu style)",
        "story": "One node runs Ad, Cart, Cache and Recommendation and hits ~97% CPU. Several services degrade "
                 "at once; the common cause is the host, not any service.",
        "args": {"seed": 5000, "fault_kind": "host_saturation", "root": "node-c"},
    },
    "catalog-lock": {
        "title": "productCatalogLockContention style retry storm",
        "story": "A Product Catalog deploy hammers the database, so PostgreSQL also degrades and has no degraded "
                 "dependencies of its own. The change record and onset order decide between them.",
        "args": {"seed": 5002, "fault_kind": "retry_storm", "root": "product-catalog"},
    },
    "quote-no-breaker": {
        "title": "Quote deploy where direct callers cannot circuit-break",
        "story": "Quote breaks after a deploy; its HTTP callers have no breakers, so the cheapest cut "
                 "would shed the whole frontend. The agent says so and leads with the rollback.",
        "args": {"seed": 5000, "fault_kind": "deploy_bug", "root": "quote"},
    },
}


def build_demo(name: str) -> Scenario:
    spec = DEMO_SCENARIOS[name]
    args = dict(spec["args"])
    seed = args.pop("seed")
    fault = args.pop("fault_kind")
    root = args.pop("root")
    return generate(otel_demo_topology(), seed, fault, root, name=name, description=spec["story"], **args)
