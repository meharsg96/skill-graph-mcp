#!/usr/bin/env python3
"""Create Atlas Vector Search indexes for Layer 2 semantic validation.

Requires an Atlas cluster — local MongoDB does not support $vectorSearch.
Run once after seed.py + seed_constraint_embeddings.py. The index takes
1-3 minutes to reach READY state after creation.

Usage:
    python scripts/create_atlas_indexes.py
    python scripts/create_atlas_indexes.py --poll   # wait for READY
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
INDEX_NAME = "constraint_embedding_index"

CONSTRAINT_VECTOR_INDEX = SearchIndexModel(
    definition={
        "fields": [
            {
                "type": "vector",
                "path": "constraint_embedding",
                "numDimensions": 1024,
                "similarity": "cosine",
            },
            {
                "type": "filter",
                "path": "skill_id",
            },
        ]
    },
    name=INDEX_NAME,
    type="vectorSearch",
)


def _index_status(col) -> str | None:
    """Return current status of the constraint embedding index, or None if absent."""
    try:
        for ix in col.list_search_indexes(INDEX_NAME):
            return ix.get("status")
    except Exception:
        pass
    return None


def _poll_ready(col, timeout_s: int = 300) -> bool:
    print("Polling for READY status...", end="", flush=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = _index_status(col)
        if status == "READY":
            print(" READY.")
            return True
        print(".", end="", flush=True)
        time.sleep(10)
    print(f"\nTimeout after {timeout_s}s — check Atlas UI for index status.")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--poll", action="store_true", help="Wait until the index is READY")
    args = p.parse_args()

    client = MongoClient(MONGODB_URI)
    col = client[DB_NAME].constraints

    status = _index_status(col)
    if status is not None:
        print(f"Index '{INDEX_NAME}' already exists (status: {status}).")
        if args.poll and status != "READY":
            _poll_ready(col)
        client.close()
        return 0

    print(f"Creating '{INDEX_NAME}' on {DB_NAME}.constraints …")
    try:
        col.create_search_index(CONSTRAINT_VECTOR_INDEX)
    except Exception as e:
        msg = str(e)
        if any(kw in msg for kw in ("Atlas", "not supported", "PlanExecutor")):
            print(
                "ERROR: $vectorSearch requires Atlas. "
                "Update MONGODB_URI in .env to an Atlas connection string.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        client.close()
        return 1

    print("Submitted. Status: PENDING → BUILDING → READY (1-3 min).")
    if args.poll:
        _poll_ready(col)

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
