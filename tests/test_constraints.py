"""constraints collection — Layer 2 semantic validation foundation (v2.9).

Tests cover:
- Seed data present and correct shape
- JSON Schema validator enforces required fields and severity enum
- Indexes created
- violation_paraphrase is distinct from rule_text (the two-representation invariant)
- extract_fact_summary script is importable and handles missing API key gracefully
"""

import os

import pytest
from pymongo import MongoClient
from pymongo.errors import WriteError


def test_constraints_seeded(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    count = db.constraints.count_documents({})
    client.close()
    assert count == 11


def test_constraint_no_green_on_white_present(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    c = db.constraints.find_one({"_id": "constraint:leafygreen-ui:no-green-on-white"})
    client.close()
    assert c is not None
    assert c["skill_id"] == "skill:leafygreen-ui"
    assert c["severity"] == "fail"
    assert c["category"] == "accessibility"
    assert "rule_text" in c
    assert "violation_paraphrase" in c


def test_constraint_two_representation_invariant(seeded):
    """violation_paraphrase must be different from rule_text.

    The two-representation design requires the paraphrase to be a positive
    description of what a violation looks like, not the negation-phrased rule.
    If they're identical the embedding comparison degrades to naive matching.
    """
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    for c in db.constraints.find({}):
        assert c["rule_text"] != c["violation_paraphrase"], (
            f"{c['_id']}: rule_text and violation_paraphrase must differ"
        )
    client.close()


def test_constraint_embedding_null_until_seeded(seeded):
    """constraint_embedding should be null until seed_constraint_embeddings.py runs."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    for c in db.constraints.find({}):
        assert c.get("constraint_embedding") is None, (
            f"{c['_id']}: embedding should be null before Voyage AI seeding"
        )
    client.close()


def test_constraints_indexes_present(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    indexes = {ix["name"] for ix in db.constraints.list_indexes()}
    client.close()
    assert "skill_id_1" in indexes
    assert "severity_1" in indexes
    assert "skill_id_1_category_1" in indexes


def test_constraint_validator_rejects_invalid_severity(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    bad = {
        "_id": "constraint:test:bad-severity",
        "skill_id": "skill:test",
        "rule_text": "some rule",
        "violation_paraphrase": "what the violation looks like",
        "severity": "critical",  # not in enum
    }
    with pytest.raises(WriteError):
        db.constraints.insert_one(bad)
    client.close()


def test_constraint_validator_rejects_missing_required(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    incomplete = {
        "_id": "constraint:test:incomplete",
        "skill_id": "skill:test",
        "rule_text": "some rule",
        # missing violation_paraphrase and severity
    }
    with pytest.raises(WriteError):
        db.constraints.insert_one(incomplete)
    client.close()


def test_constraints_cover_multiple_skills(seeded):
    """Seed constraints should cover at least two distinct skills."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    skill_ids = db.constraints.distinct("skill_id")
    client.close()
    assert len(skill_ids) >= 2


def test_constraints_seed_idempotent(seeded, seed_module):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    before = db.constraints.count_documents({})
    seed_module.seed()
    after = db.constraints.count_documents({})
    client.close()
    assert before == after


def test_extract_fact_summary_importable():
    """extract_fact_summary.py must be importable without crashing."""
    import sys
    sys.path.insert(0, "scripts")
    import extract_fact_summary  # noqa: F401


def test_extract_fact_summary_fails_gracefully_without_api_key(tmp_path):
    """Returns error dict (not exception) when OPENROUTER_API_KEY is absent."""
    import sys
    sys.path.insert(0, "scripts")
    import extract_fact_summary

    orig = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        artifact = tmp_path / "artifact.json"
        artifact.write_text('{"components": []}')
        result = extract_fact_summary.extract_fact_summary("skill:test", artifact)
        assert result["ok"] is False
        assert "error" in result
    finally:
        if orig is not None:
            os.environ["OPENROUTER_API_KEY"] = orig
