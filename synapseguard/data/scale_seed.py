import random
from typing import Dict, Any
from synapseguard.core.db import FalkorClient

def seed_large_scale_topology(client: FalkorClient, num_services: int = 100, num_edges: int = 250) -> Dict[str, Any]:
    """
    Generates an enterprise-scale synthetic microservice mesh in FalkorDB to test
    adversarial stress, high degree distribution, and deep dependency paths.
    """
    client.reset_graph()
    g = client.graph
    
    # 1. Create Gateway
    g.query("CREATE (:Service {id: 'svc-gw-0', name: 'Global-API-Gateway', tier: 'gateway', error_rate: 0.85, latency_ms: 4200.0, status: 'FAILING'})")
    
    # 2. Create Services in bulk
    tiers = ['api', 'core', 'cache', 'database', 'worker', 'messaging']
    for i in range(1, num_services):
        tier = random.choice(tiers)
        status = 'FAILING' if i == 42 else ('DEGRADED' if random.random() < 0.05 else 'HEALTHY')
        err_rate = 0.98 if i == 42 else (0.4 if status == 'DEGRADED' else 0.001)
        lat = 6000.0 if i == 42 else (random.uniform(5.0, 150.0))
        g.query(
            "CREATE (:Service {id: $id, name: $name, tier: $tier, error_rate: $error_rate, latency_ms: $lat, status: $status})",
            {"id": f"svc-{i}", "name": f"Service-{tier}-{i}", "tier": tier, "error_rate": err_rate, "lat": lat, "status": status}
        )
        
    # 3. Create a deterministic 5-hop causal failure path to svc-42
    # gw-0 -> svc-10 -> svc-20 -> svc-30 -> svc-42 (Culprit)
    causal_path = ['svc-gw-0', 'svc-10', 'svc-20', 'svc-30', 'svc-42']
    for j in range(len(causal_path) - 1):
        g.query(
            """
            MATCH (a:Service {id: $src}), (b:Service {id: $dst})
            CREATE (a)-[:DEPENDS_ON {type: 'CALLS_SYNC', rps: 1000, timeout_ms: 3000}]->(b)
            """,
            {"src": causal_path[j], "dst": causal_path[j+1]}
        )
        
    # 4. Attach root cause ConfigChange to svc-42
    g.query(
        """
        CREATE (c:ConfigChange {
            id: 'cfg-enterprise-42',
            author: 'sre-automation@enterprise.net',
            description: 'Decreased threadpool allocation during deployment',
            parameter: 'threadpool_worker_count',
            old_value: '256',
            new_value: '8',
            timestamp: '2026-10-15T00:00:00Z'
        })
        """
    )
    g.query(
        """
        MATCH (c:ConfigChange {id: 'cfg-enterprise-42'}), (s:Service {id: 'svc-42'})
        CREATE (c)-[:APPLIED_TO {environment: 'production'}]->(s)
        """
    )
    
    # 5. Create Triggering Incident on Global-API-Gateway
    g.query(
        """
        CREATE (i:Incident {
            id: 'inc-scale-100',
            title: 'Enterprise Checkout Outage: 504 Timeouts',
            severity: 'CRITICAL',
            timestamp: '2026-10-15T00:01:00Z',
            target_service_id: 'svc-gw-0'
        })
        """
    )
    g.query(
        """
        MATCH (i:Incident {id: 'inc-scale-100'}), (s:Service {id: 'svc-gw-0'})
        CREATE (i)-[:OBSERVED_ON]->(s)
        """
    )
    
    # 6. Add random background dependencies to simulate complex graph noise
    for _ in range(num_edges):
        src_id = f"svc-{random.randint(0, num_services - 1)}" if random.random() > 0.1 else 'svc-gw-0'
        dst_id = f"svc-{random.randint(1, num_services - 1)}"
        if src_id != dst_id:
            try:
                g.query(
                    """
                    MATCH (a:Service {id: $src}), (b:Service {id: $dst})
                    CREATE (a)-[:DEPENDS_ON {type: 'CALLS_ASYNC', rps: 200, timeout_ms: 5000}]->(b)
                    """,
                    {"src": src_id, "dst": dst_id}
                )
            except Exception:
                pass
                
    return {"services_seeded": num_services, "causal_culprit": "svc-42"}

