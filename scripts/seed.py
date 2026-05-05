#!/usr/bin/env python3
"""Seed MongoDB with example skill graph data.

Drops and recreates `skills`, `edges`, and `parameters` (demo state — ephemeral).
Creates `runs` if missing but never drops it (instrumentation history is
preserved across re-seeds).

The skill schema is the v2 ABI shape (input/output blocks, semver versions,
dependency_constraints, parameter_sources). For v1 backward compatibility,
top-level `input_type` and `output_type` fields are derived from
`input.type`/`output.type` at insert time so existing v1 tools keep working.

Environment:
    MONGODB_URI    Mongo connection string (default: mongodb://localhost:27017)
"""

import json
import os
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid

DB_NAME = "skill_graph"
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
SCHEMA_DIR = Path(__file__).parent.parent / "schema"
SKILLS_PATH = SCHEMA_DIR / "skills.json"
PARAMETERS_PATH = SCHEMA_DIR / "parameters.json"

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
            "output_type": {"bsonType": "string"}
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
            "tenant":   {"bsonType": "string"}
        }
    }
}

# Retain runs documents for 90 days. Adjust if you need longer-lived
# instrumentation history; analyze.py consumes whatever is present.
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

    try:
        db.create_collection("skills", validator=SKILL_VALIDATOR)
    except CollectionInvalid:
        pass
    try:
        db.create_collection("parameters", validator=PARAMETER_VALIDATOR)
    except CollectionInvalid:
        pass

    db.skills.create_index("dependencies")
    db.skills.create_index("lifecycle")
    db.skills.create_index("input_type")
    db.skills.create_index("output_type")
    db.skills.create_index("output.type")
    db.skills.create_index("input.type")
    # Text index for search_skills (v2). v2.3.0 added `description` to
    # the indexed fields — descriptions became the primary disambiguation
    # surface in v2.2.x (F6 fix), but search_skills couldn't find skills
    # by description until now.
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

    runs_count = db.runs.estimated_document_count()
    print(f"runs collection preserved: {runs_count} existing documents")

    for state in ["active", "inactive"]:
        count = db.skills.count_documents({"lifecycle": state})
        if count:
            print(f"  {state}: {count}")

    print("\nDone. Run 'python scripts/validate.py' to test.")


if __name__ == "__main__":
    seed()
