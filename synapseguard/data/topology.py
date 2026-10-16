"""Service topologies.

``otel_demo_topology()`` reproduces the service-call graph of the public
OpenTelemetry Demo ("Astronomy Shop", Apache-2.0,
https://opentelemetry.io/docs/demo/architecture/). Service names, call
directions and protocols follow that diagram. Host placement, request rates and
baseline metrics are **synthetic** (the real demo runs on a single Docker host).

``layered_topology()`` generates seeded synthetic meshes of any size for
scale benchmarks.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Topology:
    services: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    hosts: list[dict] = field(default_factory=list)

    def service_ids(self) -> list[str]:
        return [s["id"] for s in self.services]

    def callers(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {s["id"]: [] for s in self.services}
        for e in self.edges:
            out[e["dst"]].append(e)
        return out

    def callees(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {s["id"]: [] for s in self.services}
        for e in self.edges:
            out[e["src"]].append(e)
        return out


# (id, display name, kind, language, host)
_OTEL_SERVICES = [
    ("frontend-proxy", "Frontend Proxy (Envoy)", "proxy", "cpp", "node-a"),
    ("frontend", "Frontend", "service", "typescript", "node-a"),
    ("image-provider", "Image Provider (nginx)", "service", "cpp", "node-a"),
    ("ad", "Ad", "service", "java", "node-c"),
    ("cart", "Cart", "service", "dotnet", "node-c"),
    ("cache", "Cache (Valkey)", "cache", "valkey", "node-c"),
    ("recommendation", "Recommendation", "service", "python", "node-c"),
    ("checkout", "Checkout", "service", "golang", "node-b"),
    ("payment", "Payment", "service", "javascript", "node-b"),
    ("email", "Email", "service", "ruby", "node-b"),
    ("currency", "Currency", "service", "cpp", "node-b"),
    ("product-catalog", "Product Catalog", "service", "golang", "node-d"),
    ("shipping", "Shipping", "service", "rust", "node-d"),
    ("quote", "Quote", "service", "php", "node-d"),
    ("postgresql", "astronomy-db (PostgreSQL)", "database", "postgresql", "node-e"),
    ("accounting", "Accounting", "service", "dotnet", "node-e"),
    ("queue", "Queue (Kafka)", "queue", "java", "node-f"),
    ("fraud-detection", "Fraud Detection", "service", "kotlin", "node-f"),
    ("flagd", "Flagd", "flags", "golang", "node-f"),
    ("flagd-ui", "Flagd UI", "service", "elixir", "node-f"),
    ("chatbot", "Chatbot", "service", "python", "node-g"),
    ("agent", "Agent", "service", "python", "node-g"),
    ("mcp", "MCP", "service", "python", "node-g"),
]

# (caller, callee, protocol, sync, rps, has_fallback)
# Direction = "caller depends on callee". Kafka consumers are modelled as
# depending on the queue (sync from the consumer's point of view); the
# checkout -> queue publish is asynchronous and does not propagate latency.
_OTEL_EDGES = [
    ("frontend-proxy", "frontend", "http", True, 900, False),
    ("frontend-proxy", "flagd", "grpc", True, 300, True),
    ("frontend-proxy", "flagd-ui", "http", True, 5, False),
    ("frontend-proxy", "image-provider", "http", True, 600, False),
    ("frontend-proxy", "chatbot", "http", True, 20, False),
    ("chatbot", "agent", "http", True, 20, False),
    ("agent", "frontend", "http", True, 15, False),
    ("agent", "mcp", "http", True, 15, False),
    ("mcp", "frontend", "http", True, 15, False),
    ("frontend", "ad", "grpc", True, 400, True),
    ("frontend", "cart", "grpc", True, 350, False),
    ("frontend", "currency", "grpc", True, 500, False),
    ("frontend", "checkout", "grpc", True, 120, False),
    ("frontend", "shipping", "http", True, 150, False),
    ("frontend", "product-catalog", "grpc", True, 700, False),
    ("frontend", "recommendation", "grpc", True, 300, True),
    ("checkout", "currency", "grpc", True, 120, False),
    ("checkout", "cart", "grpc", True, 120, False),
    ("checkout", "payment", "grpc", True, 120, False),
    ("checkout", "email", "http", True, 120, False),
    ("checkout", "queue", "kafka", False, 120, False),
    ("checkout", "product-catalog", "grpc", True, 240, False),
    ("checkout", "shipping", "http", True, 120, False),
    ("cart", "cache", "redis", True, 470, False),
    ("cart", "flagd", "grpc", True, 470, True),
    ("shipping", "quote", "http", True, 270, False),
    ("ad", "flagd", "grpc", True, 400, True),
    ("payment", "flagd", "grpc", True, 120, True),
    ("fraud-detection", "flagd", "grpc", True, 120, True),
    ("fraud-detection", "queue", "kafka", True, 120, False),
    ("accounting", "queue", "kafka", True, 120, False),
    ("recommendation", "product-catalog", "grpc", True, 300, False),
    ("recommendation", "flagd", "grpc", True, 300, True),
    ("product-catalog", "postgresql", "sql", True, 1240, False),
    ("accounting", "postgresql", "sql", True, 120, False),
]

_HOSTS = [
    ("node-a", "us-east-1a"),
    ("node-b", "us-east-1a"),
    ("node-c", "us-east-1b"),
    ("node-d", "us-east-1b"),
    ("node-e", "us-east-1c"),
    ("node-f", "us-east-1c"),
    ("node-g", "us-east-1c"),
]

# Baseline p99 latency (ms) by service kind.
BASE_P99 = {"proxy": 40.0, "service": 60.0, "cache": 3.0, "database": 15.0, "queue": 10.0, "flags": 5.0}

# Official OTel Demo flagd feature flags per service (from
# https://opentelemetry.io/docs/demo/feature-flags/), used to name realistic
# feature-flag change events in generated scenarios.
OTEL_FLAGS = {
    "ad": ["adFailure", "adHighCpu", "adManualGc"],
    "cart": ["cartFailure"],
    "email": ["emailMemoryLeak"],
    "payment": ["paymentFailure"],
    "checkout": ["paymentUnreachable", "kafkaQueueProblems"],
    "product-catalog": ["productCatalogFailure", "productCatalogLockContention"],
    "recommendation": ["recommendationCacheFailure"],
    "shipping": ["intlShippingSlowdown"],
    "fraud-detection": ["kafkaConsumerDead"],
    "frontend": ["imageSlowLoad"],
}


def otel_demo_topology() -> Topology:
    services = []
    for sid, name, kind, lang, host in _OTEL_SERVICES:
        services.append({
            "id": sid,
            "name": name,
            "kind": kind,
            "language": lang,
            "host": host,
            "entrypoint": sid == "frontend-proxy",
            "criticality": "high" if sid in {"frontend-proxy", "frontend", "checkout", "payment", "cart"} else "normal",
        })
    # Breaker capability (synthetic assumption): Envoy (frontend-proxy) and gRPC
    # clients can circuit-break; plain HTTP/SQL/Kafka clients here cannot.
    edges = [
        {"src": a, "dst": b, "protocol": p, "sync": s, "rps": r, "has_fallback": f,
         "breaker": p == "grpc" or a == "frontend-proxy"}
        for a, b, p, s, r, f in _OTEL_EDGES
    ]
    hosts = [{"id": h, "name": f"k8s-{h}", "zone": z} for h, z in _HOSTS]
    return Topology(services, edges, hosts)


def layered_topology(n_services: int, seed: int = 0, avg_out_degree: float = 2.5) -> Topology:
    """Seeded layered DAG of ``n_services`` services.

    Layer 0 holds entrypoints; the deepest layer holds data stores. Callees
    are chosen with preferential attachment so shared dependencies (databases,
    caches, auth) get high in-degree, as in real meshes.
    """
    if n_services < 4:
        raise ValueError("need at least 4 services")
    rng = random.Random(seed)
    n_layers = max(4, min(10, int(math.log2(n_services)) + 1))
    n_entry = max(1, n_services // 200)
    layer_of: list[int] = [0] * n_entry
    for _ in range(n_services - n_entry):
        layer_of.append(rng.randint(1, n_layers - 1))
    layers: dict[int, list[int]] = {i: [] for i in range(n_layers)}
    for i, l in enumerate(layer_of):
        layers[l].append(i)
    for l in range(1, n_layers):  # never leave a layer empty
        if not layers[l]:
            donor = max(range(1, n_layers), key=lambda k: len(layers[k]))
            layers[l].append(layers[donor].pop())
    for l, members in layers.items():
        for i in members:
            layer_of[i] = l

    def sid(i: int) -> str:
        return f"svc-{i:05d}"

    services = []
    for i in range(n_services):
        l = layer_of[i]
        if l == 0:
            kind = "proxy"
        elif l == n_layers - 1:
            kind = rng.choice(["database", "database", "cache", "queue"])
        else:
            kind = "service"
        services.append({
            "id": sid(i), "name": f"{kind}-{i}", "kind": kind, "language": "synthetic",
            "host": f"host-{rng.randrange(max(2, n_services // 5)):04d}",
            "entrypoint": l == 0, "criticality": "high" if l <= 1 else "normal",
        })

    popularity = [1.0] * n_services
    edges: list[dict] = []
    seen: set[tuple[int, int]] = set()
    has_caller = [False] * n_services

    def add_edge(a: int, b: int) -> None:
        if (a, b) in seen or a == b:
            return
        seen.add((a, b))
        has_caller[b] = True
        popularity[b] += 1.0
        sync = rng.random() < 0.85
        edges.append({
            "src": sid(a), "dst": sid(b), "protocol": rng.choice(["grpc", "http"]) if sync else "kafka",
            "sync": sync, "rps": rng.randint(10, 1000), "has_fallback": rng.random() < 0.15,
            "breaker": rng.random() < 0.7,
        })

    for l in range(n_layers - 1):
        deeper = [j for k in range(l + 1, n_layers) for j in layers[k]]
        nxt = layers[l + 1]
        for i in layers[l]:
            k = max(1, int(rng.expovariate(1.0 / avg_out_degree)))
            for _ in range(k):
                pool = nxt if rng.random() < 0.6 else deeper
                weights = [popularity[j] for j in pool]
                add_edge(i, rng.choices(pool, weights=weights, k=1)[0])
    for l in range(1, n_layers):  # every non-entry service has a caller
        for j in layers[l]:
            if not has_caller[j]:
                add_edge(rng.choice(layers[l - 1]), j)

    host_ids = sorted({s["host"] for s in services})
    hosts = [{"id": h, "name": h, "zone": f"zone-{k % 3}"} for k, h in enumerate(host_ids)]
    return Topology(services, edges, hosts)
