"""Root-cause accuracy evaluation: SynapseGuard vs baselines (seeded, reproducible).

    python -m bench.eval_accuracy                      # OTel topology, default seeds
    python -m bench.eval_accuracy --topology layered:300 --test 2000:2300

Protocol
--------
* Validation seeds tune the weights of BOTH our scorer and the strongest flat
  baseline (same tuner, same budget). Test seeds are only used for reporting.
* Every scenario is loaded into FalkorDB and our features are extracted
  through Cypher, exactly as in the product. The pure-Python reference
  traversal is run alongside and any mismatch is counted and reported.
* Ground truth never enters the graph.

Caveat: scenarios come from our own fault model (synapseguard/data/scenarios.py).
These numbers measure accuracy under that model, not on production incidents.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from synapseguard.core.graph import GraphStore
from synapseguard.data.loader import load_scenario
from synapseguard.data.scenarios import ANOMALY_THRESHOLD, generate
from synapseguard.data.topology import layered_topology, otel_demo_topology
from synapseguard.engine import baselines as B
from synapseguard.engine.rca import DEFAULT_WEIGHTS, FEATURES, CausalFrontierRCA, NodeInfo, Subgraph

GRID = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0]
OUT = Path(__file__).parent / "results"


def parse_range(s: str) -> range:
    a, b = s.split(":")
    return range(int(a), int(b))


def topology_factory(spec: str):
    if spec == "otel":
        topo = otel_demo_topology()
        return lambda seed: topo
    if spec.startswith("layered:"):
        n = int(spec.split(":")[1])
        return lambda seed: layered_topology(n, seed=seed)
    raise SystemExit(f"unknown topology {spec}")


def global_subgraph(sc) -> Subgraph:
    """Ablation: every degraded/unknown service is a candidate (no reachability pruning)."""
    svc = {s["id"]: s for s in sc.services}
    keep = {sid for sid, s in svc.items() if s.get("anomaly") is None or s["anomaly"] >= ANOMALY_THRESHOLD}
    keep.add(sc.incident["service"])
    sub = Subgraph(symptom=sc.incident["service"])
    hosts = {h["id"]: h for h in sc.hosts}
    for n in keep:
        s = svc[n]
        sub.nodes[n] = NodeInfo(n, s["name"], s["kind"], s.get("anomaly"), s.get("onset"), s.get("error_rate"),
                                s.get("p99_ms"), s.get("base_error_rate"), s.get("base_p99_ms"),
                                s.get("criticality"), bool(s.get("entrypoint")), s.get("host"),
                                hosts.get(s.get("host"), {}).get("cpu"))
    sub.edges = [{"src": e["src"], "dst": e["dst"], "has_fallback": False} for e in sc.edges
                 if e["sync"] and e["src"] in keep and e["dst"] in keep]
    for hid in {i.host for i in sub.nodes.values() if i.host}:
        sub.hosts[hid] = {"cpu": hosts[hid]["cpu"], "mem": hosts[hid]["mem"],
                          "services": [(s["id"], s.get("anomaly"), s.get("onset")) for s in sc.services
                                       if s.get("host") == hid]}
    sub.changes = [dict(c) for c in sc.changes]
    return sub


def collect(store: GraphStore, seeds: range, topo_for) -> list[dict]:
    rca = CausalFrontierRCA(store)
    recs = []
    for seed in seeds:
        sc = generate(topo_for(seed), seed)
        load_scenario(store, sc, reset=True)
        t0 = time.perf_counter()
        sub = rca.extract_subgraph(sc.incident["service"])
        cands = rca.features(sub)
        latency = (time.perf_counter() - t0) * 1000
        ref = B.reference_subgraph(sc)
        mismatch = set(ref.nodes) != set(sub.nodes) or \
            {(e["src"], e["dst"]) for e in ref.edges} != {(e["src"], e["dst"]) for e in sub.edges}
        nop = rca.features(global_subgraph(sc))
        for c in nop:
            c.features["exposed"] = 1.0
        recs.append({
            "seed": seed,
            "kind": sc.truth["fault_kind"],
            "truth": sc.truth["root_id"],
            "ours": [(c.id, c.features) for c in cands],
            "auto_evidence": bool(sub.changes or sub.hosts),
            "no_pruning": [(c.id, c.features) for c in nop],
            "flat": B.flat_features(sc),
            "fixed": {
                "symptom_blame": B.symptom_blame(sc),
                "flat_anomaly": B.flat_anomaly(sc),
                "flat_change": B.flat_change(sc),
                "naive_deepest (old SynapseGuard)": B.naive_deepest(sc),
                "pagerank (MicroRCA-style)": B.pagerank(sc, ref),
            },
            "latency_ms": latency,
            "subgraph_size": len(sub.nodes),
            "queries": sub.query_count,
            "mismatch": mismatch,
        })
    return recs


def rank_pos(ranked: list[str], truth: str) -> int | None:
    return ranked.index(truth) + 1 if truth in ranked else None


def metrics(positions: list[int | None]) -> dict:
    n = len(positions)
    return {
        "n": n,
        "top1": sum(1 for p in positions if p == 1) / n,
        "top3": sum(1 for p in positions if p is not None and p <= 3) / n,
        "mrr": sum(1 / p for p in positions if p) / n,
    }


def evaluate(recs, key, weights) -> list[int | None]:
    return [rank_pos(B.rank_features(r[key], weights), r["truth"]) for r in recs]


def objective(pos) -> float:
    m = metrics(pos)
    return m["top1"] + 0.1 * m["mrr"]


def tune(recs, key, features, start) -> dict[str, float]:
    w = dict(start)
    best = objective(evaluate(recs, key, w))
    for _ in range(3):
        improved = False
        for f in features:
            for v in GRID:
                trial = {**w, f: v}
                o = objective(evaluate(recs, key, trial))
                if o > best + 1e-9:
                    best, w, improved = o, trial, True
        if not improved:
            break
    return w


def calibration(recs, key, weights) -> dict:
    """Is the top candidate's relative confidence trustworthy? Buckets, confidently-wrong rate,
    and the accuracy/coverage trade-off of abstaining below a confidence threshold."""
    import math
    from synapseguard.engine.rca import SOFTMAX_TEMPERATURE
    pts = []
    for r in recs:
        scored = sorted(((sum(weights.get(k, 0) * v for k, v in f.items()), cid) for cid, f in r[key]),
                        key=lambda x: (-x[0], x[1]))
        if not scored:
            pts.append((0.0, False))
            continue
        m = scored[0][0]
        z = sum(math.exp((sc - m) / SOFTMAX_TEMPERATURE) for sc, _ in scored)
        pts.append((1 / z, scored[0][1] == r["truth"]))
    buckets = {}
    for lo, hi in ((0, .5), (.5, .8), (.8, .95), (.95, 1.01)):
        sel = [ok for c, ok in pts if lo <= c < hi]
        buckets[f"{lo:.2f}-{min(hi, 1):.2f}"] = {"n": len(sel), "accuracy": round(sum(sel) / len(sel), 3) if sel else None}
    abstain = {}
    for t in (0.0, 0.5, 0.8, 0.9):
        kept = [ok for c, ok in pts if c >= t]
        abstain[f">={t}"] = {"answered": round(len(kept) / len(pts), 3),
                             "accuracy_when_answered": round(sum(kept) / len(kept), 3) if kept else None}
    return {"buckets": buckets,
            "confidently_wrong_rate": round(sum(1 for c, ok in pts if c >= 0.8 and not ok) / len(pts), 4),
            "abstention": abstain}


def by_kind(recs, positions) -> dict:
    groups = defaultdict(list)
    for r, p in zip(recs, positions):
        groups[r["kind"]].append(p)
    return {k: round(metrics(v)["top1"], 3) for k, v in sorted(groups.items())}


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q / 100 * (len(xs) - 1))))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="otel")
    ap.add_argument("--val", default="0:300")
    ap.add_argument("--test", default="1000:1500")
    ap.add_argument("--weights", choices=["tune", "default"], default="tune")
    ap.add_argument("--graph", default="sg_eval")
    ap.add_argument("--out", default=str(OUT), help="results folder (CI smoke runs use a temp folder)")
    args = ap.parse_args(argv)

    store = GraphStore(args.graph)
    store.wait_until_ready()
    topo_for = topology_factory(args.topology)
    t0 = time.time()
    val = collect(store, parse_range(args.val), topo_for)
    test = collect(store, parse_range(args.test), topo_for)
    store.drop()

    if args.weights == "tune":
        w_ours = tune(val, "ours", FEATURES, DEFAULT_WEIGHTS)
        w_flat = tune(val, "flat", B.FLAT_FEATURES, B.FLAT_DEFAULT_WEIGHTS)
    else:
        w_ours, w_flat = dict(DEFAULT_WEIGHTS), dict(B.FLAT_DEFAULT_WEIGHTS)

    rows = {}
    pos_ours = evaluate(test, "ours", w_ours)
    rows["ours, evidence profile"] = (pos_ours, "graph")
    from synapseguard.engine.rca import SEVERITY_WEIGHTS
    rows["ours, auto profile (product default)"] = ([rank_pos(B.rank_features(
        r["ours"], w_ours if r["auto_evidence"] else SEVERITY_WEIGHTS), r["truth"]) for r in test], "graph")
    rows["graph pruning + severity only"] = (evaluate(test, "ours", SEVERITY_WEIGHTS), "graph (ablation)")
    rows["ours, no reachability pruning"] = (evaluate(test, "no_pruning", w_ours), "graph (ablation)")
    for f in FEATURES:
        rows[f"ours without '{f}'"] = (evaluate(test, "ours", {**w_ours, f: 0.0}), "graph (ablation)")
    rows["flat evidence (tuned, no relationships)"] = (evaluate(test, "flat", w_flat), "flat")
    for name in test[0]["fixed"]:
        kind = "graph" if ("deepest" in name or "pagerank" in name) else "flat"
        rows[name] = ([rank_pos(r["fixed"][name], r["truth"]) for r in test], kind)

    lat = [r["latency_ms"] for r in test]
    report = {
        "topology": args.topology,
        "validation_seeds": args.val,
        "test_seeds": args.test,
        "weights_ours": w_ours,
        "weights_flat": w_flat,
        "methods": {k: {**metrics(p), "uses": u, "top1_by_fault": by_kind(test, p)} for k, (p, u) in rows.items()},
        "calibration": calibration(test, "ours", w_ours),
        "fault_mix": dict(sorted(defaultdict(int, {k: sum(1 for r in test if r["kind"] == k)
                                                   for k in {r["kind"] for r in test}}).items())),
        "cypher_vs_reference_mismatches": sum(r["mismatch"] for r in val + test),
        "extract_and_score_latency_ms": {"p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
                                         "mean": statistics.mean(lat)},
        "mean_subgraph_size": statistics.mean(r["subgraph_size"] for r in test),
        "mean_queries": statistics.mean(r["queries"] for r in test),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                        "processor": platform.processor()},
        "wall_time_s": round(time.time() - t0, 1),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = args.topology.replace(":", "")
    (out / f"accuracy_{tag}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / f"accuracy_{tag}.md").write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))


def to_markdown(r: dict) -> str:
    lines = [
        f"# Root-cause accuracy: topology `{r['topology']}`",
        "",
        f"Validation seeds {r['validation_seeds']} (weights tuned here), test seeds {r['test_seeds']} "
        f"(reported here). Fault mix in test: {r['fault_mix']}.",
        "",
        "| Method | Uses | Top-1 | Top-3 | MRR |",
        "|---|---|---:|---:|---:|",
    ]
    for k, m in r["methods"].items():
        lines.append(f"| {k} | {m['uses']} | {m['top1']:.3f} | {m['top3']:.3f} | {m['mrr']:.3f} |")
    lines += ["", "Top-1 by fault kind:", "", "| Method | " + " | ".join(r["fault_mix"]) + " |",
              "|---|" + "---:|" * len(r["fault_mix"])]
    for k, m in r["methods"].items():
        lines.append(f"| {k} | " + " | ".join(f"{m['top1_by_fault'].get(f, 0):.2f}" for f in r["fault_mix"]) + " |")
    l = r["extract_and_score_latency_ms"]
    lines += [
        "",
        "Calibration of the top candidate's relative confidence (ours, test set):",
        "",
        "| Confidence | n | Accuracy |", "|---|---:|---:|",
        *[f"| {b} | {v['n']} | {v['accuracy']} |" for b, v in r["calibration"]["buckets"].items()],
        "",
        f"Confidently wrong (confidence >= 0.8 and wrong): **{r['calibration']['confidently_wrong_rate']:.1%}** of incidents.  ",
        "Abstaining below a threshold: " + "; ".join(
            f"{k}: answers {v['answered']:.0%}, {v['accuracy_when_answered']} correct"
            for k, v in r["calibration"]["abstention"].items()) + "  ",
        "",
        f"Tuned weights (ours): `{r['weights_ours']}`  ",
        f"Tuned weights (flat): `{r['weights_flat']}`  ",
        f"Cypher traversal vs pure-Python reference mismatches: **{r['cypher_vs_reference_mismatches']}**  ",
        f"Extract + score latency through FalkorDB: p50 {l['p50']:.1f} ms, p95 {l['p95']:.1f} ms, "
        f"p99 {l['p99']:.1f} ms (mean subgraph {r['mean_subgraph_size']:.1f} nodes, "
        f"{r['mean_queries']:.1f} queries)  ",
        f"Environment: {r['environment']}  ",
        f"Wall time: {r['wall_time_s']} s",
        "",
        "Scenarios come from our own fault model; see docs/evaluation.md for its assumptions and limits.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
