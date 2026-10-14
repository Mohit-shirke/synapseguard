# Graph Data Model & Cypher Specification

## 1. Node Labels & Properties

### 1.1 `(:Service)`
Represents an individual microservice, gateway, cache, or database cluster.
- `id: STRING` (Unique identifier, e.g. `'svc-order'`)
- `name: STRING` (Human-readable name, e.g. `'Order-Service'`)
- `tier: STRING` (`'gateway'`, `'core'`, `'cache'`, `'database'`, `'worker'`)
- `error_rate: FLOAT` (Value between 0.0 and 1.0)
- `latency_ms: FLOAT` (Average service latency in milliseconds)
- `status: STRING` (`'HEALTHY'`, `'DEGRADED'`, `'FAILING'`)

### 1.2 `(:Incident)`
Represents an active anomaly, alert, or customer-impacting degradation.
- `id: STRING` (e.g. `'inc-2026-10-15-001'`)
- `title: STRING` (e.g. `'High HTTP 504 Gateway Timeouts on Checkout API'`)
- `severity: STRING` (`'CRITICAL'`, `'HIGH'`, `'MEDIUM'`, `'LOW'`)
- `timestamp: STRING` (ISO-8601 UTC timestamp)
- `target_service_id: STRING` (Service where symptoms first surfaced)

### 1.3 `(:ConfigChange)`
Represents an infrastructure parameter update, deployment, or schema migration.
- `id: STRING` (e.g. `'cfg-change-8912'`)
- `author: STRING` (e.g. `'devops-lead@company.internal'`)
- `description: STRING` (e.g. `'Tuned connection pool size for maintenance'`)
- `parameter: STRING` (e.g. `'max_connections'`)
- `old_value: STRING` (e.g. `'500'`)
- `new_value: STRING` (e.g. `'20'`)
- `timestamp: STRING` (ISO-8601 UTC timestamp)

### 1.4 `(:Host)`
Represents a physical host, Kubernetes node, or virtual machine.
- `id: STRING` (e.g. `'host-us-east-1b'`)
- `hostname: STRING` (e.g. `'k8s-node-02.us-east-1'`)
- `cpu_utilization: FLOAT` (Percentage, 0.0 to 100.0)
- `memory_utilization: FLOAT` (Percentage, 0.0 to 100.0)
- `zone: STRING` (e.g. `'us-east-1b'`)

---

## 2. Relationship Types & Directions

| Source Node | Relationship | Target Node | Properties | Semantics |
| :--- | :--- | :--- | :--- | :--- |
| `(:Service)` | `[:DEPENDS_ON]` | `(:Service)` | `type: STRING`, `rps: INT`, `timeout_ms: INT` | Synchronous or asynchronous RPC call edge |
| `(:Service)` | `[:DEPLOYED_ON]` | `(:Host)` | None | Host infrastructure placement |
| `(:ConfigChange)` | `[:APPLIED_TO]` | `(:Service)` | `environment: STRING` | Configuration mutation event |
| `(:Incident)` | `[:OBSERVED_ON]` | `(:Service)` | None | Symptom manifestation mapping |

---

## 3. Essential Cypher Queries & Complexity Analysis

### 3.1 Multi-Hop Root-Cause Isolation Query
```cypher
MATCH path = (symptom:Service {id: $symptom_id})-[:DEPENDS_ON*1..5]->(target:Service)
WHERE target.status = 'FAILING' OR target.error_rate > 0.5
OPTIONAL MATCH (cfg:ConfigChange)-[:APPLIED_TO]->(target)
OPTIONAL MATCH (target)-[:DEPLOYED_ON]->(h:Host)
RETURN 
    [n in nodes(path) | n.id] AS service_path,
    [n in nodes(path) | n.name] AS service_names,
    length(path) AS hop_count,
    target.id AS target_id,
    target.name AS target_name,
    target.error_rate AS target_error_rate,
    target.latency_ms AS target_latency,
    cfg.parameter AS config_param,
    cfg.old_value AS config_old,
    cfg.new_value AS config_new,
    h.hostname AS host_name
ORDER BY hop_count DESC, target.error_rate DESC;
```
- **Complexity:** $O(d^k)$ where $d$ is the maximum average degree and $k \le 5$ is bounded traversal depth. Thanks to FalkorDB's linear-algebra GraphBLAS sparse matrix representation, this runs in $< 5 \text{ ms}$ even on graphs with tens of thousands of services.

### 3.2 Upstream Blast Radius Reachability
```cypher
MATCH path = (upstream:Service)-[:DEPENDS_ON*1..5]->(culprit:Service {id: $culprit_id})
RETURN 
    upstream.id AS upstream_id,
    upstream.name AS upstream_name,
    length(path) AS hops_to_culprit;
```
- **Complexity:** $O(V_{sub} + E_{sub})$ over the reverse directed subgraph reachable from the culprit.

