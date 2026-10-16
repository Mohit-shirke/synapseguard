"""Call graph learned from Jaeger spans (no database needed)."""
import pandas as pd

from bench.trace_topology import derive


def test_derive_edges_entry_alias_and_no_post_injection_leak(tmp_path):
    inject = 1_000
    rows = [
        # trace 1 (normal period): frontendservice -> cart -> redis
        ("t1", "s1", "", "frontendservice", 900_000),
        ("t1", "s2", "s1", "cart", 900_001),
        ("t1", "s3", "s2", "redis", 900_002),
        ("t1", "s4", "s2", "cart", 900_003),  # same-service child: not an edge
        # trace 2 (normal): another root on frontendservice, a second root on auth
        ("t2", "s5", "", "frontendservice", 950_000),
        ("t3", "s6", "", "auth", 950_001),
        # after injection: must be ignored entirely (would add cart -> payment)
        ("t4", "s7", "", "cart", 1_000_500),
        ("t4", "s8", "s7", "payment", 1_000_501),
    ]
    df = pd.DataFrame(rows, columns=["traceID", "spanID", "parentSpanID", "serviceName", "startTimeMillis"])
    path = tmp_path / "traces.parquet"
    df.to_parquet(path)
    topo = derive(path, inject)
    assert topo["edges"] == {"frontend": ["cart"], "cart": ["redis"]}
    assert topo["entry"] == "frontend"
    assert "payment" not in topo["services"]
    assert topo["spans_used"] == 6
