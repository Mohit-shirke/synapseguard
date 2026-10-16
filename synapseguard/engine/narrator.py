"""Human-readable incident summaries.

The template narrative is built only from evidence items and is always
available. If ``SG_LLM=1``, a local Ollama model may rewrite it; the
rewrite is accepted only if a grounding check passes:

* every service / host / change identifier it mentions must appear in the
  evidence for this investigation, and
* it must name the root-cause candidate.

Otherwise the template is used and the violations are reported. The LLM never
chooses the root cause; it only words what the graph already established.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from synapseguard.core.config import Settings, get_settings


def template(incident: dict, top: Any | None, blast: dict | None, abstain: str | None) -> str:
    if top is None:
        return abstain or "No root cause could be established."
    parts = [f"Incident {incident['id']} ({incident['title']}) most likely originates at "
             f"{top.label.lower()} {top.id} (relative confidence {top.confidence:.0%})."]
    path = " -> ".join(top.path)
    if len(top.path) > 1:
        parts.append(f"The failure propagates along {path}.")
    for e in top.evidence:
        if e["kind"] in ("change", "host", "frontier", "blind_spot"):
            parts.append(e["statement"] + ".")
    if blast:
        parts.append(f"Blast radius: {len(blast['impacted'])} upstream services "
                     f"({blast['sync_count']} synchronously exposed), entrypoints: "
                     f"{', '.join(blast['entrypoints']) or 'none'}.")
    if top.predicted_recovery:
        parts.append(f"Fixing it is expected to recover {len(top.predicted_recovery)} degraded services: "
                     f"{', '.join(top.predicted_recovery)}.")
    return " ".join(parts)


def grounding_violations(text: str, allowed: set[str], universe: set[str], must_mention: str) -> list[str]:
    """Identifiers from ``universe`` that ``text`` mentions but evidence does not."""
    found = {u for u in universe if re.search(rf"(?<![\w-]){re.escape(u)}(?![\w-])", text)}
    v = sorted(found - allowed)
    if must_mention not in text:
        v.append(f"missing root cause '{must_mention}'")
    return v


def narrate(incident: dict, top: Any | None, blast: dict | None, abstain: str | None, universe: set[str],
            settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    base = template(incident, top, blast, abstain)
    out: dict[str, Any] = {"text": base, "source": "template", "violations": [], "llm_ms": None}
    if not s.llm_enabled or top is None:
        return out
    allowed = {top.id, incident["service"], *top.path, *top.predicted_recovery}
    for e in top.evidence:
        allowed.update(str(r) for r in e["refs"])
    if blast:
        allowed.update(i["id"] for i in blast["impacted"])
    prompt = (
        "Rewrite this incident analysis as a concise briefing for an on-call engineer (max 5 sentences). "
        "Use ONLY the facts below. Do not add services, hosts, changes, numbers or causes that are not "
        "listed. Return JSON {\"summary\": \"...\"}.\n\nFACTS:\n" + base
    )
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=s.llm_timeout_s) as c:
            r = c.post(f"{s.ollama_url}/api/generate", json={
                "model": s.ollama_model, "prompt": prompt, "stream": False, "format": "json",
                "options": {"temperature": 0, "seed": 7}})
            r.raise_for_status()
            text = json.loads(r.json().get("response", "{}")).get("summary", "")
    except Exception as exc:  # noqa: BLE001 - any LLM failure falls back to the template
        out["violations"] = [f"llm_unavailable: {type(exc).__name__}"]
        out["llm_ms"] = (time.perf_counter() - t0) * 1000
        return out
    out["llm_ms"] = (time.perf_counter() - t0) * 1000
    if not isinstance(text, str) or not text.strip():
        out["violations"] = ["llm_returned_no_summary"]
        return out
    if callable(universe):  # lazily find which graph entities the LLM text mentions
        universe = universe(text)
    v = grounding_violations(text, allowed, universe, top.id)
    if v:
        out["violations"] = v
        out["rejected_llm_text"] = text
        return out
    out.update({"text": text.strip(), "source": f"llm:{s.ollama_model} (grounding-verified)"})
    return out
