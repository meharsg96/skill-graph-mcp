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
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    p = db.preferences.find_one({"_id": "pref:cyborg:lg-flavour-not-cage"})
    client.close()
    assert p is not None
    assert p["scope"] == "skill"
    assert p["applies_to_skill_id"] == "skill:leafygreen-ui"
    assert p["category"] == "house_style"
    assert "summary" in p["policy"]


def test_preferences_indexes_present(seeded, call):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    indexes = {ix["name"] for ix in db.preferences.list_indexes()}
    client.close()
    assert "owner_1" in indexes
    assert "scope_1_applies_to_skill_id_1" in indexes
    assert "category_1" in indexes


def test_preferences_seed_idempotent(seeded, seed_module):
    """Re-seeding drops + recreates preferences; the LG-flavour doc
    is always present after seed completes."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    before = db.preferences.count_documents({})
    seed_module.seed()
    after = db.preferences.count_documents({})
    client.close()
    assert before == after >= 1


def test_preference_validator_requires_scope_enum(seeded):
    """scope is enum [skill, category, global] — anything else rejected."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
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
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    incomplete = {
        "_id": "pref:test:incomplete",
        "owner": "owner",
        # missing scope, category, name, version
    }
    with pytest.raises(WriteError):
        db.preferences.insert_one(incomplete)
    client.close()


# ---------- get_preferences MCP tool (v2.6) ----------

def _call(tool, **kwargs):
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


def test_get_preferences_returns_lg_flavour_for_leafygreen(seeded):
    r = _call(seeded.get_preferences, skill_id="skill:leafygreen-ui")
    assert r["count"] == 1
    assert r["source"] == "preferences"
    assert r["preferences"][0]["_id"] == "pref:cyborg:lg-flavour-not-cage"


def test_get_preferences_unrelated_skill_returns_zero(seeded):
    r = _call(seeded.get_preferences, skill_id="skill:schema-review")
    assert r["count"] == 0
    assert r["preferences"] == []


def test_get_preferences_unknown_skill_errors(seeded):
    r = _call(seeded.get_preferences, skill_id="skill:does-not-exist")
    assert "error" in r


def test_get_preferences_owner_filter(seeded):
    r = _call(seeded.get_preferences, skill_id="skill:leafygreen-ui", owner="cyborg")
    assert r["count"] == 1
    r2 = _call(seeded.get_preferences, skill_id="skill:leafygreen-ui", owner="someone-else")
    assert r2["count"] == 0


def test_get_preferences_category_filter(seeded):
    r = _call(seeded.get_preferences, skill_id="skill:leafygreen-ui", category="house_style")
    assert r["count"] == 1
    r2 = _call(seeded.get_preferences, skill_id="skill:leafygreen-ui", category="security")
    assert r2["count"] == 0


def test_get_preferences_global_scope_applies_everywhere(seeded):
    """A scope: global preference should attach to any active skill."""
    db = seeded.db
    db.preferences.insert_one({
        "_id": "pref:test:global-temp",
        "owner": "owner",
        "scope": "global",
        "category": "test",
        "name": "global temp",
        "version": "0.0.1",
        "policy": {"summary": "applies to everything"},
    })
    try:
        r = _call(seeded.get_preferences, skill_id="skill:schema-review")
        assert r["count"] == 1
        assert r["preferences"][0]["_id"] == "pref:test:global-temp"
        r2 = _call(seeded.get_preferences, skill_id="skill:leafygreen-ui")
        # both global-temp AND lg-flavour-not-cage should match
        ids = {p["_id"] for p in r2["preferences"]}
        assert ids == {"pref:test:global-temp", "pref:cyborg:lg-flavour-not-cage"}
    finally:
        db.preferences.delete_one({"_id": "pref:test:global-temp"})


def test_get_preferences_logged_in_runs(seeded):
    db = seeded.db
    before = db.runs.count_documents({"tool": "get_preferences"})
    _call(seeded.get_preferences, skill_id="skill:leafygreen-ui")
    after = db.runs.count_documents({"tool": "get_preferences"})
    assert after == before + 1


# ---------- list_preferences (v2.7) — catalogue-style enumeration ----------

def test_list_preferences_no_filters_returns_all(seeded):
    r = _call(seeded.list_preferences)
    assert r["count"] >= 1
    ids = {p["_id"] for p in r["preferences"]}
    assert "pref:cyborg:lg-flavour-not-cage" in ids
    assert r["source"] == "preferences"


def test_list_preferences_owner_filter(seeded):
    r = _call(seeded.list_preferences, owner="cyborg")
    assert r["count"] == 1
    r2 = _call(seeded.list_preferences, owner="someone-else")
    assert r2["count"] == 0


def test_list_preferences_category_filter(seeded):
    r = _call(seeded.list_preferences, category="house_style")
    assert r["count"] == 1
    r2 = _call(seeded.list_preferences, category="security")
    assert r2["count"] == 0


def test_list_preferences_scope_filter(seeded):
    r = _call(seeded.list_preferences, scope="skill")
    assert r["count"] == 1
    r2 = _call(seeded.list_preferences, scope="global")
    assert r2["count"] == 0


def test_list_preferences_invalid_scope_errors(seeded):
    r = _call(seeded.list_preferences, scope="everywhere")
    assert "error" in r


def test_list_preferences_logged_in_runs(seeded):
    db = seeded.db
    before = db.runs.count_documents({"tool": "list_preferences"})
    _call(seeded.list_preferences)
    after = db.runs.count_documents({"tool": "list_preferences"})
    assert after == before + 1


def test_list_preferences_classified_as_routing(seeded):
    """list_preferences is a discovery/navigation tool — must be in
    ROUTING_TOOLS so the routing_ratio metric counts it correctly.
    Closes R5 F1: graph-wide preference questions should not collapse
    the routing ratio toward zero just because of API shape."""
    import sys
    sys.path.insert(0, "scripts")
    import analyze
    assert "list_preferences" in analyze.ROUTING_TOOLS
