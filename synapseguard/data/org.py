"""Who owns what, who is on call, who worked on it: the people layer of the graph.

Synthetic and clearly fictional (names, emails, rotations). Built from its own
fixed RNG so it never changes the incident scenarios or their ground truth, and
nothing in it feeds root-cause scoring. It answers the questions an on-call
engineer asks next: who do I wake up, who made the change, who knows this system.

    (:Person)-[:MEMBER_OF]->(:Team)-[:OWNS]->(:Service|:Host)
    (:Person)-[:ON_CALL {from, to}]->(:Team)        weekly rotation
    (:Person)-[:WORKED_ON {commits}]->(:Service)
    (:Person)-[:AUTHORED]->(:Change)                matched on Change.author == Person.email
"""
from __future__ import annotations

import random

from synapseguard.data.scenarios import EPOCH0

WEEK = 7 * 24 * 3600
ROTATION_START = EPOCH0 - 4 * WEEK
ROTATION_WEEKS = 120  # covers every seed used by tests, demos and benchmarks

# team id, display name, services owned, hosts owned
OTEL_TEAMS = [
    ("team-storefront", "Storefront", ["frontend-proxy", "frontend", "image-provider", "ad", "recommendation"], []),
    ("team-checkout", "Checkout & Payments", ["checkout", "payment", "email", "currency", "cart", "cache"], []),
    ("team-catalog", "Catalog & Fulfilment", ["product-catalog", "postgresql", "shipping", "quote"], []),
    ("team-platform", "Platform", ["queue", "flagd", "flagd-ui", "fraud-detection", "accounting"], []),
    ("team-ai", "AI Assistant", ["chatbot", "agent", "mcp"], []),
    ("team-sre", "SRE / Infrastructure", [], ["node-a", "node-b", "node-c", "node-d", "node-e", "node-f", "node-g"]),
]

# person id, name, email, team. alice@ and bob@ match change authors the scenario generator already uses.
OTEL_PEOPLE = [
    ("p-alice", "Alice Moreau", "alice@astronomy.shop", "team-checkout"),
    ("p-bob", "Bob Tanaka", "bob@astronomy.shop", "team-catalog"),
    ("p-chen", "Chen Wei", "chen@astronomy.shop", "team-storefront"),
    ("p-dara", "Dara Okafor", "dara@astronomy.shop", "team-storefront"),
    ("p-elif", "Elif Demir", "elif@astronomy.shop", "team-checkout"),
    ("p-farah", "Farah Siddiqui", "farah@astronomy.shop", "team-catalog"),
    ("p-gabe", "Gabe Lindqvist", "gabe@astronomy.shop", "team-platform"),
    ("p-hana", "Hana Novak", "hana@astronomy.shop", "team-platform"),
    ("p-ines", "Ines Duarte", "ines@astronomy.shop", "team-ai"),
    ("p-jon", "Jon Abara", "jon@astronomy.shop", "team-ai"),
    ("p-kiri", "Kiri Walker", "kiri@astronomy.shop", "team-sre"),
    ("p-luca", "Luca Ferri", "luca@astronomy.shop", "team-sre"),
]


def build_org(service_ids: list[str], host_ids: list[str], *, seed: int = 0) -> dict:
    """OTel topology gets the fixed org above; any other topology gets a generated one."""
    rng = random.Random(10_000 + seed)
    sset, hset = set(service_ids), set(host_ids)
    if {s for _, _, svcs, _ in OTEL_TEAMS for s in svcs} <= sset:
        teams = [(t, n, s, h) for t, n, s, h in OTEL_TEAMS]
        people = list(OTEL_PEOPLE)
    else:
        ordered = sorted(service_ids)
        k = max(2, len(ordered) // 40)
        teams = [(f"team-{i:03d}", f"Team {i}", ordered[i::k], []) for i in range(k)]
        teams.append(("team-sre", "SRE / Infrastructure", [], sorted(host_ids)))
        people = []
        for t, _, _, _ in teams:
            for j in range(3):
                pid = f"p-{t[5:]}-{j}"
                people.append((pid, f"Engineer {t[5:]}.{j}", f"{pid}@example.test", t))
    members: dict[str, list[str]] = {}
    for pid, _, _, team in people:
        members.setdefault(team, []).append(pid)

    owns = [{"team": t, "target": s, "label": "Service"} for t, _, svcs, _ in teams for s in svcs if s in sset]
    owns += [{"team": t, "target": h, "label": "Host"} for t, _, _, hosts in teams for h in hosts if h in hset]
    oncall = []
    for t, _, _, _ in teams:
        rota = members.get(t, [])
        for w in range(ROTATION_WEEKS):
            if rota:
                oncall.append({"person": rota[w % len(rota)], "team": t,
                               "from": ROTATION_START + w * WEEK, "to": ROTATION_START + (w + 1) * WEEK})
    team_of = {s: t for t, _, svcs, _ in teams for s in svcs}
    worked = []
    for pid, _, _, team in people:
        own = [s for s, t in team_of.items() if t == team]
        other = [s for s in service_ids if team_of.get(s) != team]
        for s in own:
            worked.append({"person": pid, "service": s, "commits": rng.randint(5, 120)})
        for s in rng.sample(other, min(2, len(other))):
            worked.append({"person": pid, "service": s, "commits": rng.randint(1, 15)})
    return {
        "teams": [{"id": t, "name": n} for t, n, _, _ in teams],
        "people": [{"id": p, "name": n, "email": e, "team": t} for p, n, e, t in people],
        "owns": owns, "oncall": oncall, "worked_on": worked,
    }
