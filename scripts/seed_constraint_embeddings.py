#!/usr/bin/env python3
"""Populate constraint_embedding fields using Voyage AI.

Reads all constraints where constraint_embedding is null, embeds each
violation_paraphrase via voyage-3 (text model), and writes the vector
back to the same document.

Usage:
    python scripts/seed_constraint_embeddings.py [--dry-run]

Requires:
    VOYAGE_API_KEY env var (set in .env)
    pip install voyageai

Run after scripts/seed.py. Safe to re-run — skips docs that already
have a non-null embedding.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")

# voyage-3 for natural-language violation paraphrases.
# Use voyage-code-3 only when paraphrases contain significant code/CSS.
EMBEDDING_MODEL = "voyage-3"


def embed_texts(texts: list[str], client) -> list[list[float]]:
    result = client.embed(texts, model=EMBEDDING_MODEL, input_type="document")
    return result.embeddings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dry-run", action="store_true", help="Print what would be updated, don't write")
    args = p.parse_args()

    if not VOYAGE_API_KEY:
        print("ERROR: VOYAGE_API_KEY not set. Add it to .env.", file=sys.stderr)
        return 1

    try:
        import voyageai
    except ImportError:
        print("ERROR: voyageai not installed. Run: pip install voyageai", file=sys.stderr)
        return 1

    mongo = MongoClient(MONGODB_URI)
    col = mongo[DB_NAME].constraints

    pending = list(col.find({"constraint_embedding": None}, {"_id": 1, "violation_paraphrase": 1}))
    if not pending:
        print("All constraints already have embeddings. Nothing to do.")
        mongo.close()
        return 0

    print(f"Embedding {len(pending)} constraint(s) with {EMBEDDING_MODEL}...")

    if args.dry_run:
        for doc in pending:
            print(f"  would embed: {doc['_id']!r}  →  {doc['violation_paraphrase']!r}")
        mongo.close()
        return 0

    voyage = voyageai.Client(api_key=VOYAGE_API_KEY)
    texts = [doc["violation_paraphrase"] for doc in pending]

    try:
        vectors = embed_texts(texts, voyage)
    except Exception as e:
        print(f"ERROR: Voyage AI call failed: {e}", file=sys.stderr)
        mongo.close()
        return 1

    updated = 0
    for doc, vector in zip(pending, vectors):
        col.update_one({"_id": doc["_id"]}, {"$set": {"constraint_embedding": vector}})
        print(f"  {doc['_id']}: {len(vector)}-dim vector written")
        updated += 1

    mongo.close()
    print(f"\nDone. {updated} constraint(s) updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
