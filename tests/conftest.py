"""Pytest fixtures: ephemeral MongoDB container + freshly seeded skill graph.

Two env vars are set HERE at module import time, BEFORE any fixture or
target module loads, so module-level constants in server.py / seed.py /
scripts capture the test values:

  SKILL_GRAPH_DB         → `skill_graph_test`, isolated from the
                           user's working `skill_graph` database. Pytest
                           runs can never silently overwrite the real DB.
  SKILL_GRAPH_LOCAL_DIR  → a guaranteed-empty sentinel path, isolated
                           from the user's real `~/.skill-graph-local/`.
                           Tests can opt-in to a fixture overlay via
                           `with_local_dir`.

Setting these in fixtures (even autouse session-scoped) is too late —
pytest's fixture resolution order is not guaranteed to run an
autouse-session fixture before another session-scoped fixture that
imports the target module.
"""

import os
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient
from testcontainers.mongodb import MongoDbContainer

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Set BEFORE any target module is imported. server.py / seed.py /
# scripts read these into module-level constants at import time.
# Forced (not setdefault) so a populated env from the user's shell
# can't leak into tests.
os.environ["SKILL_GRAPH_LOCAL_DIR"] = str(REPO_ROOT / "tests" / "_no_local_overlay_sentinel")
os.environ["SKILL_GRAPH_DB"] = "skill_graph_test"


@pytest.fixture(scope="session")
def mongo_container():
    """Start a Mongo testcontainer, unless MONGODB_URI is already set.

    Honoring a preset URI keeps CI fast (it provides mongo as a service
    container) and lets local devs point at an existing daemon without
    needing Docker for nested test runs.
    """
    preset = os.environ.get("MONGODB_URI")
    if preset:
        yield preset
        return
    with MongoDbContainer("mongo:7") as container:
        os.environ["MONGODB_URI"] = container.get_connection_url()
        yield container


@pytest.fixture(scope="session")
def server_module(mongo_container):
    import server  # imported after MONGODB_URI is set
    return server


@pytest.fixture(scope="session")
def seed_module(mongo_container):
    import seed
    return seed


@pytest.fixture(autouse=True)
def reset_db(mongo_container):
    """Clear skills/edges/runs before each test for isolation.
    Targets `skill_graph_test` (per `_isolate_test_database`) so
    tests can never collide with the user's working `skill_graph`."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    db.skills.drop()
    db.edges.drop()
    db.runs.drop()
    yield db
    client.close()


@pytest.fixture
def seeded(reset_db, seed_module, server_module):
    seed_module.seed()
    return server_module


def _call(tool, **kwargs):
    """FastMCP may wrap the function in a Tool object exposing .fn."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


@pytest.fixture
def call():
    """Helper for tests: `call(seeded.tool, **kwargs)`."""
    return _call
