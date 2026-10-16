"""Find seeds for the named demo scenarios (presentation only, not evaluation).

Criteria per story: incident lands on the entrypoint, at least two decoys,
our agent is right with confidence > 0.6, and story-specific conditions
(e.g. flat baseline wrong, or min-cut differs from naive). Run:

    python -m scripts.find_demo_seeds
"""
from __future__ import annotations

from synapseguard.core.graph import GraphStore
from synapseguard.data.loader import load_scenario
from synapseguard.data.scenarios import generate
from synapseguard.data.topology import otel_demo_topology
from synapseguard.engine.investigator import Investigator

STORIES = [
    ("payment-flag", "feature_flag", "payment", {"red_herring": "frontend"}, "flat_wrong"),
    ("silent-db", "silent", "postgresql", {}, "flat_wrong"),
    ("noisy-neighbour", "host_saturation", "node-c", {}, "any"),
    ("catalog-lock", "retry_storm", "product-catalog", {}, "any"),
    ("quote-no-breaker", "deploy_bug", "quote", {}, "cut_differs"),
]


def main() -> None:
    st = GraphStore("sg_find_seeds")
    topo = otel_demo_topology()
    agent = Investigator(st)
    for name, kind, root, kw, cond in STORIES:
        for seed in range(5000, 5400):
            sc = generate(topo, seed, kind, root, **kw)
            if sc.incident["service"] != "frontend-proxy" or len(sc.truth["decoys"]) < 2:
                continue
            load_scenario(st, sc, reset=True)
            r = agent.investigate(sc.incident["id"], persist=False)
            top = r["root_cause"]
            if not top or top["id"] != sc.truth["root_id"] or top["confidence"] <= 0.6:
                continue
            flat = agent.investigate_flat(sc.incident["id"])["root_cause"]["id"]
            b = r["breakers"] or {}
            if cond == "flat_wrong" and flat == sc.truth["root_id"]:
                continue
            if cond == "cut_differs" and not (b.get("status") == "ok" and not b["naive"]["feasible"]):
                continue
            print(f"{name}: seed={seed} confidence={top['confidence']} flat_top={flat} "
                  f"breakers={[(e['src'], e['dst']) for e in b.get('cut', [])]}")
            break
        else:
            print(f"{name}: no seed found in range")
    st.drop()


if __name__ == "__main__":
    main()
