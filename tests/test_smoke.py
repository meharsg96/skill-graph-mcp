"""Phase 1 smoke + bug-fix verification suite.

Covers:
  - get_tokens theme filter narrows result (D2 fix)
  - get_components category filter narrows result (D3 fix)
  - get_layouts breakpoint omits layout_grids (D4 fix)
  - validate_chain happy path + 3 failure modes
  - traverse_dependencies prunes inactive skills mid-traversal (the load-bearing invariant)
  - log_tool_call writes a runs document
  - re-running seed preserves the runs collection
"""

import os

from pymongo import MongoClient


def _call(tool, **kwargs):
    """FastMCP may wrap the function in a Tool object exposing .fn."""
    fn = getattr(tool, "fn", tool)
    return fn(**kwargs)


# ----- D2: get_tokens theme filter -----

def test_get_tokens_returns_only_requested_theme(seeded):
    result = _call(seeded.get_tokens, skill_id="skill:ui-builder", theme="dark")
    assert "error" not in result
    assert result["theme"] == "dark"
    # Theme block contains color tokens, not the other theme
    assert result["tokens"]["primary"] == "#60A5FA"
    assert "themes" not in result  # other themes excluded
    # Theme-agnostic fields preserved
    assert result["spacing_unit"] == 4
    assert result["type_scale"] == [12, 14, 16, 20, 24, 32]


def test_get_tokens_unknown_theme_reports_available(seeded):
    result = _call(seeded.get_tokens, skill_id="skill:ui-builder", theme="solarized")
    assert "error" in result
    assert "available_themes" in result
    assert set(result["available_themes"]) == {"light", "dark"}


def test_get_tokens_no_theme_returns_full_block(seeded):
    result = _call(seeded.get_tokens, skill_id="skill:ui-builder")
    assert "error" not in result
    assert "themes" in result["tokens"]
    assert set(result["tokens"]["themes"].keys()) == {"light", "dark"}


# ----- D3: get_components category filter -----

def test_get_components_returns_only_requested_category(seeded):
    result = _call(seeded.get_components, skill_id="skill:ui-builder", category="buttons")
    assert "error" not in result
    assert result["category"] == "buttons"
    variants = {v["variant"] for v in result["variants"]}
    assert {"primary", "secondary", "ghost", "danger"}.issubset(variants)
    assert "inputs" not in result  # other categories excluded


def test_get_components_no_category_lists_categories_only(seeded):
    result = _call(seeded.get_components, skill_id="skill:ui-builder")
    assert "categories" in result
    assert set(result["categories"]) == {"buttons", "inputs", "cards", "forms"}
    assert "variants" not in result


def test_get_components_unknown_category_reports_available(seeded):
    result = _call(seeded.get_components, skill_id="skill:ui-builder", category="modals")
    assert "error" in result
    assert "available_categories" in result


# ----- D4: get_layouts breakpoint shape -----

def test_get_layouts_breakpoint_omits_layout_grids(seeded):
    result = _call(seeded.get_layouts, skill_id="skill:ui-builder", breakpoint="md")
    assert "error" not in result
    assert result["breakpoint"] == "md"
    assert result["value"] == 768
    assert "breakpoints" not in result
    assert "layout_grids" not in result


def test_get_layouts_no_breakpoint_returns_all(seeded):
    result = _call(seeded.get_layouts, skill_id="skill:ui-builder")
    assert "breakpoints" in result
    assert result["breakpoints"]["lg"] == 1024


# ----- validate_chain four cases -----

def test_validate_chain_valid_full_pipeline(seeded):
    result = _call(seeded.validate_chain, skill_ids=[
        "skill:query-analysis", "skill:schema-review",
        "skill:code-gen", "skill:ui-builder",
    ])
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_chain_type_mismatch(seeded):
    result = _call(seeded.validate_chain, skill_ids=[
        "skill:query-analysis", "skill:code-gen",
    ])
    assert result["valid"] is False
    types = {e["type"] for e in result["errors"]}
    assert "type_mismatch" in types


def test_validate_chain_inactive_skill_flagged(seeded):
    result = _call(seeded.validate_chain, skill_ids=[
        "skill:schema-review-v1", "skill:code-gen",
    ])
    assert result["valid"] is False
    types = {e["type"] for e in result["errors"]}
    assert "not_active" in types


def test_validate_chain_missing_dependency(seeded):
    # schema-review depends on query-analysis; chain that skips it should flag missing_dependency
    result = _call(seeded.validate_chain, skill_ids=["skill:schema-review"])
    assert result["valid"] is False
    types = {e["type"] for e in result["errors"]}
    assert "missing_dependency" in types


def test_validate_chain_version_constraint_violation(seeded):
    # schema-review declares dependency_constraints on query-analysis: ">=1.0.0 <2.0.0".
    # Bump query-analysis to 2.0.0 to push it out of range, then restore.
    db = seeded.db
    original = db.skills.find_one({"_id": "skill:query-analysis"}, {"version": 1})["version"]
    db.skills.update_one({"_id": "skill:query-analysis"}, {"$set": {"version": "2.0.0"}})
    try:
        result = _call(seeded.validate_chain, skill_ids=[
            "skill:query-analysis", "skill:schema-review",
        ])
        types = {e["type"] for e in result["errors"]}
        assert "version_constraint_violation" in types
        assert result["valid"] is False
    finally:
        db.skills.update_one({"_id": "skill:query-analysis"}, {"$set": {"version": original}})


# ----- traverse_dependencies invariant: inactive skills never appear -----

def test_traverse_excludes_inactive_skills(seeded):
    # schema-review-v1 is inactive in the seed.
    # traversal from query-analysis should never surface it,
    # even though it's in the same graph.
    result = _call(seeded.traverse_dependencies, skill_id="skill:query-analysis")
    assert "error" not in result
    chain_ids_via_names = [s["name"] for s in result["chain"]]
    assert "Schema Design Review (v1)" not in chain_ids_via_names


# ----- 1B: log_tool_call writes runs -----

def test_log_tool_call_writes_runs_document(seeded):
    _call(seeded.get_skill_contract, skill_id="skill:query-analysis")
    client = MongoClient(os.environ["MONGODB_URI"])
    runs = list(client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]["runs"].find({"tool": "get_skill_contract"}))
    client.close()
    assert len(runs) == 1
    doc = runs[0]
    assert doc["params"] == {"skill_id": "skill:query-analysis"}
    assert doc["tokens_returned"] > 0
    assert doc["duration_ms"] >= 0
    # SESSION_ID is captured at module load. Conftest doesn't set one, so we
    # expect either the auto-generated fallback (preferred) or whatever the
    # surrounding env supplied.
    assert doc["session_id"].startswith("session:") or doc["session_id"] == os.environ.get("SESSION_ID")
    assert doc["error"] is None


def test_session_id_auto_fallback_when_unset(seeded):
    """If SESSION_ID was unset at server import, the captured id should be
    the auto-generated session:auto-<pid>-<epoch> form so calls still group."""
    captured = seeded.SESSION_ID
    if os.environ.get("SESSION_ID"):
        # Surrounding env supplied one — the explicit value should win.
        assert captured == os.environ["SESSION_ID"]
    else:
        assert captured.startswith("session:auto-")
        # Two segments after the prefix: pid and epoch
        suffix = captured[len("session:auto-"):]
        pid, epoch = suffix.split("-")
        assert pid.isdigit() and epoch.isdigit()


def test_seed_preserves_runs_collection(seeded, seed_module):
    # Generate two run records
    _call(seeded.get_skill_contract, skill_id="skill:query-analysis")
    _call(seeded.get_skill_contract, skill_id="skill:schema-review")
    client = MongoClient(os.environ["MONGODB_URI"])
    before = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]["runs"].count_documents({})
    assert before == 2
    # Re-seed: skills/edges drop, runs survive
    seed_module.seed()
    after = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]["runs"].count_documents({})
    client.close()
    assert after == before


def test_log_tool_call_records_error_and_reraises(seeded, monkeypatch):
    """When a tool raises, @log_tool_call must:
       1) re-raise the exception (instrumentation must not swallow errors)
       2) write a runs doc tagged with error = "<ExceptionClassName>"
    """
    import pytest

    def boom(*a, **kw):
        raise RuntimeError("simulated mongo failure")

    # _active_skill is the helper get_skill_contract actually calls.
    # Patching db.skills.find_one is unreliable because pymongo's
    # Database.__getattr__ creates a fresh Collection each access.
    monkeypatch.setattr(seeded, "_active_skill", boom)

    with pytest.raises(RuntimeError, match="simulated mongo failure"):
        _call(seeded.get_skill_contract, skill_id="skill:query-analysis")

    client = MongoClient(os.environ["MONGODB_URI"])
    runs = list(client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]["runs"].find({"tool": "get_skill_contract"}))
    client.close()
    assert len(runs) == 1
    assert runs[0]["error"] == "RuntimeError"
    assert runs[0]["params"] == {"skill_id": "skill:query-analysis"}
