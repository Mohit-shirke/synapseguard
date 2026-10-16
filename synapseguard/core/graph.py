"""FalkorDB access layer.

Every query in SynapseGuard goes through :class:`GraphStore`, which

* always sends parameters separately from the Cypher text (no string
  interpolation of user data),
* applies a server-side timeout to every query,
* offers ``ro_query`` backed by FalkorDB's ``GRAPH.RO_QUERY`` command, which the
  server itself rejects for any write clause. This is what the LLM-facing
  "free-form Cypher" tool uses, so prompt-injected writes are refused by the
  database rather than by a regex.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable

from falkordb import FalkorDB

from synapseguard.core.config import Settings, get_settings

log = logging.getLogger(__name__)

INDEXES = [
    "CREATE INDEX FOR (s:Service) ON (s.id)",
    "CREATE INDEX FOR (s:Service) ON (s.anomaly)",
    "CREATE INDEX FOR (h:Host) ON (h.id)",
    "CREATE INDEX FOR (c:Change) ON (c.id)",
    "CREATE INDEX FOR (i:Incident) ON (i.id)",
    "CREATE INDEX FOR (v:Investigation) ON (v.id)",
    "CREATE INDEX FOR (a:Action) ON (a.id)",
    "CREATE INDEX FOR (p:Person) ON (p.id)",
    "CREATE INDEX FOR (p:Person) ON (p.email)",
    "CREATE INDEX FOR (t:Team) ON (t.id)",
]


class GraphUnavailable(RuntimeError):
    """FalkorDB could not be reached."""


class GraphStore:
    """Thin, thread-safe wrapper around one FalkorDB graph.

    The underlying redis-py client keeps a connection pool, so a single
    ``GraphStore`` can be shared by FastAPI worker threads.
    """

    def __init__(self, graph_name: str | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.graph_name = graph_name or self.settings.graph_name
        self._db: FalkorDB | None = None
        self._graph = None
        self._lock = threading.Lock()

    # -- connection ---------------------------------------------------------
    def _connect(self):
        with self._lock:
            if self._graph is None:
                s = self.settings
                self._db = FalkorDB(
                    host=s.falkordb_host,
                    port=s.falkordb_port,
                    username=s.falkordb_username,
                    password=s.falkordb_password,
                )
                self._graph = self._db.select_graph(self.graph_name)
        return self._graph

    def wait_until_ready(self, attempts: int = 30, delay_s: float = 1.0) -> None:
        """Block until FalkorDB answers, for container start-up ordering."""
        last: Exception | None = None
        for _ in range(attempts):
            try:
                self.query("RETURN 1")
                return
            except Exception as exc:  # noqa: BLE001 - we re-raise below
                last = exc
                self._graph = None
                time.sleep(delay_s)
        raise GraphUnavailable(f"FalkorDB not reachable: {last}")

    def ping(self) -> bool:
        try:
            self.query("RETURN 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- queries ------------------------------------------------------------
    def query(self, cypher: str, params: dict[str, Any] | None = None, timeout_ms: int | None = None):
        g = self._graph or self._connect()
        try:
            return g.query(cypher, params or None, timeout=timeout_ms or self.settings.query_timeout_ms)
        except (ConnectionError, OSError) as exc:
            self._graph = None
            raise GraphUnavailable(str(exc)) from exc
        except Exception as exc:
            # redis-py raises its own ConnectionError subclasses
            if exc.__class__.__name__ in {"ConnectionError", "TimeoutError"} and "redis" in exc.__class__.__module__:
                self._graph = None
                raise GraphUnavailable(str(exc)) from exc
            raise

    def ro_query(self, cypher: str, params: dict[str, Any] | None = None, timeout_ms: int | None = None):
        """Read-only query; FalkorDB refuses any write clause server-side."""
        g = self._graph or self._connect()
        return g.ro_query(cypher, params or None, timeout=timeout_ms or self.settings.query_timeout_ms)

    def rows(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        return self.query(cypher, params).result_set

    def batch(self, cypher: str, rows: Iterable[dict[str, Any]], size: int = 1000) -> int:
        """Run ``UNWIND $rows AS r ...`` in chunks; returns rows sent."""
        buf: list[dict[str, Any]] = []
        sent = 0
        for row in rows:
            buf.append(row)
            if len(buf) >= size:
                self.query(cypher, {"rows": buf}, timeout_ms=60_000)
                sent += len(buf)
                buf = []
        if buf:
            self.query(cypher, {"rows": buf}, timeout_ms=60_000)
            sent += len(buf)
        return sent

    # -- lifecycle ----------------------------------------------------------
    def ensure_indexes(self) -> None:
        for q in INDEXES:
            try:
                self.query(q)
            except Exception as exc:  # noqa: BLE001
                if "already indexed" not in str(exc).lower():
                    log.debug("index statement %r: %s", q, exc)

    def drop(self) -> None:
        """Delete this graph (only this one) if it exists."""
        g = self._graph or self._connect()
        try:
            g.delete()
        except Exception as exc:  # noqa: BLE001
            if "empty key" not in str(exc).lower() and "invalid graph" not in str(exc).lower():
                raise
        self._graph = None
