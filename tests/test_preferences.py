"""preferences collection — per-deployment usage policies (v2.4.0).

Adds a per-concern collection separate from `parameters`. Parameters
carry data overrides (tokens, components) that change per tenant;
preferences carry policy/style/conventions that change per owner.
Different lifecycles, different access patterns, different indexes.
"""

import os

import pytest
from pymongo import MongoClient
from pymongo.errors import WriteError


def test_lg_flavour_preference_seeded(seeded, call):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client["skill_graph"]
    p = db.preferences.find_one({"_id": "pref:owner:lg-flavour-not-cage"})
    client.close()
    assert p is not None
    assert p["scope"] == "skill"
    assert p["applies_to_skill_id"] == "skill:leafygreen-ui"
    assert p["category"] == "house_style"
    assert "summary" in p["policy"]


def test_preferences_indexes_present(seeded, call):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client["skill_graph"]
    indexes = {ix["name"] for ix in db.preferences.list_indexes()}
    client.close()
    assert "owner_1" in indexes
    assert "scope_1_applies_to_skill_id_1" in indexes
    assert "category_1" in indexes


def test_preferences_seed_idempotent(seeded, seed_module):
    """Re-seeding drops + recreates preferences; the LG-flavour doc
    is always present after seed completes."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client["skill_graph"]
    before = db.preferences.count_documents({})
    seed_module.seed()
    after = db.preferences.count_documents({})
    client.close()
    assert before == after >= 1


def test_preference_validator_requires_scope_enum(seeded):
    """scope is enum [skill, category, global] — anything else rejected."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client["skill_graph"]
    bad = {
        "_id": "pref:test:invalid-scope",
        "owner": "owner",
        "scope": "everywhere",
        "category": "test",
        "name": "test",
        "version": "0.0.1",
    }
    with pytest.raises(WriteError):
        db.preferences.insert_one(bad)
    client.close()


def test_preference_validator_requires_required_fields(seeded):
    """owner, scope, category, name, version are required — missing any
    of them is a schema violation, not application logic."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client["skill_graph"]
    incomplete = {
        "_id": "pref:test:incomplete",
        "owner": "owner",
        # missing scope, category, name, version
    }
    with pytest.raises(WriteError):
        db.preferences.insert_one(incomplete)
    client.close()
