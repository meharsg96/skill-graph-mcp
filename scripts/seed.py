#!/usr/bin/env python3
"""Seed MongoDB with example skill graph data."""

import json
from pathlib import Path
from pymongo import MongoClient
from pymongo.errors import CollectionInvalid

DB_NAME = "skill_graph"
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

def seed():
    client = MongoClient("mongodb://localhost:27017")
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

    data = json.loads(SCHEMA_PATH.read_text())

    if data["skills"]:
        db.skills.insert_many(data["skills"])
        print(f"Inserted {len(data['skills'])} skills")

    if data["edges"]:
        db.edges.insert_many(data["edges"])
        print(f"Inserted {len(data['edges'])} edges")

    for state in ["active", "inactive"]:
        count = db.skills.count_documents({"lifecycle": state})
        if count:
            print(f"  {state}: {count}")

    print(f"\nDone. Run 'python scripts/validate.py' to test.")

if __name__ == "__main__":
    seed()
