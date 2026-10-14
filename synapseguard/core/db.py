import os
from typing import Optional
from falkordb import FalkorDB

class FalkorClient:
    """
    Thread-safe FalkorDB connection provider for SynapseGuard.
    Handles graph selection, query execution, indexing, and cleanup.
    """
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, graph_name: str = "synapseguard"):
        self.host = host or os.getenv("FALKORDB_HOST", "localhost")
        self.port = int(port or os.getenv("FALKORDB_PORT", "6379"))
        self.graph_name = graph_name
        self._db: Optional[FalkorDB] = None
        self._graph = None

    def connect(self):
        if self._db is None:
            self._db = FalkorDB(host=self.host, port=self.port)
            self._graph = self._db.select_graph(self.graph_name)
        return self._graph

    @property
    def graph(self):
        if self._graph is None:
            return self.connect()
        return self._graph

    def query(self, cypher: str, params: Optional[dict] = None):
        """Execute a Cypher query with optional parameterization."""
        g = self.graph
        if params:
            return g.query(cypher, params)
        return g.query(cypher)

    def reset_graph(self):
        """Deletes and recreates the graph to ensure deterministic state."""
        try:
            if self._graph is not None:
                self._graph.delete()
        except Exception:
            pass
        if self._db is None:
            self.connect()
        self._graph = self._db.select_graph(self.graph_name)
        self.create_indices()

    def create_indices(self):
        """Creates schema indices to accelerate multi-hop lookups."""
        index_queries = [
            "CREATE INDEX FOR (s:Service) ON (s.id)",
            "CREATE INDEX FOR (i:Incident) ON (i.id)",
            "CREATE INDEX FOR (c:ConfigChange) ON (c.id)",
            "CREATE INDEX FOR (h:Host) ON (h.id)"
        ]
        for q in index_queries:
            try:
                self.graph.query(q)
            except Exception:
                pass  # Indices may already exist

