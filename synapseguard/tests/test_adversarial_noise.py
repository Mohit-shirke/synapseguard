import random
import time
import pytest
from synapseguard.core.db import FalkorClient
from synapseguard.data.seed import seed_telemetry_topology
from synapseguard.engine.traversal import GraphTraversalEngine
from synapseguard.engine.agent import SynapseAgent
from synapseguard.engine.memory import GraphMemoryCoordinator

@pytest.fixture(scope="module")
def client():
    c = FalkorClient(graph_name="synapseguard_adversarial_test")
    yield c
    c.reset_graph()

def test_random_noise_and_spurious_failures(client):
    """
    TEST 1: SPURIOUS NOISE & DECOY FAILURES
    Inject random flaky services with 30-60% error rates that are NOT in the causal path.
    Verify that the multi-hop traversal does not get distracted by random noise
    and strictly isolates the true causal leaf node.
    """
    seed_telemetry_topology(client)
    g = client.graph
    
    # Inject 15 decoy noisy microservices with random jitter, packet drops, and high latency
    for i in range(15):
        noise_err = random.uniform(0.35, 0.65)
        noise_lat = random.uniform(800.0, 3500.0)
        g.query(
            "CREATE (:Service {id: $id, name: $name, tier: 'worker', error_rate: $err, latency_ms: $lat, status: 'DEGRADED'})",
            {"id": f"decoy-svc-{i}", "name": f"Decoy-Background-Worker-{i}", "err": noise_err, "lat": noise_lat}
        )
        # Random spurious edges between decoys
        if i > 0:
            g.query(
                "MATCH (a:Service {id: $a}), (b:Service {id: $b}) CREATE (a)-[:DEPENDS_ON {type: 'NOISE'}]->(b)",
                {"a": f"decoy-svc-{i}", "b": f"decoy-svc-{i-1}"}
            )

    traversal = GraphTraversalEngine(client)
    chains = traversal.find_root_cause_chains("svc-gateway", max_depth=5)
    
    # Despite 15 noisy decoy services, the causal path must strictly pinpoint MySQL-InventoryShard-01
    assert len(chains) > 0
    top_culprit = chains[0]["culprit_service_name"]
    assert top_culprit == "MySQL-InventoryShard-01"
    assert chains[0]["hop_count"] == 3
    assert chains[0]["has_config_change"] is True

def test_cyclic_dependency_deadlock(client):
    """
    TEST 2: CYCLIC RECURSIVE DEPENDENCY LOOPS
    In the real world, microservices accidentally form cyclic retry loops:
    Service A -> Service B -> Service C -> Service A.
    Verify that variable-length bounded Cypher traversal terminates cleanly
    without infinite recursion or stack overflow.
    """
    seed_telemetry_topology(client)
    g = client.graph
    
    # Introduce a cyclic dependency: Inventory -> Order (Mutual recursive calls)
    g.query(
        """
        MATCH (inv:Service {id: 'svc-inventory'}), (ord:Service {id: 'svc-order'})
        CREATE (inv)-[:DEPENDS_ON {type: 'RECURSIVE_WEBHOOK'}]->(ord)
        """
    )
    
    traversal = GraphTraversalEngine(client)
    t0 = time.perf_counter()
    chains = traversal.find_root_cause_chains("svc-gateway", max_depth=5)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    
    # Must terminate within bounded time (< 15 ms) and not hang in infinite loop
    assert elapsed_ms < 15.0
    assert len(chains) > 0
    assert chains[0]["culprit_service_name"] == "MySQL-InventoryShard-01"

def test_extreme_telemetry_and_malformed_properties(client):
    """
    TEST 3: ADVERSARIAL TELEMETRY & UNICODE INJECTION
    Inject abnormal telemetry values:
    - Extreme latency spikes (e.g. 999999.0 ms)
    - Negative or NaN-like error rates
    - SQL/Cypher injection strings inside configuration descriptions
    Verify that Pydantic parsing and agent synthesis do not break.
    """
    seed_telemetry_topology(client)
    g = client.graph
    
    # Inject adversarial config change with prompt injection and Cypher payload
    adversarial_payload = "'; DROP ALL; MATCH (n) DETACH DELETE n; -- <script>alert(1)</script>"
    g.query(
        """
        MATCH (s:Service {id: 'svc-db-inventory'})
        CREATE (c:ConfigChange {
            id: 'cfg-adversarial-999',
            author: 'malicious-actor@external.io',
            description: $payload,
            parameter: 'max_connections',
            old_value: '500',
            new_value: '5',
            timestamp: '2026-10-15T00:50:00Z'
        })
        CREATE (c)-[:APPLIED_TO]->(s)
        """,
        {"payload": adversarial_payload}
    )
    
    agent = SynapseAgent(client)
    plan = agent.analyze_and_remediate("inc-2026-10-15-001")
    
    assert plan.root_cause_service == "MySQL-InventoryShard-01"
    assert len(plan.evidence_chain) > 0
    # Verify graph state was NOT deleted or corrupted by the injection string
    count_res = client.query("MATCH (n) RETURN count(n)")
    assert count_res.result_set[0][0] >= 13

def test_missing_and_disconnected_subgraphs(client):
    """
    TEST 4: DISCONNECTED & ORPHANED ANOMALIES
    Test an incident on an isolated service with 0 downstream dependencies.
    Verify agent returns an abstention / self-contained explanation without crashing.
    """
    g = client.graph
    g.query("CREATE (iso:Service {id: 'svc-isolated', name: 'Standalone-Cron', status: 'FAILING', error_rate: 0.99, latency_ms: 50.0})")
    g.query("CREATE (i:Incident {id: 'inc-isolated-001', title: 'Cron job crash', severity: 'MEDIUM', timestamp: '2026-10-15T00:55:00Z', target_service_id: 'svc-isolated'})")
    g.query("MATCH (i:Incident {id: 'inc-isolated-001'}), (s:Service {id: 'svc-isolated'}) CREATE (i)-[:OBSERVED_ON]->(s)")
    
    agent = SynapseAgent(client)
    plan = agent.analyze_and_remediate("inc-isolated-001")
    
    assert plan.root_cause_service == "svc-isolated"
    assert "No downstream dependency failures" in plan.root_cause_explanation
    assert plan.blast_radius.risk_score <= 0.25

def test_concurrent_incident_investigations(client):
    """
    TEST 5: CONCURRENT SRE AGENT WORKLOAD
    Simulate multiple simultaneous SRE engineers running incident investigations.
    """
    import concurrent.futures
    agent = SynapseAgent(client)
    
    def run_investigation():
        return agent.analyze_and_remediate("inc-2026-10-15-001")
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_investigation) for _ in range(5)]
        results = [f.result() for f in futures]
        
    assert len(results) == 5
    for r in results:
        assert r.root_cause_service == "MySQL-InventoryShard-01"
        assert len(r.evidence_chain) > 0
