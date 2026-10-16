"""Real-world evaluation on RCAEval RE1 (real fault injection, real metrics).

RCAEval (Pham et al., WWW'25 companion; data CC-BY-4.0, code MIT):
https://github.com/phamquiluan/RCAEval, https://zenodo.org/records/14504481

    python -m bench.eval_rcaeval --data <dir containing online-boutique/ and sock-shop-2/>

Protocol (mirrors RCAEval main.py): drop *_latency-50, ffill/fillna, take the
10 minutes before the given injection time as normal and the 10 minutes after
as abnormal; score with AC@k and Avg@5 on the root-cause service.

Adapter (our choices, all fixed before looking at results):
* Per metric robust z-score against the normal window (median / IQR, as BARO's
  RobustScaler). Service anomaly = max |z| over its metrics in the abnormal window.
  A service is degraded if max |z| >= Z; onset = first abnormal second where any of
  its metrics crosses Z. anomaly = 0.2*z/Z below Z and 0.2 + 0.8*(1 - Z/z) above,
  monotone and non-saturating, so "degraded" matches the product's 0.2 threshold.
* Call graphs come from each system's source code (see CALL_GRAPHS). Train Ticket
  is NOT evaluated with the graph: RE1 has no traces and we have no verified call
  graph for its ~40 services.
* The incident is reported on the user-facing entrypoint.
* RE1 has no change log and no service->host placement, so the 'change' and
  'common' features are always 0. Weights are the ones tuned on synthetic data;
  nothing is tuned on RCAEval.
* Ranking = graph candidates (in score order), then every remaining service by
  anomaly, so AC@k is over a complete ranking like the other methods.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from synapseguard.core.graph import GraphStore
from synapseguard.data.loader import load_scenario
from synapseguard.data.scenarios import Scenario
from synapseguard.engine.rca import CausalFrontierRCA

OUT = Path(__file__).parent / "results"

# caller -> callees, derived from service source code (see docs/evaluation.md)
CALL_GRAPHS = {
    "online-boutique": {
        "entry": "frontend",
        "edges": {
            "frontend-external": ["frontend"],
            "frontend": ["adservice", "cartservice", "checkoutservice", "currencyservice",
                         "productcatalogservice", "recommendationservice", "shippingservice"],
            "checkoutservice": ["cartservice", "productcatalogservice", "shippingservice", "currencyservice",
                                "paymentservice", "emailservice"],
            "recommendationservice": ["productcatalogservice"],
            "cartservice": ["redis"],
        },
    },
    "sock-shop-2": {
        "entry": "front-end",
        "edges": {
            "front-end": ["catalogue", "carts", "orders", "user", "session-db"],
            "catalogue": ["catalogue-db"],
            "carts": ["carts-db"],
            "orders": ["orders-db", "carts", "user", "payment", "shipping"],
            "user": ["user-db"],
            "shipping": ["rabbitmq"],
            "queue-master": ["rabbitmq"],
        },
    },
}


SUITE_DIRS = {"re1": {"online-boutique": "online-boutique", "sock-shop-2": "sock-shop-2", "train-ticket": "train-ticket"},
              "re2": {"online-boutique": "RE2/RE2-OB", "sock-shop-2": "RE2/RE2-SS", "train-ticket": "RE2/RE2-TT"}}
TT_TOPOLOGY = OUT / "topology_train-ticket_from_traces.json"


def train_ticket_graph(min_root_share: float = 0.01) -> dict | None:
    """Train Ticket call graph learned from real RE2 Jaeger spans (normal period only).
    The load generator calls many services directly, so a virtual `external-client`
    (users) calls every service that receives >=1% of root spans; it has no telemetry
    and is never blamed."""
    if not TT_TOPOLOGY.exists():
        return None
    t = json.loads(TT_TOPOLOGY.read_text())
    total = sum(t["root_spans"].values())
    entries = sorted(s for s, n in t["root_spans"].items() if n / total >= min_root_share)
    edges = {k: list(v) for k, v in t["edges"].items()}
    edges["external-client"] = entries
    return {"entry": "external-client", "edges": edges, "derived_from": "traces", "virtual": ["external-client"]}


if (tt := train_ticket_graph()) is not None:
    CALL_GRAPHS["train-ticket"] = tt


def load_case(path: Path, length_min: int = 20) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    data = pd.read_csv(path / "data.csv")
    if "time.1" in data:
        data = data.drop(columns=["time.1"])
    data = data.loc[:, ~data.columns.str.endswith("_latency-50")]
    data = data.replace([np.inf, -np.inf], np.nan).ffill().fillna(0)
    inject = int((path / "inject_time.txt").read_text().strip().splitlines()[0])
    normal = data[data["time"] < inject].tail(length_min * 60 // 2)
    abnormal = data[data["time"] >= inject].head(length_min * 60 // 2)
    return normal, abnormal, inject


def service_scores(normal: pd.DataFrame, abnormal: pd.DataFrame, services: set[str], z_thr: float):
    """max robust |z| per service and the first abnormal time it crosses z_thr."""
    out: dict[str, tuple[float, int | None]] = {}
    for svc in services:
        cols = [c for c in normal.columns if c != "time" and c.rsplit("_", 1)[0] == svc]
        best, onset = 0.0, None
        for c in cols:
            med = normal[c].median()
            iqr = normal[c].quantile(0.75) - normal[c].quantile(0.25)
            scale = iqr if iqr > 0 else (normal[c].std() or 1.0)
            z = ((abnormal[c] - med) / scale).abs()
            m = float(z.max()) if len(z) else 0.0
            if not np.isfinite(m):
                continue
            best = max(best, m)
            hit = abnormal["time"][z >= z_thr]
            if len(hit):
                t = int(hit.iloc[0])
                onset = t if onset is None else min(onset, t)
        out[svc] = (best, onset)
    return out


def to_scenario(system: str, case: str, scores: dict, z_thr: float) -> Scenario:
    g = CALL_GRAPHS[system]
    services = []
    for sid, (z, onset) in sorted(scores.items()):
        # monotone in z and non-saturating above the threshold
        a = round(0.2 * z / z_thr if z < z_thr else 0.2 + 0.8 * (1 - z_thr / z), 6)
        services.append({"id": sid, "name": sid, "kind": "service", "language": "n/a", "host": None,
                         "entrypoint": sid == g["entry"], "criticality": "normal", "base_error_rate": None,
                         "base_p99_ms": None, "error_rate": None, "p99_ms": None, "anomaly": a,
                         "onset": onset if a >= 0.2 else None})
    for v in g.get("virtual", []):  # e.g. external-client: no telemetry, never blamed
        services.append({"id": v, "name": v, "kind": "proxy", "language": "n/a", "host": None, "entrypoint": True,
                         "criticality": "high", "base_error_rate": None, "base_p99_ms": None, "error_rate": None,
                         "p99_ms": None, "anomaly": None, "onset": None})
    ids = {s["id"] for s in services}
    edges = [{"src": a, "dst": b, "protocol": "http", "sync": True, "rps": 1, "has_fallback": False}
             for a, bs in g["edges"].items() for b in bs if a in ids and b in ids]
    inc = {"id": f"rcaeval-{system}-{case}".replace("_", "-").replace("/", "-"), "title": case,
           "severity": "SEV2", "opened_at": 0, "service": g["entry"]}
    return Scenario(name=case, seed=0, description="RCAEval RE1", services=services, edges=edges,
                    hosts=[], changes=[], incident=inc)


def ac_at(ranked: list[str], truth: str, k: int) -> float:
    return 1.0 if truth in ranked[:k] else 0.0


def summarize(rows: list[dict], key: str) -> dict:
    by_fault = defaultdict(list)
    for r in rows:
        by_fault[r["fault"]].append(r)
    out = {}
    for fault, rs in sorted(by_fault.items()) + [("ALL", rows)]:
        ac = {k: statistics.mean(ac_at(r[key], r["truth"], k) for r in rs) for k in (1, 2, 3, 4, 5)}
        out[fault] = {"AC@1": ac[1], "AC@3": ac[3], "Avg@5": statistics.mean(ac.values()), "n": len(rs)}
    return out


def rerank(row: dict, weights: dict[str, float]) -> list[str]:
    scored = sorted(row["feats"], key=lambda cf: (-sum(weights.get(k, 0) * v for k, v in cf[1].items()), cf[0]))
    ranked = [cid for cid, _ in scored]
    return ranked + [s for s in row["max_z"] if s not in ranked]


def avg5(rows, weights) -> float:
    return statistics.mean(statistics.mean(ac_at(rerank(r, weights), r["truth"], k) for k in range(1, 6))
                           for r in rows)


def tune(rows_by_z: dict[float, list[dict]]) -> tuple[float, dict[str, float], float]:
    """Coordinate ascent over weights and Z, maximising Avg@5 on the given (training) system."""
    from synapseguard.engine.rca import DEFAULT_WEIGHTS, FEATURES
    grid = [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]
    best = (-1.0, None, None)
    for z, rows in rows_by_z.items():
        w = dict(DEFAULT_WEIGHTS)
        cur = avg5(rows, w)
        for _ in range(3):
            improved = False
            for f in FEATURES:
                for v in grid:
                    t = {**w, f: v}
                    o = avg5(rows, t)
                    if o > cur + 1e-9:
                        cur, w, improved = o, t, True
            if not improved:
                break
        if cur > best[0]:
            best = (cur, z, w)
    return best[1], best[2], best[0]


def loso(cache: dict) -> dict:
    """Leave-one-system-out: tune weights and Z on one system, report on the other."""
    out = {}
    systems = list(cache)
    for test in systems:
        train = [s for s in systems if s != test]
        merged: dict[float, list[dict]] = {}
        for s in train:
            for z, rows in cache[s].items():
                merged.setdefault(z, []).extend(rows)
        z, w, train_score = tune(merged)
        test_rows = cache[test][z]
        by_fault = defaultdict(list)
        for r in test_rows:
            by_fault[r["fault"]].append(r)
        out[test] = {"tuned_on": train, "z": z, "weights": w, "train_avg5": round(train_score, 4),
                     "test_avg5": round(avg5(test_rows, w), 4),
                     "test_avg5_by_fault": {f: round(avg5(rs, w), 4) for f, rs in sorted(by_fault.items())},
                     "no_graph_max_z_avg5": round(statistics.mean(
                         statistics.mean(ac_at(r["max_z"], r["truth"], k) for k in range(1, 6)) for r in test_rows), 4)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--z", type=float, nargs="+", default=[3.0, 5.0, 10.0])
    ap.add_argument("--graph", default="sg_rcaeval")
    ap.add_argument("--suite", default="re1", choices=["re1", "re2"],
                    help="re1: <data>/online-boutique, <data>/sock-shop-2; re2: <data>/RE2/RE2-OB, RE2-SS")
    args = ap.parse_args(argv)
    store = GraphStore(args.graph)
    store.wait_until_ready()
    rca = CausalFrontierRCA(store)
    report: dict = {"protocol": __doc__.split("Protocol")[1].strip(), "systems": {}}
    cache: dict = {}
    for system in CALL_GRAPHS:
        root = Path(args.data) / SUITE_DIRS[args.suite][system]
        if not root.exists():
            print(f"skip {system}: {root} not found")
            continue
        cases = sorted(p for p in root.glob("*_*/*") if (p / "data.csv").exists())
        for z_thr in args.z:
            rows = []
            lat = []
            for case in cases:
                svc_fault = case.parent.name
                truth, fault = svc_fault.rsplit("_", 1)
                normal, abnormal, _ = load_case(case)
                services = {c.rsplit("_", 1)[0] for c in normal.columns if c != "time"}
                services &= set(CALL_GRAPHS[system]["edges"]) | {b for bs in CALL_GRAPHS[system]["edges"].values()
                                                                for b in bs}
                scores = service_scores(normal, abnormal, services, z_thr)
                sc = to_scenario(system, f"{svc_fault}/{case.name}", scores, z_thr)
                load_scenario(store, sc, reset=True)
                t0 = time.perf_counter()
                sub = rca.extract_subgraph(sc.incident["service"])
                cands = rca.features(sub)
                feats = [(c.id, dict(c.features)) for c in cands]
                ranked_graph = [c.id for c in rca.rank(cands)]  # auto -> severity here (no changes/hosts)
                evid = [c.id for c in rca.rank([c for c in cands], weights=rca.weights)]
                evid += [s for s in sorted(scores, key=lambda k: (-scores[k][0], k)) if s not in evid]
                lat.append((time.perf_counter() - t0) * 1000)
                by_anom = [s for s, _ in sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))]
                ours = ranked_graph + [s for s in by_anom if s not in ranked_graph]
                rows.append({"case": f"{svc_fault}/{case.name}", "truth": truth, "fault": fault,
                             "ours": ours, "max_z": by_anom, "feats": feats, "evidence": evid,
                             "truth_degraded": scores.get(truth, (0, None))[0] >= z_thr,
                             "truth_in_subgraph": truth in sub.nodes})
            cache.setdefault(system, {})[z_thr] = rows
            report["systems"].setdefault(system, {})[f"z={z_thr}"] = {
                "cases": len(rows),
                "ours (product default: auto profile)": summarize(rows, "ours"),
                "ours, evidence profile (synthetic-tuned weights)": summarize(rows, "evidence"),
                "max robust z (no graph; BARO-style scorer)": summarize(rows, "max_z"),
                "truth_degraded_rate": statistics.mean(r["truth_degraded"] for r in rows),
                "truth_reachable_rate": statistics.mean(r["truth_in_subgraph"] for r in rows),
                "graph_latency_ms_p50": float(np.percentile(lat, 50)),
                "graph_latency_ms_p95": float(np.percentile(lat, 95)),
            }
            print(system, z_thr, json.dumps({k: v["ALL"] if isinstance(v, dict) and "ALL" in v else v
                                             for k, v in report["systems"][system][f"z={z_thr}"].items()}))
    store.drop()
    report["leave_one_system_out"] = loso(cache)
    # Predefined ablation: graph only prunes (reachable via degraded calls), severity ranks.
    sev_only = {"severity": 1.0}
    report["graph_pruning_plus_severity"] = {
        sysn: {f"z={z}": round(avg5(rows, sev_only), 4) for z, rows in zs.items()} for sysn, zs in cache.items()}
    print("pruning+severity", report["graph_pruning_plus_severity"])
    print(json.dumps(report["leave_one_system_out"], indent=1))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"rcaeval_{args.suite}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
