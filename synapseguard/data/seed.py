import datetime
from synapseguard.core.db import FalkorClient

def seed_telemetry_topology(client: FalkorClient):
    """
    Populates FalkorDB with a realistic distributed microservice architecture 
    experiencing a multi-hop cascading failure.
    
    Architecture:
    Client/User -> API Gateway -> Order Service -> Payment Service -> Stripe Gateway
                                -> Inventory Service -> Database Cluster (MySQL Shard 01)
                                -> Notification Service -> Kafka Queue
                                
    Incident Scenario:
    A developer pushed a ConfigChange to 'MySQL Shard 01' reducing max_connections from 500 to 20.
    1. MySQL Shard 01 starts rejecting connections.
    2. Inventory Service runs out of DB connections, starts timing out (latency jumps to 4500ms, error_rate 88%).
    3. Order Service, depending synchronously on Inventory Service, enters retry storm.
    4. API Gateway starts returning 504 Gateway Timeout to external clients.
    5. Naive log search spots '504 in API Gateway' and blames API Gateway.
    6. FalkorDB multi-hop traversal isolates root cause 4 hops deep to MySQL Shard 01 config change!
    """
    client.reset_graph()
    g = client.graph
    
    # 1. Create Services
    services = [
        {"id": "svc-gateway", "name": "API-Gateway", "tier": "gateway", "error_rate": 0.45, "latency_ms": 3200.0, "status": "FAILING"},
        {"id": "svc-order", "name": "Order-Service", "tier": "core", "error_rate": 0.65, "latency_ms": 2800.0, "status": "FAILING"},
        {"id": "svc-payment", "name": "Payment-Service", "tier": "core", "error_rate": 0.02, "latency_ms": 110.0, "status": "HEALTHY"},
        {"id": "svc-inventory", "name": "Inventory-Service", "tier": "core", "error_rate": 0.88, "latency_ms": 4500.0, "status": "FAILING"},
        {"id": "svc-notify", "name": "Notification-Service", "tier": "worker", "error_rate": 0.01, "latency_ms": 45.0, "status": "HEALTHY"},
        {"id": "svc-db-order", "name": "Postgres-OrderDB", "tier": "database", "error_rate": 0.00, "latency_ms": 12.0, "status": "HEALTHY"},
        {"id": "svc-db-inventory", "name": "MySQL-InventoryShard-01", "tier": "database", "error_rate": 0.95, "latency_ms": 5000.0, "status": "FAILING"},
        {"id": "svc-redis-cache", "name": "Redis-GlobalCache", "tier": "cache", "error_rate": 0.00, "latency_ms": 2.0, "status": "HEALTHY"}
    ]
    
    for s in services:
        g.query(
            "CREATE (:Service {id: $id, name: $name, tier: $tier, error_rate: $error_rate, latency_ms: $latency_ms, status: $status})",
            s
        )
        
    # 2. Create Infrastructure Hosts
    hosts = [
        {"id": "host-us-east-1a", "hostname": "k8s-node-01.us-east-1", "cpu": 45.2, "mem": 62.1, "zone": "us-east-1a"},
        {"id": "host-us-east-1b", "hostname": "k8s-node-02.us-east-1", "cpu": 92.5, "mem": 89.4, "zone": "us-east-1b"},
        {"id": "host-db-dedicated", "hostname": "rds-mysql-shard01.internal", "cpu": 98.2, "mem": 94.0, "zone": "us-east-1b"}
    ]
    for h in hosts:
        g.query(
            "CREATE (:Host {id: $id, hostname: $hostname, cpu_utilization: $cpu, memory_utilization: $mem, zone: $zone})",
            h
        )
        
    # 3. Create Dependencies (CALLS / DEPENDS_ON)
    dependencies = [
        ("svc-gateway", "svc-order", "CALLS_SYNC", 2500),
        ("svc-order", "svc-payment", "CALLS_SYNC", 800),
        ("svc-order", "svc-inventory", "CALLS_SYNC", 2500),
        ("svc-order", "svc-db-order", "READS_WRITES", 1200),
        ("svc-order", "svc-notify", "CALLS_ASYNC", 400),
        ("svc-inventory", "svc-db-inventory", "READS_WRITES", 3500),
        ("svc-inventory", "svc-redis-cache", "READS_CACHE", 4500)
    ]
    for src, dst, rel, rps in dependencies:
        g.query(
            f"""
            MATCH (a:Service {{id: $src}}), (b:Service {{id: $dst}})
            CREATE (a)-[:DEPENDS_ON {{type: $rel, rps: $rps, timeout_ms: 3000}}]->(b)
            """,
            {"src": src, "dst": dst, "rel": rel, "rps": rps}
        )
        
    # 4. Host Placements (DEPLOYED_ON)
    deployments = [
        ("svc-gateway", "host-us-east-1a"),
        ("svc-order", "host-us-east-1a"),
        ("svc-inventory", "host-us-east-1b"),
        ("svc-payment", "host-us-east-1a"),
        ("svc-db-inventory", "host-db-dedicated")
    ]
    for s_id, h_id in deployments:
        g.query(
            """
            MATCH (s:Service {id: $s_id}), (h:Host {id: $h_id})
            CREATE (s)-[:DEPLOYED_ON]->(h)
            """,
            {"s_id": s_id, "h_id": h_id}
        )
        
    # 5. Create ConfigChange (The Root Cause Event)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    g.query(
        """
        CREATE (c:ConfigChange {
            id: 'cfg-change-8912',
            author: 'devops-lead@company.internal',
            description: 'Tuned connection pool size for maintenance',
            parameter: 'max_connections',
            old_value: '500',
            new_value: '20',
            timestamp: $ts
        })
        """,
        {"ts": now_iso}
    )
    g.query(
        """
        MATCH (c:ConfigChange {id: 'cfg-change-8912'}), (s:Service {id: 'svc-db-inventory'})
        CREATE (c)-[:APPLIED_TO {environment: 'production'}]->(s)
        """
    )
    
    # 6. Create Triggering Incident on API Gateway (Symptom observed by users)
    g.query(
        """
        CREATE (i:Incident {
            id: 'inc-2026-10-15-001',
            title: 'High HTTP 504 Gateway Timeouts on Checkout API',
            severity: 'CRITICAL',
            timestamp: $ts,
            target_service_id: 'svc-gateway'
        })
        """,
        {"ts": now_iso}
    )
    g.query(
        """
        MATCH (i:Incident {id: 'inc-2026-10-15-001'}), (s:Service {id: 'svc-gateway'})
        CREATE (i)-[:OBSERVED_ON]->(s)
        """
    )
    
    return {"status": "success", "nodes_seeded": len(services) + len(hosts) + 2}

