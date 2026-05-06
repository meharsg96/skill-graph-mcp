"""Field-path-aware blast radius (consumer-driven contracts).

Validates that the algorithm:
  - identifies edges whose fields_used intersects the schema diff
  - returns 0 affected edges when the changed field is in no consumer's
    projection (the headline CDC insight: structurally breaking changes
    can be contractually safe)
  - distinguishes added/removed required fields from type changes
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import blast_radius as br  # noqa: E402


def test_diff_schemas_added_required():
    old = {"required": ["a", "b"], "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}
    new = {"required": ["a", "b", "c"], "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "c": {"type": "string"}}}
    d = br.diff_schemas(old, new)
    assert d["added_required"] == ["c"]
    assert d["removed_required"] == []
    assert "c" in d["all_changed"]


def test_diff_schemas_type_change():
    old = {"required": [], "properties": {"a": {"type": "object"}}}
    new = {"required": [], "properties": {"a": {"type": "string"}}}
    d = br.diff_schemas(old, new)
    assert d["type_changes"] == ["a"]
    assert "a" in d["all_changed"]


def test_diff_schemas_removed_required():
    old = {"required": ["a", "b"], "properties": {"a": {}, "b": {}}}
    new = {"required": ["a"], "properties": {"a": {}, "b": {}}}
    d = br.diff_schemas(old, new)
    assert d["removed_required"] == ["b"]


def test_field_overlaps_handles_array_notation():
    fields_used = ["queries[*].pattern", "workload"]
    changed = ["queries", "indexes"]
    overlap = br.field_overlaps(fields_used, changed)
    assert overlap == ["queries"]


def test_blast_radius_zero_when_field_outside_consumer_projection(seeded, tmp_path):
    """Headline CDC insight: removing a required field is not breaking
    if no consumer projects it. anti_patterns is in schema-review's
    output but not in code-gen's fields_used, so dropping it from
    required is safe.

    `seeded` ensures the canonical edges (with fields_used arrays) are
    in the database for the query side.
    """
    v2 = json.loads((REPO_ROOT / "schema/contracts/schema-recommendation.v2.json").read_text())
    new_schema = dict(v2)
    new_schema["required"] = [r for r in v2["required"] if r != "anti_patterns"]
    new_path = tmp_path / "drop-anti-patterns.json"
    new_path.write_text(json.dumps(new_schema))

    r = br.measure("skill:schema-review", new_path)
    assert "anti_patterns" in r["diff"]["removed_required"]
    assert r["affected_edges"] == [], (
        "anti_patterns is not in code-gen's fields_used; CDC says blast=0"
    )
    assert r["impact_ratio"] == "0/1"


def test_blast_radius_hits_consumer_projecting_changed_field(seeded, tmp_path):
    """Removing `indexes` (in code-gen's fields_used) should fire on
    the schema-review->code-gen edge."""
    v2 = json.loads((REPO_ROOT / "schema/contracts/schema-recommendation.v2.json").read_text())
    new_schema = dict(v2)
    new_schema["required"] = [r for r in v2["required"] if r != "indexes"]
    new_path = tmp_path / "drop-indexes.json"
    new_path.write_text(json.dumps(new_schema))

    r = br.measure("skill:schema-review", new_path)
    assert "indexes" in r["diff"]["removed_required"]
    assert len(r["affected_edges"]) == 1
    edge = r["affected_edges"][0]
    assert edge["edge"] == "edge:schema-review->code-gen"
    assert "indexes" in edge["fields_in_play"]


def test_blast_radius_unknown_skill_errors(seeded, tmp_path):
    fake = tmp_path / "x.json"
    fake.write_text("{}")
    r = br.measure("skill:does-not-exist", fake)
    assert "error" in r
