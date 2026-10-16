"""Download and lay out RCAEval RE1 + RE2 (Online Boutique, Sock Shop) for bench.eval_rcaeval.

    python -m scripts.prepare_rcaeval --out bench/data/rcaeval

Produces:
    <out>/online-boutique/<svc>_<fault>/<n>/{data.csv,inject_time.txt}   (RE1-OB, Zenodo RE1.zip)
    <out>/sock-shop-2/...                                                 (RE1-SS)
    <out>/RE2/RE2-OB/<svc>_<fault>/<n>/{data.csv,inject_time.txt}        (RE2 metrics from Hugging Face)
    <out>/RE2/RE2-SS/...

Data: RCAEval (CC-BY-4.0 on Zenodo, MIT on Hugging Face). Not redistributed by this repo.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import urllib.request
import zipfile
from pathlib import Path

RE1_URL = "https://zenodo.org/records/14504481/files/RE1.zip?download=1"
HF_TREE = "https://huggingface.co/api/datasets/phamquiluan/RCAEval/tree/main"
HF_FILE = "https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/main/{case}/{name}"
RE1_MAP = {"RE1/RE1-OB/": "online-boutique/", "RE1/RE1-SS/": "sock-shop-2/", "RE1/RE1-TT/": "train-ticket/"}


def prepare_re1(out: Path) -> int:
    if all((out / d).exists() for d in ("online-boutique", "sock-shop-2", "train-ticket")):
        print("RE1 already present")
        return 0
    zpath = out / "RE1.zip"
    if not zpath.exists():
        print("downloading RE1.zip (~390 MB) ...")
        urllib.request.urlretrieve(RE1_URL, zpath)
    n = 0
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            for src, dst in RE1_MAP.items():
                if m.filename.startswith(src) and not m.is_dir():
                    rel = m.filename[len(src):]
                    if ".." in Path(rel).parts or rel.startswith("/"):
                        raise SystemExit(f"unsafe path in zip: {m.filename}")
                    target = out / dst / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(m) as r, open(target, "wb") as w:
                        w.write(r.read())
                    n += 1
    print(f"RE1: extracted {n} files")
    return n


def prepare_re2(out: Path) -> int:
    import pandas as pd  # needs pyarrow for parquet

    with urllib.request.urlopen(HF_TREE) as r:
        cases = [x["path"] for x in json.load(r) if x["path"].startswith(("re2ob_", "re2ss_", "re2tt_"))]

    def one(case: str) -> str:
        m = re.fullmatch(r"re2(ob|ss|tt)_(.+)_([a-z]+)_(\d+)", case)
        if not m:
            return "skip"
        sysdir = {"ob": "RE2-OB", "ss": "RE2-SS", "tt": "RE2-TT"}[m.group(1)]
        dst = out / "RE2" / sysdir / f"{m.group(2)}_{m.group(3)}" / m.group(4)
        if (dst / "data.csv").exists():
            return "cached"
        dst.mkdir(parents=True, exist_ok=True)
        tmp = dst / "metrics.parquet"
        urllib.request.urlretrieve(HF_FILE.format(case=case, name="metrics.parquet"), tmp)
        urllib.request.urlretrieve(HF_FILE.format(case=case, name="inject_time.txt"), dst / "inject_time.txt")
        pd.read_parquet(tmp).to_csv(dst / "data.csv", index=False)
        tmp.unlink()
        return "ok"

    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, cases))
    print(f"RE2: {results.count('ok')} downloaded, {results.count('cached')} cached, {len(cases)} cases")
    return len(cases)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bench/data/rcaeval")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prepare_re1(out)
    prepare_re2(out)
    prepare_tt_topology(out)
    print(f"done: python -m bench.eval_rcaeval --suite re1 --data {out}; --suite re2 likewise")



def prepare_tt_topology(out: Path) -> None:
    """Learn the Train Ticket call graph from one RE2 case's normal-period traces (~27 MB download)."""
    from bench.trace_topology import OUT as TOPO_OUT, derive
    case = "re2tt_ts-auth-service_cpu_1"
    dst = out / "traces" / case
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("traces.parquet", "inject_time.txt"):
        if not (dst / name).exists():
            urllib.request.urlretrieve(HF_FILE.format(case=case, name=name), dst / name)
    inject = int((dst / "inject_time.txt").read_text().strip().splitlines()[0])
    topo = derive(dst / "traces.parquet", inject)
    TOPO_OUT.mkdir(parents=True, exist_ok=True)
    (TOPO_OUT / "topology_train-ticket_from_traces.json").write_text(json.dumps(topo, indent=1), encoding="utf-8")
    print(f"Train Ticket topology: {len(topo['services'])} services, "
          f"{sum(len(v) for v in topo['edges'].values())} edges from {topo['spans_used']} normal-period spans")


if __name__ == "__main__":
    main()
