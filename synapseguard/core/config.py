"""Runtime configuration, read once from environment variables.

Nothing here is secret by default; FALKORDB_PASSWORD is only read from the
environment and never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    falkordb_host: str = field(default_factory=lambda: os.getenv("FALKORDB_HOST", "localhost"))
    falkordb_port: int = field(default_factory=lambda: int(os.getenv("FALKORDB_PORT", "6379")))
    falkordb_username: str | None = field(default_factory=lambda: os.getenv("FALKORDB_USERNAME") or None)
    falkordb_password: str | None = field(default_factory=lambda: os.getenv("FALKORDB_PASSWORD") or None)
    graph_name: str = field(default_factory=lambda: os.getenv("SG_GRAPH", "sg_otel_demo"))
    # Per-query timeout enforced by FalkorDB itself (milliseconds).
    query_timeout_ms: int = field(default_factory=lambda: int(os.getenv("SG_QUERY_TIMEOUT_MS", "2000")))
    # Hard cap on rows returned by the read-only Cypher tool exposed to LLMs.
    readonly_row_limit: int = field(default_factory=lambda: int(os.getenv("SG_READONLY_ROW_LIMIT", "200")))
    # Optional local LLM narration (off by default; the core never needs it).
    llm_enabled: bool = field(default_factory=lambda: _env_bool("SG_LLM", False))
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("SG_LLM_TIMEOUT_S", "90")))


def get_settings() -> Settings:
    return Settings()
