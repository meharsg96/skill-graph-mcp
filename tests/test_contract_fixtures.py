"""Eval-against-contract harness.

Each active skill in the graph declares `input.schema` and `output.schema`
identifiers (e.g. `schema:schema-recommendation:v2`). This test:

  1. Resolves each identifier to a JSON Schema file under `schema/contracts/`.
  2. Loads input/output fixture pairs from `tests/fixtures/contracts/<skill>/`.
  3. Validates every fixture against the declared schema.
  4. Validates chain edges — for adjacent skills A → B, A's output fixture
     must also satisfy B's input schema (proves the type label isn't lying
     about shape compatibility).
  5. Includes a deliberate-drift negative case to prove the schemas catch
     a v1-style schema_recommendation missing the v2 fields.

Skills without a fixture directory are skipped silently. This lets the
harness grow incrementally — add schemas + fixtures for new skills as
they earn coverage, the test scales without code changes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "schema" / "contracts"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "contracts"


def _schema_id_to_path(schema_id: str) -> Path:
    """`schema:schema-recommendation:v2` -> contracts/schema-recommendation.v2.json"""
    parts = schema_id.split(":")
    if len(parts) != 3 or parts[0] != "schema":
        raise ValueError(f"unrecognized schema id: {schema_id!r}")
    _, name, version = parts
    return CONTRACTS_DIR / f"{name}.{version}.json"


def _load_schema(schema_id: str) -> dict:
    path = _schema_id_to_path(schema_id)
    if not path.is_file():
        pytest.skip(f"no contract file for {schema_id}")
    return json.loads(path.read_text())


def _fixture_dir_for(skill_id: str) -> Path:
    # `skill:schema-review` -> fixtures/contracts/skill_schema-review
    return FIXTURES_DIR / skill_id.replace(":", "_")


def _cases(skill_id: str) -> list[Path]:
    base = _fixture_dir_for(skill_id)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def _active_skills_with_fixtures(db):
    out = []
    for s in db.skills.find({"lifecycle": "active"}):
        if _cases(s["_id"]):
            out.append(s)
    return out


def test_every_fixture_validates_against_declared_contracts(seeded):
    skills = _active_skills_with_fixtures(seeded.db)
    assert skills, "no fixtures present — harness has nothing to check"
    for skill in skills:
        in_schema = _load_schema(skill["input"]["schema"])
        out_schema = _load_schema(skill["output"]["schema"])
        for case in _cases(skill["_id"]):
            inp = json.loads((case / "input.json").read_text())
            outp = json.loads((case / "output.json").read_text())
            Draft7Validator(in_schema).validate(inp)
            Draft7Validator(out_schema).validate(outp)


def test_chain_edges_are_wireable(seeded):
    """For every edge A -> B in the active graph, if both ends have fixtures,
    A's output must validate against B's input schema."""
    db = seeded.db
    by_id = {s["_id"]: s for s in db.skills.find({"lifecycle": "active"})}
    checked = 0
    for b in by_id.values():
        for dep_id in b.get("dependencies", []):
            a = by_id.get(dep_id)
            if not a:
                continue
            a_cases = _cases(a["_id"])
            if not a_cases:
                continue
            try:
                b_in_schema = _load_schema(b["input"]["schema"])
            except Exception:
                continue
            a_out = json.loads((a_cases[0] / "output.json").read_text())
            Draft7Validator(b_in_schema).validate(a_out)
            checked += 1
    assert checked > 0, "no chain edges with fixtures on both ends — coverage gap"


def test_v1_shape_fails_v2_contract(seeded):
    """The original incident: v1 schema_recommendation lacked index strategy
    fields and anti_patterns. Feeding a v1-shaped output to the v2 schema
    must fail — that's the contract earning its keep."""
    schema = _load_schema("schema:schema-recommendation:v2")
    v1_shaped = {
        "collection": "users",
        "shape": {"email": "string"},
    }
    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(v1_shaped)
