"""Local-only skill overlay (v2.5.0).

Validates the mechanism that lets users load private skills from a
directory outside the repo (typically ~/.skill-graph-local/) without
risking git leakage. Fixtures live in tests/fixtures/local-overlay/
and are synthetic — no real local content.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "local-overlay"


@pytest.fixture
def with_local_dir(reset_db, seed_module, server_module, monkeypatch):
    """Point seed at the fixture overlay dir, re-seed, and re-import
    the module so LOCAL_DIR is recomputed from the env var."""
    monkeypatch.setenv("SKILL_GRAPH_LOCAL_DIR", str(FIXTURE_DIR))
    # Re-import seed module so its module-level LOCAL_DIR picks up the env
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    fresh_seed = importlib.reload(seed_module)
    fresh_seed.seed()
    return fresh_seed


@pytest.fixture
def with_no_local_dir(reset_db, seed_module, monkeypatch):
    """Run a clean seed without any LOCAL_DIR set."""
    monkeypatch.delenv("SKILL_GRAPH_LOCAL_DIR", raising=False)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    fresh_seed = importlib.reload(seed_module)
    fresh_seed.seed()
    return fresh_seed


def test_local_overlay_unset_no_change(with_no_local_dir):
    """Without SKILL_GRAPH_LOCAL_DIR pointing at a real dir, seed
    behaves exactly as before — no local skills loaded."""
    db = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]
    locals_count = db.skills.count_documents({"_id": {"$regex": "^skill:local:"}})
    db.client.close()
    assert locals_count == 0


def test_local_overlay_loads_fixture_skill(with_local_dir):
    """With the fixture dir, the synthetic local skill lands in db."""
    db = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]
    fixture = db.skills.find_one({"_id": "skill:local:fixture"})
    db.client.close()
    assert fixture is not None
    assert fixture["name"] == "Local Fixture Skill"
    assert fixture["lifecycle"] == "active"
    # v1-compat fields derived
    assert fixture["input_type"] == "fixture_query"
    assert fixture["output_type"] == "fixture_artifact"


def test_local_overlay_idempotent_on_reseed(with_local_dir):
    """Re-running seed (with the same fixture) doesn't duplicate."""
    db = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]
    before = db.skills.count_documents({"_id": "skill:local:fixture"})
    with_local_dir.seed()
    after = db.skills.count_documents({"_id": "skill:local:fixture"})
    db.client.close()
    assert before == after == 1


def test_local_overlay_rejects_non_local_namespace(reset_db, seed_module,
                                                    monkeypatch, tmp_path):
    """A local-overlay skills.json that tries to define a canonical-id
    skill is rejected with a clear message; canonical seed still loads."""
    bad_dir = tmp_path / "bad-overlay"
    bad_dir.mkdir()
    (bad_dir / "skills.json").write_text("""
    {"skills": [{"_id": "skill:my-canonical-impostor", "name": "Bad",
                 "version": "0.0.1", "lifecycle": "active",
                 "input": {"type": "x"}, "output": {"type": "y"},
                 "dependencies": []}],
     "edges": []}
    """)
    monkeypatch.setenv("SKILL_GRAPH_LOCAL_DIR", str(bad_dir))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    fresh = importlib.reload(seed_module)
    fresh.seed()  # Must not raise
    db = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]
    impostor = db.skills.count_documents({"_id": "skill:my-canonical-impostor"})
    canonical = db.skills.count_documents({"_id": "skill:query-analysis"})
    db.client.close()
    assert impostor == 0, "non-local-namespace skill should be rejected"
    assert canonical == 1, "canonical seed should be unaffected"


def test_local_overlay_invalid_json_doesnt_block_canonical(reset_db, seed_module,
                                                            monkeypatch, tmp_path):
    """Malformed local skills.json prints a warning; canonical seed completes."""
    bad_dir = tmp_path / "broken-overlay"
    bad_dir.mkdir()
    (bad_dir / "skills.json").write_text("{ this is not valid json")
    monkeypatch.setenv("SKILL_GRAPH_LOCAL_DIR", str(bad_dir))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    fresh = importlib.reload(seed_module)
    fresh.seed()  # Must not raise
    db = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]
    canonical = db.skills.count_documents({"_id": "skill:query-analysis"})
    db.client.close()
    assert canonical == 1


def test_get_skill_instructions_resolves_local_path(with_local_dir, call):
    """The fixture skill's skill_path resolves under LOCAL_DIR (not
    REPO_ROOT) — get_skill_instructions accepts both trusted roots
    and returns the body."""
    # Re-import server so TRUSTED_ROOTS picks up LOCAL_DIR
    import server
    fresh_server = importlib.reload(server)
    r = call(fresh_server.get_skill_instructions, skill_id="skill:local:fixture")
    assert "error" not in r
    assert "Local Fixture Skill" in r["content"]
    assert r["source"] == "graph"
