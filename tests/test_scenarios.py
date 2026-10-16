"""Generator and scoring-function properties (no database needed)."""
import json
import math

from hypothesis import given, settings
from hypothesis import strategies as st

from synapseguard.data.scenarios import ANOMALY_THRESHOLD, FAULT_KINDS, anomaly_score, generate
from synapseguard.data.topology import layered_topology, otel_demo_topology
from synapseguard.engine.baselines import reference_subgraph
from synapseguard.engine.rca import best_change

OTEL = otel_demo_topology()


def _dump(sc):
    return json.dumps([sc.services, sc.edges, sc.hosts, sc.changes, sc.incident, sc.truth], sort_keys=True)


def test_generation_is_deterministic_per_seed():
    for seed in range(30):
        assert _dump(generate(OTEL, seed)) == _dump(generate(OTEL, seed))
    assert _dump(generate(OTEL, 1)) != _dump(generate(OTEL, 2))


def test_layered_topology_is_deterministic_and_connected():
    a, b = layered_topology(300, seed=4), layered_topology(300, seed=4)
    assert a.edges == b.edges
    called = {e["dst"] for e in a.edges}
    for s in a.services:
        assert s["entrypoint"] or s["id"] in called, "every non-entry service has a caller"


@settings(max_examples=300, deadline=None)
@given(err=st.floats(0, 1), base=st.floats(0.0001, 0.05), p99=st.floats(0.1, 1e6), bp99=st.floats(0.1, 1e4))
def test_anomaly_score_bounded(err, base, p99, bp99):
    a = anomaly_score(err, base, p99, bp99)
    assert 0.0 <= a <= 1.0 and not math.isnan(a)


@given(base=st.floats(0.001, 0.05), bp99=st.floats(1, 500), d=st.floats(0, 0.5))
def test_anomaly_score_monotone_in_error(base, bp99, d):
    assert anomaly_score(min(1, base + d), base, bp99, bp99) >= anomaly_score(base, base, bp99, bp99)


def test_missing_telemetry_is_none():
    assert anomaly_score(None, 0.01, 10, 10) is None


@given(onset=st.integers(0, 10**9), dt=st.integers(-10_000, 10_000))
def test_change_after_onset_never_counts(onset, dt):
    score, _ = best_change([{"id": "c", "at": onset - dt}], onset)
    if dt < -60 or dt > 1800:
        assert score == 0
    else:
        assert 0.5 <= score <= 1.0


def test_ground_truth_is_consistent_over_many_seeds():
    """Root is degraded, symptom is degraded, and the root is reachable from the
    symptom in the anomalous subgraph whenever the root is a service."""
    for kind in FAULT_KINDS:
        for seed in range(40):
            sc = generate(OTEL, seed, kind)
            svc = {s["id"]: s for s in sc.services}
            assert svc[sc.incident["service"]]["anomaly"] >= ANOMALY_THRESHOLD
            sub = reference_subgraph(sc)
            if sc.truth["root_label"] == "Service":
                assert svc[sc.truth["root_id"]]["anomaly"] >= ANOMALY_THRESHOLD
                assert sc.truth["root_id"] in sub.nodes
            else:
                host_svcs = {s["id"] for s in sc.services if s["host"] == sc.truth["root_id"]}
                assert host_svcs & set(sub.nodes)


def test_ground_truth_never_stored_in_service_rows():
    sc = generate(OTEL, 3)
    for s in sc.services:
        assert not {"truth", "root", "is_root", "decoy"} & set(s)
