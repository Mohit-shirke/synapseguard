"""Build the recorded demo for GitHub Pages from the live app.

    uvicorn synapseguard.api.app:app --port 8000      # with FalkorDB running
    python -m scripts.build_static_site               # writes site/

Every JSON file under site/data/ is a real response captured from the running
API. The page is the same synapseguard/web/index.html; on *.github.io (or with
?static=1) it replays these files instead of calling the backend, and shows a
"Recorded demo" banner. Approve/execute/Ask-LLM need the live app.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def tool_key(tool: str, args: dict) -> str:
    """Must match toolKey() in synapseguard/web/index.html."""
    raw = tool + "__" + "_".join(f"{k}-{args[k]}" for k in sorted(args))
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def chip_calls(inv: dict) -> list[tuple[str, dict]]:
    """Same tool calls as renderChips() in the UI."""
    root = (inv.get("root_cause") or {}).get("id") or inv["incident"]["service"]
    sym = inv["incident"]["service"]
    return [
        ("blast_radius", {"service": root, "depth": 1}),
        ("blast_radius", {"service": root, "depth": 4}),
        ("recent_changes", {"service": sym, "hours": 2, "depth": 3}),
        ("path_between", {"source": sym, "target": root}),
        ("load_bearing", {"limit": 5}),
        ("recall", {"service": root}),
        ("blast_radius", {"service": "payments-gateway"}),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=str(ROOT / "site"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    data = out / "data"
    data.mkdir(parents=True)

    c = httpx.Client(base_url=args.api, timeout=60)

    def get(path):
        r = c.get(path)
        r.raise_for_status()
        return r.json()

    def post(path, body=None):
        r = c.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def save(rel, obj):
        p = data / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=1), encoding="utf-8")

    assert get("/health")["falkordb"], "start FalkorDB and the API first"
    post("/api/reset?confirm=true")
    scenarios = get("/api/scenarios")
    save("scenarios.json", scenarios)
    n = 0
    for sc in scenarios:
        name = sc["name"]
        loaded = post(f"/api/scenarios/{name}/load")
        save(f"{name}/load.json", loaded)
        save(f"{name}/graph.json", get("/api/graph"))
        save(f"{name}/incidents.json", get("/api/incidents"))
        inv = post(f"/api/incidents/{loaded['incident']}/investigate")
        save(f"{name}/investigate.json", inv)
        save(f"{name}/flat.json", post(f"/api/incidents/{loaded['incident']}/investigate?mode=flat"))
        for tool, targs in chip_calls(inv):
            save(f"{name}/tools/{tool_key(tool, targs)}.json", post(f"/api/tools/{tool}", targs))
            n += 1
        print(f"{name}: root={inv['root_cause']['id'] if inv['root_cause'] else None}")

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip() or "unknown"
    save("meta.json", {"captured_on": datetime.date.today().isoformat(), "commit": commit,
                       "scenarios": len(scenarios), "tool_answers": n})
    shutil.copy(ROOT / "synapseguard" / "web" / "index.html", out / "index.html")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"site written to {out} ({len(scenarios)} scenarios, {n} recorded tool answers)")


if __name__ == "__main__":
    main()
