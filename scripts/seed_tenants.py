#!/usr/bin/env python3
"""Seed only the parameters collection — useful when iterating on tenant configs.

Idempotent: upserts each parameter document by `_id`. The skills/edges
collections are untouched.

Usage:
    python scripts/seed_tenants.py
    python scripts/seed_tenants.py --tenant client-a
    python scripts/seed_tenants.py --file schema/parameters.json

Environment:
    MONGODB_URI    Mongo connection string (default: mongodb://localhost:27017)
"""

import argparse
import json
import os
from pathlib import Path

from pymongo import MongoClient

DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DEFAULT_PATH = Path(__file__).parent.parent / "schema" / "parameters.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant", help="Only seed parameter docs for this tenant")
    ap.add_argument("--file", default=str(DEFAULT_PATH), help="Source JSON (default: schema/parameters.json)")
    args = ap.parse_args()

    data = json.loads(Path(args.file).read_text())
    docs = data.get("parameters", [])
    if args.tenant:
        docs = [d for d in docs if d.get("tenant") == args.tenant]

    if not docs:
        print("no parameter docs to seed (filter matched nothing?)")
        return

    db = MongoClient(MONGODB_URI)[DB_NAME]
    upserted = 0
    for d in docs:
        result = db.parameters.replace_one({"_id": d["_id"]}, d, upsert=True)
        upserted += 1 if (result.upserted_id or result.modified_count) else 0
    print(f"upserted {upserted} of {len(docs)} parameter docs")


if __name__ == "__main__":
    main()
