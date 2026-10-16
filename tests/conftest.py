import os
import uuid

import pytest

from synapseguard.core.graph import GraphStore
from synapseguard.data.loader import load_scenario
from synapseguard.data.scenarios import generate
from synapseguard.data.topology import otel_demo_topology


@pytest.fixture(scope="session")
def store():
    s = GraphStore(f"sg_test_{uuid.uuid4().hex[:8]}")
    if not s.ping():
        pytest.exit("FalkorDB is not reachable on FALKORDB_HOST/PORT; start it with docker compose up -d falkordb",
                    returncode=2)
    yield s
    s.drop()


@pytest.fixture
def fresh(store):
    """Empty graph for each test that needs one."""
    store.drop()
    store.ensure_indexes()
    return store


@pytest.fixture(scope="session")
def otel():
    return otel_demo_topology()


@pytest.fixture
def loaded(fresh, otel):
    sc = generate(otel, 1, "deploy_bug", "chatbot")
    load_scenario(fresh, sc)
    return fresh, sc


def pytest_configure(config):
    os.environ.setdefault("SG_LLM", "0")
