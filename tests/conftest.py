"""Pytest fixtures: ephemeral MongoDB container + freshly seeded skill graph."""

import os
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient
from testcontainers.mongodb import MongoDbContainer

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


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
    """Clear skills/edges/runs before each test for isolation."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client["skill_graph"]
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
