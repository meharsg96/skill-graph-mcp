#!/usr/bin/env python3
"""Validate a live skill artifact against its declared output contract.

Bridges the gap between the static-fixture contract harness
(`tests/test_contract_fixtures.py`) and live agent output. The fixture
harness proves the contracts are coherent; this script proves the
agent's actual output conforms.

Usage:
    python scripts/validate_artifact.py <skill_id> <artifact_path>

The artifact must be a JSON file matching the producer skill's declared
`output.schema`. Returns 0 on validation success, 1 on schema violation.
Prints localized error path on failure.

Design — why a script, not an MCP tool: validation of agent output is
a measurement / CI concern, not an agent-facing capability. Keeping it
in scripts/ matches the model used by `analyze.py` and
`measure_baseline.py`.

Intended use case (RA1 — Run-Artifact-1):
    1. In a fresh Claude Code session, prompt the agent to *execute*
       a skill (e.g. produce a schema_recommendation given a
       query_patterns input).
    2. Capture the structured output to disk.
    3. `python scripts/validate_artifact.py skill:schema-review out.json`
    4. Aggregate pass/fail across N runs to compute a Layer 1 catch
       rate on live data.

This closes the "static fixtures, not live output" limitation in the
contract-harness narrative — converts a hand-waved claim into a
measured number.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from jsonschema import Draft7Validator
from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "schema" / "contracts"
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
LOCAL_DIR = Path(
    os.environ.get("SKILL_GRAPH_LOCAL_DIR", str(Path.home() / ".skill-graph-local"))
).expanduser()
LOCAL_CONTRACTS_DIR = LOCAL_DIR / "contracts"


def schema_id_to_path(schema_id: str) -> Path:
    parts = schema_id.split(":")
    if len(parts) != 3 or parts[0] != "schema":
        raise ValueError(f"unrecognized schema id: {schema_id!r}")
    _, name, version = parts
    filename = f"{name}.{version}.json"
    repo_path = CONTRACTS_DIR / filename
    if repo_path.is_file():
        return repo_path
    return LOCAL_CONTRACTS_DIR / filename  # caller checks .is_file()


def validate(skill_id: str, artifact_path: Path) -> dict:
    client = MongoClient(MONGODB_URI)
    skill = client[DB_NAME].skills.find_one({"_id": skill_id})
    client.close()
    if not skill:
        return {"ok": False, "error": f"skill '{skill_id}' not found"}

    output = (skill.get("output") or {})
    schema_id = output.get("schema")
    if not schema_id:
        return {"ok": False, "error": f"skill '{skill_id}' has no output.schema"}
    contract_path = schema_id_to_path(schema_id)
    if not contract_path.is_file():
        return {"ok": False, "error": f"no contract file for {schema_id}"}
    if not artifact_path.is_file():
        return {"ok": False, "error": f"artifact not found: {artifact_path}"}

    schema = json.loads(contract_path.read_text())
    try:
        artifact = json.loads(artifact_path.read_text())
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "skill_id": skill_id,
            "schema_id": schema_id,
            "phase": "parse",
            "error": f"artifact is not valid JSON: {e}",
        }

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(artifact), key=lambda e: list(e.absolute_path))
    if not errors:
        return {
            "ok": True,
            "skill_id": skill_id,
            "schema_id": schema_id,
            "artifact": str(artifact_path),
        }

    return {
        "ok": False,
        "skill_id": skill_id,
        "schema_id": schema_id,
        "phase": "schema",
        "violations": [
            {
                "message": e.message,
                "path": list(e.absolute_path),
                "validator": e.validator,
            }
            for e in errors
        ],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("skill_id")
    p.add_argument("artifact_path", type=Path)
    args = p.parse_args()

    r = validate(args.skill_id, args.artifact_path)
    if r["ok"]:
        print(f"PASS  {args.skill_id}  {r['schema_id']}  {r['artifact']}")
        return 0

    if "violations" in r:
        print(f"FAIL  {args.skill_id}  {r['schema_id']}", file=sys.stderr)
        for v in r["violations"]:
            path = ".".join(str(p) for p in v["path"]) or "<root>"
            print(f"  {path}: {v['message']}", file=sys.stderr)
        return 1

    print(f"ERROR  {r.get('error') or r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
