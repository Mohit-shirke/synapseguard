"""Scale and latency benchmark on seeded synthetic meshes.

    python -m bench.bench_scale --sizes 100 1000 10000 --reps 30

For each size: batched ingestion throughput, cold + warm latency (p50/p95/p99)
of the investigation steps, the hop-by-hop BFS vs a single variable-length
Cypher query for subgraph extraction, concurrent throughput, and FalkorDB
memory for the graph. Results -> bench/results/scale.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from synapseguard.core.graph import GraphStore
from synapseguard.data.loader import load_scenario
from synapseguard.data.scenarios import generate
from synapseguard.data.topology import layered_topology
from synapseguard.engine import containment as C
from synapseguard.engine.investigator import Investigator
from synapseguard.engine.rca import CausalFrontierRCA

OUT = Path(__file__).parent / "results"


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q / 100 * (len(xs) - 1))))]


def stats(xs: list[float]) -> dict:
    return {"p50": round(pct(xs, 50), 2), "p95": round(pct(xs, 95), 2), "p99": round(pct(xs, 99), 2),
            "mean": round(statistics.mean(xs), 2), "n": len(xs)}


def timed(fn, reps: int) -> tuple[float, list[float]]:
    t0 = time.perf_counter()
    fn()
    cold = (time.perf_counter() - t0) * 1000
    warm = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        warm.append((time.perf_counter() - t0) * 1000)
    return cold, warm


def graph_memory_mb(store: GraphStore) -> float | None:
    try:
        g = store._graph or store._connect()
        res = g.execute_command("GRAPH.MEMORY", "USAGE", store.graph_name)
        d = dict(zip(res[::2], res[1::2]))
        return float(d.get(b"total_graph_sz_mb", d.get("total_graph_sz_mb")))
    except Exception:  # noqa: BLE001 - optional metric
        return None


def run_size(n: int, reps: int, seed: int) -> dict:
    store = GraphStore(f"sg_scale_{n}")
    store.drop()
    topo = layered_topology(n, seed=seed)
    sc = generate(topo, seed, "deploy_bug")
    t0 = time.perf_counter()
    load_scenario(store, sc, reset=True)
    ingest_s = time.perf_counter() - t0
    rows = len(sc.services) + len(sc.edges) + len(sc.hosts) + len(sc.changes)

    rca = CausalFrontierRCA(store)
    sym = sc.incident["service"]
    sub = rca.extract_subgraph(sym)
    out: dict = {"services": len(sc.services), "calls": len(sc.edges), "hosts": len(sc.hosts),
                 "ingest_s": round(ingest_s, 2), "ingest_rows_per_s": round(rows / ingest_s),
                 "anomalous_subgraph_nodes": len(sub.nodes), "degraded_total": len(sc.anomalous_ids()),
                 "graph_memory_mb": graph_memory_mb(store)}

    cold, warm = timed(lambda: rca.extract_subgraph(sym), reps)
    out["extract_bfs_ms"] = {"cold": round(cold, 2), **stats(warm)}

    # single variable-length query alternative (FalkorDB enumerates paths)
    try:
        cold, warm = timed(lambda: rca.extract_ids_pathenum(sym), max(3, reps // 3))
        out["extract_varlength_ms"] = {"cold": round(cold, 2), **stats(warm)}
        # same node set as the BFS before unknown-leaf pruning
        out["varlength_same_nodes"] = rca.extract_ids_pathenum(sym) == set(sub.depth)
    except Exception as exc:  # noqa: BLE001 - e.g. server-side timeout on large meshes
        out["extract_varlength_ms"] = {"error": str(exc)[:120]}

    inv = Investigator(store)
    cold, warm = timed(lambda: inv.investigate(sc.incident["id"], persist=False), reps)
    out["investigate_end_to_end_ms"] = {"cold": round(cold, 2), **stats(warm)}

    roots = [sc.truth["root_id"]]
    cold, warm = timed(lambda: C.blast_radius(store, roots), reps)
    out["blast_radius_ms"] = {"cold": round(cold, 2), **stats(warm)}
    blast = C.blast_radius(store, roots)
    out["blast_radius_size"] = len(blast["impacted"])
    if blast["entrypoints"]:
        cold, warm = timed(lambda: C.breaker_plan(store, roots, blast), max(3, reps // 3))
        out["maxflow_mincut_ms"] = {"cold": round(cold, 2), **stats(warm)}

    def one(_):
        t0 = time.perf_counter()
        inv.investigate(sc.incident["id"], persist=False)
        return (time.perf_counter() - t0) * 1000

    for workers in (1, 8):
        t0 = time.perf_counter()
        with ThreadPoolExecutor(workers) as ex:
            lats = list(ex.map(one, range(reps * 2)))
        wall = time.perf_counter() - t0
        out[f"concurrency_{workers}"] = {"investigations_per_s": round(len(lats) / wall, 1), **stats(lats)}
    store.drop()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)
    report = {"environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "processor": platform.processor(), "falkordb_module": "6.0.2 (Docker, local)"},
              "sizes": {}}
    for n in args.sizes:
        report["sizes"][n] = run_size(n, args.reps, args.seed)
        print(n, json.dumps(report["sizes"][n]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "scale.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Scale benchmark", "", f"Environment: {report['environment']}", "",
             "| Services | Calls | Ingest rows/s | Subgraph | Extract BFS p50/p95 ms | Var-length p50 ms | "
             "Investigate p50/p95/p99 ms | Max-flow p50 ms | 8-thread inv/s | Graph MB |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n, r in report["sizes"].items():
        vl = r["extract_varlength_ms"]
        lines.append(f"| {r['services']} | {r['calls']} | {r['ingest_rows_per_s']} | {r['anomalous_subgraph_nodes']} | "
                     f"{r['extract_bfs_ms']['p50']}/{r['extract_bfs_ms']['p95']} | "
                     f"{vl.get('p50', 'error')} | "
                     f"{r['investigate_end_to_end_ms']['p50']}/{r['investigate_end_to_end_ms']['p95']}/"
                     f"{r['investigate_end_to_end_ms']['p99']} | {r.get('maxflow_mincut_ms', {}).get('p50', 'n/a')} | "
                     f"{r['concurrency_8']['investigations_per_s']} | {r['graph_memory_mb']} |")
    (OUT / "scale.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
