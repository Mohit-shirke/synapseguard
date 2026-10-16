"""Blast radius and max-flow/min-cut invariants."""
import networkx as nx
import pytest

from synapseguard.data.loader import BREAKER_INF, load_scenario
from synapseguard.data.scenarios import generate
from synapseguard.data.topology import layered_topology
from synapseguard.engine import containment as C


@pytest.mark.parametrize("seed", range(15))
def test_blast_radius_matches_networkx_ancestors(fresh, otel, seed):
    sc = generate(otel, seed)
    load_scenario(fresh, sc)
    g = nx.DiGraph((e["src"], e["dst"]) for e in sc.edges)
    root = sc.truth["root_id"] if sc.truth["root_label"] == "Service" else None
    if root is None:
        return
    blast = C.blast_radius(fresh, [root])
    assert {i["id"] for i in blast["impacted"]} == nx.ancestors(g, root)


@pytest.mark.parametrize("seed", range(20))
def test_min_cut_equals_max_flow_and_separates(fresh, seed):
    sc = generate(layered_topology(120, seed=seed), seed, "deploy_bug")
    load_scenario(fresh, sc)
    roots = [sc.truth["root_id"]]
    blast = C.blast_radius(fresh, roots)
    plan = C.breaker_plan(fresh, roots, blast)
    if plan["status"] != "ok":
        assert plan["status"] in ("no_entrypoint_exposed", "no_breaker_cut")
        return
    assert plan["cut_cost"] == pytest.approx(plan["max_flow"])
    assert all(e["breaker"] for e in plan["cut"]) and plan["cut_cost"] < BREAKER_INF
    # removing the cut must disconnect every entrypoint from the root
    g = nx.DiGraph((e["src"], e["dst"]) for e in sc.edges)
    g.remove_edges_from((e["src"], e["dst"]) for e in plan["cut"])
    for s in blast["entrypoints"]:
        assert not nx.has_path(g, s, roots[0])
    # never worse than breaking every direct caller, when that is possible
    if plan["naive"]["feasible"]:
        assert plan["cut_cost"] <= plan["naive"]["cost"] + 1e-6


def test_breakers_stay_near_the_fault_on_otel(fresh, otel):
    sc = generate(otel, 5000, "silent", "postgresql")
    load_scenario(fresh, sc)
    blast = C.blast_radius(fresh, ["postgresql"])
    plan = C.breaker_plan(fresh, ["postgresql"], blast)
    assert plan["status"] == "ok"
    assert all(e["dst"] == "product-catalog" for e in plan["cut"]), plan["cut"]


def test_naive_plan_sees_direct_callers(fresh, otel):
    """Regression for a FalkorDB 6.0.2 wrong-result bug: two MATCH clauses with WHERE
    filters and no WITH in between returned no rows (see docs/known-issues.md)."""
    sc = generate(otel, 5000, "silent", "postgresql")
    load_scenario(fresh, sc)
    naive = C._naive_plan(fresh, ["postgresql"])
    assert {e["src"] for e in naive["edges"]} == {"product-catalog", "accounting"}
