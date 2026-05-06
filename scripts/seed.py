#!/usr/bin/env python3
"""Seed MongoDB with example skill graph data.

Drops and recreates `skills`, `edges`, `parameters`, and `preferences`
(demo state — ephemeral). Creates `runs` if missing but never drops it
(instrumentation history is preserved across re-seeds).

The skill schema is the v2 ABI shape (input/output blocks, semver
versions, dependency_constraints, parameter_sources). For v1 backward
compatibility, top-level `input_type` and `output_type` fields are
derived from `input.type`/`output.type` at insert time so existing
v1 tools keep working.

Local-only overlay (v2.5.0): if SKILL_GRAPH_LOCAL_DIR is set and the
directory exists, additional skills/parameters/preferences in that
directory are loaded *after* the canonical seed via idempotent upsert.
Local docs MUST use the `skill:local:<slug>` namespace; canonical
namespaces in the local overlay are rejected. The local directory is
expected to live OUTSIDE the repository (default ~/.skill-graph-local/);
this is the source of the leak-resistance — local content cannot enter
git from a path outside the working tree.

Environment:
    MONGODB_URI            Mongo connection string (default: mongodb://localhost:27017)
    SKILL_GRAPH_LOCAL_DIR  Optional directory of local-only overlay docs
                           (default: ~/.skill-graph-local/)
"""

import json
import os
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, WriteError

DB_NAME = "skill_graph"
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
SCHEMA_DIR = Path(__file__).parent.parent / "schema"
SKILLS_PATH = SCHEMA_DIR / "skills.json"
PARAMETERS_PATH = SCHEMA_DIR / "parameters.json"
PREFERENCES_PATH = SCHEMA_DIR / "preferences.json"

LOCAL_DIR = Path(
    os.environ.get("SKILL_GRAPH_LOCAL_DIR", str(Path.home() / ".skill-graph-local"))
).expanduser()
LOCAL_NAMESPACE_PREFIX = "skill:local:"

SKILL_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "input", "output", "lifecycle", "version", "dependencies"],
        "properties": {
            "_id": {"bsonType": "string"},
            "name": {"bsonType": "string"},
            "version": {"bsonType": "string"},
            "lifecycle": {"enum": ["active", "inactive"]},
            "input": {
                "bsonType": "object",
                "required": ["type"],
                "properties": {
                    "type":   {"bsonType": "string"},
                    "schema": {"bsonType": "string"}
                }
            },
            "output": {
                "bsonType": "object",
                "required": ["type"],
                "properties": {
                    "type":   {"bsonType": "string"},
                    "schema": {"bsonType": "string"}
                }
            },
            "dependencies": {"bsonType": "array", "items": {"bsonType": "string"}},
            "input_type":  {"bsonType": "string"},
            "output_type": {"bsonType": "string"},
        }
    }
}

PARAMETER_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "skill_id", "tenant"],
        "properties": {
            "_id":      {"bsonType": "string"},
            "skill_id": {"bsonType": "string"},
            "tenant":   {"bsonType": "string"},
        }
    }
}

PREFERENCE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "owner", "scope", "category", "name", "version"],
        "properties": {
            "_id":      {"bsonType": "string"},
            "owner":    {"bsonType": "string"},
            "scope":    {"enum": ["skill", "category", "global"]},
            "applies_to_skill_id": {"bsonType": "string"},
            "category": {"bsonType": "string"},
            "name":     {"bsonType": "string"},
            "version":  {"bsonType": "string"},
            "policy":   {"bsonType": "object"},
        }
    }
}

RUNS_TTL_SECONDS = 60 * 60 * 24 * 90


def _derive_compat_fields(skill: dict) -> dict:
    """Add v1 top-level input_type/output_type fields from the v2 input/output blocks."""
    skill = dict(skill)
    if "input" in skill and "type" in skill["input"]:
        skill["input_type"] = skill["input"]["type"]
    if "output" in skill and "type" in skill["output"]:
        skill["output_type"] = skill["output"]["type"]
    return skill


def seed():
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]

    db.skills.drop()
    db.edges.drop()
    db.parameters.drop()
    db.preferences.drop()

    for name, validator in [
        ("skills", SKILL_VALIDATOR),
        ("parameters", PARAMETER_VALIDATOR),
        ("preferences", PREFERENCE_VALIDATOR),
    ]:
        try:
            db.create_collection(name, validator=validator)
        except CollectionInvalid:
            pass

    db.skills.create_index("dependencies")
    db.skills.create_index("lifecycle")
    db.skills.create_index("input_type")
    db.skills.create_index("output_type")
    db.skills.create_index("output.type")
    db.skills.create_index("input.type")
    db.skills.create_index([
        ("name", "text"),
        ("description", "text"),
        ("domain_fields", "text"),
    ])

    db.edges.create_index("from_skill")
    db.edges.create_index("to_skill")
    db.edges.create_index("compatible")

    db.parameters.create_index([("skill_id", 1), ("tenant", 1)], unique=True)
    db.parameters.create_index("tenant")

    # preferences indexes — designed forward-compatible with Queryable
    # Encryption: equality filters only (no text or unique compound on
    # the policy body), per-owner partition for future per-owner DEKs.
    db.preferences.create_index("owner")
    db.preferences.create_index([("scope", 1), ("applies_to_skill_id", 1)])
    db.preferences.create_index("category")

    if "runs" not in db.list_collection_names():
        db.create_collection("runs")
    db.runs.create_index("tool")
    db.runs.create_index([("session_id", 1), ("timestamp", 1)])
    db.runs.create_index("timestamp", expireAfterSeconds=RUNS_TTL_SECONDS)

    skills_data = json.loads(SKILLS_PATH.read_text())

    if skills_data["skills"]:
        compat_docs = [_derive_compat_fields(s) for s in skills_data["skills"]]
        db.skills.insert_many(compat_docs)
        print(f"Inserted {len(compat_docs)} skills")

    if skills_data["edges"]:
        db.edges.insert_many(skills_data["edges"])
        print(f"Inserted {len(skills_data['edges'])} edges")

    if PARAMETERS_PATH.exists():
        params_data = json.loads(PARAMETERS_PATH.read_text())
        if params_data.get("parameters"):
            db.parameters.insert_many(params_data["parameters"])
            print(f"Inserted {len(params_data['parameters'])} parameter docs")

    if PREFERENCES_PATH.exists():
        prefs_data = json.loads(PREFERENCES_PATH.read_text())
        if prefs_data.get("preferences"):
            db.preferences.insert_many(prefs_data["preferences"])
            print(f"Inserted {len(prefs_data['preferences'])} preference docs")

    runs_count = db.runs.estimated_document_count()
    print(f"runs collection preserved: {runs_count} existing documents")

    if LOCAL_DIR.is_dir():
        _load_local_overlay(db)

    for state in ["active", "inactive"]:
        count = db.skills.count_documents({"lifecycle": state})
        if count:
            print(f"  {state}: {count}")

    print("\nDone. Run 'python scripts/validate.py' to test.")


def _load_local_overlay(db) -> None:
    """Load private skills/parameters/preferences from LOCAL_DIR.

    Idempotent: each doc is upserted by `_id`, so re-seeding updates
    existing local entries rather than duplicating. Schema validation
    failures on individual local docs are logged but do not crash the
    seed — canonical content stays loaded even if a local doc is bad.

    Hard guards:
      - skills.json entries must use `skill:local:` namespace
      - parameters.json entries' skill_id must reference a local skill
      - preferences.json entries' applies_to_skill_id (if set) must
        reference a local skill

    These prevent local content from masquerading as canonical data.
    """
    print(f"\nlocal overlay: loading from {LOCAL_DIR}")
    skill_count = _overlay_load(
        db.skills, LOCAL_DIR / "skills.json", "skills",
        guard=lambda d: d["_id"].startswith(LOCAL_NAMESPACE_PREFIX),
        guard_msg=f"_id must start with '{LOCAL_NAMESPACE_PREFIX}'",
        compat=_derive_compat_fields,
    )
    if skill_count:
        print(f"  skills upserted:      {skill_count}")

    param_count = _overlay_load(
        db.parameters, LOCAL_DIR / "parameters.json", "parameters",
        guard=lambda d: d["skill_id"].startswith(LOCAL_NAMESPACE_PREFIX),
        guard_msg=f"skill_id must start with '{LOCAL_NAMESPACE_PREFIX}'",
    )
    if param_count:
        print(f"  parameters upserted:  {param_count}")

    pref_count = _overlay_load(
        db.preferences, LOCAL_DIR / "preferences.json", "preferences",
        guard=lambda d: (
            d.get("scope") != "skill"
            or d.get("applies_to_skill_id", "").startswith(LOCAL_NAMESPACE_PREFIX)
        ),
        guard_msg=f"applies_to_skill_id must start with '{LOCAL_NAMESPACE_PREFIX}' when scope=skill",
    )
    if pref_count:
        print(f"  preferences upserted: {pref_count}")


def _overlay_load(collection, path: Path, key: str, guard, guard_msg: str,
                  compat=None) -> int:
    """Read a local-overlay JSON file and upsert each doc under `key`.

    `guard(doc)` must return True; otherwise the doc is skipped with a
    clear message. `compat`, if provided, runs over each doc before
    insert (used for v1 input_type/output_type derivation on skills)."""
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"  WARNING: {path.name} is not valid JSON ({e}); skipping overlay")
        return 0
    docs = data.get(key) or []
    upserted = 0
    for doc in docs:
        if not guard(doc):
            print(f"  REJECTED {key}/{doc.get('_id', '<no _id>')}: {guard_msg}")
            continue
        if compat is not None:
            doc = compat(doc)
        try:
            collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            upserted += 1
        except WriteError as e:
            print(f"  REJECTED {key}/{doc.get('_id', '<no _id>')}: {e.details.get('errmsg', e)}")
    return upserted


if __name__ == "__main__":
    seed()
