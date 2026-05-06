#!/usr/bin/env python3
"""Layer 2 semantic validation pipeline.

Full path: artifact → fact summary (LLM) → embed (Voyage AI) →
$vectorSearch (Atlas) → two-threshold verdict per matched constraint.

Usage:
    python scripts/query_layer2.py skill:leafygreen-ui artifact.json
    python scripts/query_layer2.py skill:leafygreen-ui artifact.json --json

Requires:
    ANTHROPIC_API_KEY  fact extraction (claude-haiku)
    VOYAGE_API_KEY     fact summary embedding (voyage-3)
    MONGODB_URI        Atlas, or MongoDB 8.2+ with mongot (Linux only as of 8.2)

Thresholds (cosine similarity — calibrate against labeled pairs):
    score >= HIGH_THRESHOLD → escalate (likely violation)
    score >= LOW_THRESHOLD  → flag    (possible violation)
    score <  LOW_THRESHOLD  → clean
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

from pymongo import MongoClient  # noqa: E402

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")

# Calibrated against 3 labeled leafygreen-ui artifacts (voyage-code-3):
#   clear violation (#00ED64 on #FFF):  0.726
#   subtle violation (#00A35C on #F9F): 0.725
#   compliant (#FFF on #016BF8):        0.681
# Gap ~0.044. Expand calibration set to 10-20 artifacts before production.
LOW_THRESHOLD = 0.70
HIGH_THRESHOLD = 0.725

EMBEDDING_MODEL = "voyage-code-3"
INDEX_NAME = "constraint_embedding_index"
MAX_RESULTS = 20


def _embed(text: str) -> list[float]:
    import voyageai
    vc = voyageai.Client(api_key=VOYAGE_API_KEY)
    result = vc.embed([text], model=EMBEDDING_MODEL, input_type="query")
    return result.embeddings[0]


def _verdict(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "escalate"
    if score >= LOW_THRESHOLD:
        return "flag"
    return "clean"


def run_layer2(skill_id: str, artifact_path: Path) -> dict:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import extract_fact_summary as efs

    fact_result = efs.extract_fact_summary(skill_id, artifact_path)
    if not fact_result["ok"]:
        return {"ok": False, "phase": "fact_extraction", "error": fact_result["error"]}

    fact_summary = fact_result["fact_summary"]

    if not VOYAGE_API_KEY:
        return {"ok": False, "phase": "embedding", "error": "VOYAGE_API_KEY not set"}
    try:
        query_vector = _embed(fact_summary)
    except Exception as e:
        return {"ok": False, "phase": "embedding", "error": str(e)}

    client = MongoClient(MONGODB_URI)
    col = client[DB_NAME].constraints
    pipeline = [
        {
            "$vectorSearch": {
                "index": INDEX_NAME,
                "path": "constraint_embedding",
                "queryVector": query_vector,
                "exact": True,
                "limit": MAX_RESULTS,
                "filter": {"skill_id": {"$eq": skill_id}},
            }
        },
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        {
            "$project": {
                "_id": 1,
                "rule_text": 1,
                "severity": 1,
                "category": 1,
                "score": 1,
            }
        },
    ]
    try:
        results = list(col.aggregate(pipeline))
    except Exception as e:
        client.close()
        msg = str(e)
        if "$vectorSearch" in msg or "not supported" in msg.lower():
            return {
                "ok": False,
                "phase": "vector_search",
                "error": (
                    "$vectorSearch requires MongoDB 8.2+ with mongot, or Atlas. "
                    "Update MONGODB_URI in .env to an Atlas connection string, "
                    "or start mongot alongside mongod (Linux only as of 8.2)."
                ),
            }
        return {"ok": False, "phase": "vector_search", "error": msg}
    client.close()

    checks = [
        {
            "constraint_id": r["_id"],
            "score": round(r["score"], 4),
            "verdict": _verdict(r["score"]),
            "severity": r.get("severity"),
            "category": r.get("category"),
            "rule_text": r.get("rule_text"),
        }
        for r in results
    ]

    summary = {
        "total": len(checks),
        "escalate": sum(1 for c in checks if c["verdict"] == "escalate"),
        "flag": sum(1 for c in checks if c["verdict"] == "flag"),
        "clean": sum(1 for c in checks if c["verdict"] == "clean"),
    }

    return {
        "ok": True,
        "skill_id": skill_id,
        "artifact": str(artifact_path),
        "fact_summary": fact_summary,
        "thresholds": {"low": LOW_THRESHOLD, "high": HIGH_THRESHOLD},
        "checks": checks,
        "summary": summary,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("skill_id")
    p.add_argument("artifact_path", type=Path)
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args()

    result = run_layer2(args.skill_id, args.artifact_path)

    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    if not result["ok"]:
        print(f"ERROR [{result.get('phase', '?')}]  {result['error']}", file=sys.stderr)
        return 1

    s = result["summary"]
    print(
        f"Layer 2  {result['skill_id']}  "
        f"— {s['escalate']} escalate / {s['flag']} flag / {s['clean']} clean"
    )
    for c in result["checks"]:
        if c["verdict"] != "clean":
            tag = c["verdict"].upper()
            print(f"  [{tag:8}]  score={c['score']:.4f}  [{c['severity']}]  {c['constraint_id']}")
            print(f"             {c['rule_text']}")
    return 0 if s["escalate"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
