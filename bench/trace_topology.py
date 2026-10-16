"""Derive a service call graph from real Jaeger spans (RCAEval RE2 traces).

    python -m bench.trace_topology --traces <case>/traces.parquet --inject <case>/inject_time.txt --system tt

Only spans that start *before* the fault injection time are used, so the topology
carries no information about the fault. An edge caller -> callee exists when a span
of `callee` has a parent span belonging to `caller` (different services). The entry
service is the one owning the most root spans (spans without a parent in the trace).
Service names are normalised to the metric naming (`frontendservice` -> `frontend`).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "results"
ALIASES = {"frontendservice": "frontend"}  # trace name -> metric name (Online Boutique)


def derive(traces: Path, inject_time: int, min_calls: int = 1) -> dict:
    d = pd.read_parquet(traces, columns=["traceID", "spanID", "parentSpanID", "serviceName", "startTimeMillis"])
    d = d[d["startTimeMillis"] < inject_time * 1000]  # normal period only
    d["svc"] = d["serviceName"].map(lambda s: ALIASES.get(s, s))
    owner = dict(zip(d["spanID"], d["svc"]))
    calls: Counter = Counter()
    roots: Counter = Counter()
    for parent, svc in zip(d["parentSpanID"], d["svc"]):
        caller = owner.get(parent) if isinstance(parent, str) and parent else None
        if caller is None:
            roots[svc] += 1
        elif caller != svc:
            calls[(caller, svc)] += 1
    edges: dict[str, list[str]] = {}
    for (a, b), n in sorted(calls.items()):
        if n >= min_calls:
            edges.setdefault(a, []).append(b)
    return {
        "entry": roots.most_common(1)[0][0],
        "edges": edges,
        "calls": {f"{a}->{b}": n for (a, b), n in sorted(calls.items())},
        "services": sorted(set(d["svc"])),
        "root_spans": dict(roots.most_common()),
        "spans_used": int(len(d)),
        "source": str(traces.name),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", required=True)
    ap.add_argument("--inject", required=True)
    ap.add_argument("--system", required=True)
    args = ap.parse_args(argv)
    inject = int(Path(args.inject).read_text().strip().splitlines()[0])
    topo = derive(Path(args.traces), inject)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"topology_{args.system}_from_traces.json").write_text(json.dumps(topo, indent=1), encoding="utf-8")
    print(f"{args.system}: entry={topo['entry']} services={len(topo['services'])} "
          f"edges={sum(len(v) for v in topo['edges'].values())} spans={topo['spans_used']}")


if __name__ == "__main__":
    main()
