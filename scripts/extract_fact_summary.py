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
    OPENROUTER_API_KEY   Required for LLM extraction
    OPENROUTER_MODEL     Model to use (default: anthropic/claude-haiku-4-5)
    MONGODB_URI          MongoDB connection (default: localhost:27017)
    SKILL_GRAPH_DB       Database name (default: skill_graph)
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

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-haiku-4-5"

EXTRACTION_PROMPT = """\
You are a fact extractor for a UI/code compliance validator.

Given an artifact (JSON component spec, JSX, or application code), extract
a canonical fact summary: structured natural-language statements about what
the artifact ACTUALLY DOES. Do not infer intent or evaluate correctness —
only state observable facts.

Critical rules for color observations:
- ALWAYS state text color and background color together in one sentence.
  Say "text color X on background Y" — never split them across sentences.
- Include exact hex values. Do not paraphrase (#00ED64, not "green").

Focus on:
- Text color paired with its background color (one combined sentence)
- Spacing/padding/margin values as exact px amounts with their multiples
- Typography: font size, weight, family
- Component type, variant, and key props
- ARIA attributes present or explicitly absent

Format: plain prose, one sentence per observable fact. No bullet points.
No evaluative language. Just facts.

Artifact:
{artifact}
"""


def extract_fact_summary(skill_id: str, artifact_path: Path) -> dict:
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai package not installed. Run: pip install openai"}

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"ok": False, "error": "OPENROUTER_API_KEY not set"}

    if not artifact_path.is_file():
        return {"ok": False, "error": f"artifact not found: {artifact_path}"}

    artifact_text = artifact_path.read_text()
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)

    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    response = client.chat.completions.create(
        model=model,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT.format(artifact=artifact_text),
        }],
    )

    summary = response.choices[0].message.content.strip()
    usage = response.usage
    return {
        "ok": True,
        "skill_id": skill_id,
        "artifact": str(artifact_path),
        "model": model,
        "fact_summary": summary,
        "input_tokens": usage.prompt_tokens if usage else None,
        "output_tokens": usage.completion_tokens if usage else None,
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
