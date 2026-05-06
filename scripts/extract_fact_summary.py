#!/usr/bin/env python3
"""Extract a canonical fact summary from a skill artifact.

Layer 2 pipeline entry point. Raw artifacts (JSON, JSX, code) are
semantically opaque to embedding models — both violating and compliant
artifacts are "about" the same topic. A canonical fact summary converts
the artifact into structured natural-language claims that embeddings can
distinguish.

The summary is then compared via $vectorSearch against violation
paraphrases stored in db.constraints. See notes/PLAN-layer2.md.

Usage:
    python scripts/extract_fact_summary.py skill:leafygreen-ui artifact.json
    python scripts/extract_fact_summary.py skill:ui-builder artifact.json --raw

Environment:
    ANTHROPIC_API_KEY   Required for LLM extraction
    MONGODB_URI         MongoDB connection (default: localhost:27017)
    SKILL_GRAPH_DB      Database name (default: skill_graph)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")

EXTRACTION_PROMPT = """\
You are a fact extractor for a UI/code compliance validator.

Given an artifact (JSON component spec, JSX, or application code), extract
a canonical fact summary: structured natural-language statements about what
the artifact ACTUALLY DOES. Do not infer intent or evaluate correctness —
only state observable facts.

Focus on:
- Color values used (exact hex or token names)
- Spacing/padding/margin values (exact px values or token multiples)
- Typography: font sizes, weights, families
- Component types and props
- ARIA attributes and accessibility signals present or absent
- Layout patterns

Format: plain prose, one sentence per observable fact. No bullet points.
No evaluative language ("correctly", "incorrectly"). Just facts.

Artifact:
{artifact}
"""


def extract_fact_summary(skill_id: str, artifact_path: Path) -> dict:
    try:
        import anthropic
    except ImportError:
        return {"ok": False, "error": "anthropic package not installed. Run: pip install anthropic"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "error": "ANTHROPIC_API_KEY not set"}

    if not artifact_path.is_file():
        return {"ok": False, "error": f"artifact not found: {artifact_path}"}

    artifact_text = artifact_path.read_text()

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT.format(artifact=artifact_text)
        }]
    )

    summary = message.content[0].text.strip()
    return {
        "ok": True,
        "skill_id": skill_id,
        "artifact": str(artifact_path),
        "fact_summary": summary,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("skill_id")
    p.add_argument("artifact_path", type=Path)
    p.add_argument("--raw", action="store_true", help="Print raw summary text only")
    args = p.parse_args()

    result = extract_fact_summary(args.skill_id, args.artifact_path)

    if not result["ok"]:
        print(f"ERROR  {result['error']}", file=sys.stderr)
        return 1

    if args.raw:
        print(result["fact_summary"])
    else:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
