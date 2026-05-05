#!/usr/bin/env python3
"""Seed MongoDB with example skill graph data.

Drops and recreates `skills` and `edges` (demo state — ephemeral).
Creates `runs` if missing but never drops it (instrumentation history is
preserved across re-seeds).

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
SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "skills.json"

SKILL_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "input_type", "output_type", "lifecycle", "version", "dependencies"],
        "properties": {
            "_id": {"bsonType": "string"},
            "name": {"bsonType": "string"},
            "input_type": {"bsonType": "string"},
            "output_type": {"bsonType": "string"},
            "lifecycle": {"enum": ["active", "inactive"]},
            "version": {"bsonType": "string"},
            "dependencies": {"bsonType": "array", "items": {"bsonType": "string"}},
        }
    }
}

# Retain runs documents for 90 days. Adjust if you need longer-lived
# instrumentation history; analyze.py consumes whatever is present.
RUNS_TTL_SECONDS = 60 * 60 * 24 * 90


def seed():
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]

    db.skills.drop()
    db.edges.drop()

    try:
        db.create_collection("skills", validator=SKILL_VALIDATOR)
    except CollectionInvalid:
        pass

    db.skills.create_index("dependencies")
    db.skills.create_index("lifecycle")
    db.skills.create_index("input_type")
    db.skills.create_index("output_type")
    db.edges.create_index("from_skill")
    db.edges.create_index("to_skill")

    if "runs" not in db.list_collection_names():
        db.create_collection("runs")
    db.runs.create_index("tool")
    db.runs.create_index([("session_id", 1), ("timestamp", 1)])
    db.runs.create_index("timestamp", expireAfterSeconds=RUNS_TTL_SECONDS)

    data = json.loads(SCHEMA_PATH.read_text())

    if data["skills"]:
        db.skills.insert_many(data["skills"])
        print(f"Inserted {len(data['skills'])} skills")

    if data["edges"]:
        db.edges.insert_many(data["edges"])
        print(f"Inserted {len(data['edges'])} edges")

    runs_count = db.runs.estimated_document_count()
    print(f"runs collection preserved: {runs_count} existing documents")

    for state in ["active", "inactive"]:
        count = db.skills.count_documents({"lifecycle": state})
        if count:
            print(f"  {state}: {count}")

    print("\nDone. Run 'python scripts/validate.py' to test.")


if __name__ == "__main__":
    seed()
