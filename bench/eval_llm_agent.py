"""Does a local LLM pick the right SynapseGuard tools over MCP? (Ollama, CPU)

    python -m bench.eval_llm_agent            # needs Ollama with llama3.1:8b

Fixed question set on the payment-flag demo graph. For each question we check
(a) the expected tool was called, (b) the returned answer contains the expected
fact, (c) whether the model's own sentence passed the grounding check (otherwise
the answer is assembled from tool outputs), and latency. temperature 0, seed 7.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from synapseguard.core.config import get_settings
from synapseguard.core.graph import GraphStore
from synapseguard.data.demo import build_demo
from synapseguard.data.loader import load_scenario
from synapseguard.engine.llm_agent import ask

QUESTIONS = [
    ("What breaks if postgresql goes down?", {"blast_radius"}, ["product-catalog"]),
    ("What breaks if Postgres goes down?", {"blast_radius"}, ["product-catalog"]),
    ("Who should I page for the payment service?", {"who_to_page"}, ["alice moreau"]),
    ("Who is on call for node-c?", {"who_to_page"}, ["kiri walker"]),
    ("Who made the most recent change to payment?", {"who_to_page", "recent_changes"}, ["alice moreau"]),
    ("What changed recently around checkout?", {"recent_changes"}, ["paymentfailure"]),
    ("Why is frontend-proxy connected to payment?", {"path_between"}, ["checkout"]),
    ("Is email connected to quote?", {"path_between"}, ["no call path", "not"]),
    ("Which services are the most load-bearing?", {"load_bearing"}, ["flagd"]),
    ("What breaks if payments-gateway goes down?", {"blast_radius"}, ["not in the graph", "unknown"]),
    ("What does the graph remember about payment?", {"recall"}, ["no past investigation", "payment"]),
    ("Investigate the open incident. What is the root cause?", {"investigate_incident", "list_incidents"},
     ["payment"]),
]


def main():
    s = get_settings()
    store = GraphStore("sg_llm_eval")
    load_scenario(store, build_demo("payment-flag"), reset=True)
    rows = []
    for q, tools, expect in QUESTIONS:
        t0 = time.perf_counter()
        r = ask(q, store, s)
        wall = (time.perf_counter() - t0) * 1000
        called = [st["tool"] for st in r["steps"]]
        text = r["answer"].lower()
        rows.append({"question": q, "called": called, "outcomes": r["outcomes"],
                     "tool_ok": bool(tools & set(called)), "fact_ok": any(e in text for e in expect),
                     "llm_sentence_accepted": r["source"].startswith("llm:"), "violations": r["violations"],
                     "answer": r["answer"], "wall_ms": round(wall)})
        print(json.dumps({k: rows[-1][k] for k in ("question", "called", "tool_ok", "fact_ok",
                                                   "llm_sentence_accepted", "wall_ms")}))
    store.drop()
    n = len(rows)
    summary = {"model": s.ollama_model, "questions": n,
               "tool_selection_accuracy": sum(r["tool_ok"] for r in rows) / n,
               "answer_contains_expected_fact": sum(r["fact_ok"] for r in rows) / n,
               "llm_sentence_accepted_rate": sum(r["llm_sentence_accepted"] for r in rows) / n,
               "latency_ms_p50": statistics.median(r["wall_ms"] for r in rows),
               "latency_ms_max": max(r["wall_ms"] for r in rows),
               "hardware": "CPU only (no GPU), 16 GB RAM"}
    out = Path(__file__).parent / "results"
    (out / "llm_agent.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
