#!/usr/bin/env python3
"""Field-path-aware blast radius for schema changes.

Consumer-driven contracts insight (per CDC research): the contract is
the consumer's required projection of the producer's output, not the
producer's full schema. Therefore, the right blast-radius algorithm
is:

  1. Diff producer's old vs new schema at the field-path level
  2. Query only contract edges whose `fields_used` intersect the
     changed paths
  3. Static-validate impacted edges; traverse downstream only from
     broken nodes

This replaces the naive `db.skills.find({"input.type": X})` approach
with one that ignores edges whose consumer doesn't project the
changed field. At 100+ skills with sparse field-level dependency,
the cost difference matters.

Usage:
    python scripts/blast_radius.py <skill_id> <new_schema_path>

Example:
    python scripts/blast_radius.py skill:schema-review \\
        /tmp/schema-recommendation.v3-draft.json

The script prints a timed report:
    - schema diff (added_required, removed_required, type_changes)
    - affected edges (only those whose fields_used intersects)
    - total outgoing edges and impact ratio
    - elapsed milliseconds for diff and edge query

Designed as a measurement utility, not an MCP tool. Output feeds
Blog 3 with a real number for the blast-radius claim.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "schema" / "contracts"
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")


def schema_id_to_path(schema_id: str) -> Path:
    parts = schema_id.split(":")
    if len(parts) != 3 or parts[0] != "schema":
        raise ValueError(f"unrecognized schema id: {schema_id!r}")
    _, name, version = parts
    return CONTRACTS_DIR / f"{name}.{version}.json"


def diff_schemas(old: dict, new: dict) -> dict:
    """Field-path level diff between two JSON Schema documents.

    Returns added_required, removed_required, type_changes, and
    constraint_changes (enum, minimum, maximum, etc.). Field paths
    are flat top-level keys here — for nested objects/arrays a real
    implementation would walk recursively. Sufficient for the
    consumer-projection demo.
    """
    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    added_required = new_required - old_required
    removed_required = old_required - new_required

    old_props = old.get("properties", {})
    new_props = new.get("properties", {})

    type_changes = []
    constraint_changes = []
    for k in old_props.keys() & new_props.keys():
        op = old_props[k]
        np = new_props[k]
        if op.get("type") != np.get("type"):
            type_changes.append(k)
        # Enum / range constraints — value-range drift is structurally
        # detectable via constraint diff but semantically requires evals.
        if op.get("enum") != np.get("enum"):
            constraint_changes.append(f"{k}:enum")
        for c in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
            if op.get(c) != np.get(c):
                constraint_changes.append(f"{k}:{c}")

    all_changed = sorted(
        added_required
        | removed_required
        | set(type_changes)
        | {c.split(":")[0] for c in constraint_changes}
    )

    return {
        "added_required":      sorted(added_required),
        "removed_required":    sorted(removed_required),
        "type_changes":        sorted(type_changes),
        "constraint_changes":  sorted(constraint_changes),
        "all_changed":         all_changed,
    }


def field_overlaps(fields_used: list[str], changed: list[str]) -> list[str]:
    """Return the subset of `changed` fields whose path matches any
    entry in `fields_used`. Strips array notation `[*]` so
    `queries[*].pattern` matches a top-level change to `queries`."""
    used_roots = {f.split("[")[0].split(".")[0] for f in fields_used}
    changed_roots = {f.split("[")[0].split(".")[0] for f in changed}
    return sorted(used_roots & changed_roots)


def measure(skill_id: str, new_schema_path: Path) -> dict:
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]

    skill = db.skills.find_one({"_id": skill_id})
    if not skill:
        client.close()
        return {"error": f"Skill '{skill_id}' not found"}

    output_schema_id = (skill.get("output") or {}).get("schema")
    if not output_schema_id:
        client.close()
        return {"error": f"Skill '{skill_id}' has no output.schema declared"}

    current_path = schema_id_to_path(output_schema_id)
    if not current_path.is_file():
        client.close()
        return {"error": f"No contract file for {output_schema_id} at {current_path}"}
    if not new_schema_path.is_file():
        client.close()
        return {"error": f"New schema not found: {new_schema_path}"}

    old = json.loads(current_path.read_text())
    new = json.loads(new_schema_path.read_text())

    t0 = time.perf_counter()
    diff = diff_schemas(old, new)
    diff_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    # Field-path query — only edges whose fields_used roots intersect changed roots
    changed_roots = list({f.split("[")[0].split(".")[0] for f in diff["all_changed"]})
    if changed_roots:
        # Match where any element of fields_used starts with one of the changed roots
        regex = "|".join(f"^{r}(\\[|$|\\.)" for r in changed_roots)
        affected_cursor = db.edges.find({
            "from_skill": skill_id,
            "fields_used": {"$regex": regex}
        })
    else:
        affected_cursor = iter([])
    affected_edges = list(affected_cursor)
    edge_ms = (time.perf_counter() - t1) * 1000

    total_outgoing = db.edges.count_documents({"from_skill": skill_id})
    client.close()

    # Per-edge: which specific fields hit?
    affected_detail = []
    for e in affected_edges:
        overlap = field_overlaps(e.get("fields_used", []), diff["all_changed"])
        affected_detail.append({
            "edge": e["_id"],
            "to_skill": e["to_skill"],
            "compatible_currently": e.get("compatible"),
            "fields_in_play": overlap,
        })

    return {
        "skill_id": skill_id,
        "output_schema": output_schema_id,
        "diff": diff,
        "affected_edges": affected_detail,
        "total_outgoing_edges": total_outgoing,
        "impact_ratio": (
            f"{len(affected_detail)}/{total_outgoing}" if total_outgoing else "0/0"
        ),
        "diff_ms": round(diff_ms, 2),
        "edge_query_ms": round(edge_ms, 2),
        "total_ms": round(diff_ms + edge_ms, 2),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("skill_id")
    p.add_argument("new_schema_path", type=Path)
    args = p.parse_args()

    result = measure(args.skill_id, args.new_schema_path)
    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return 1

    print(f"\nField-path blast radius for {result['skill_id']}")
    print(f"Output schema: {result['output_schema']}\n")

    print("Schema diff:")
    for k, v in result["diff"].items():
        if v:
            print(f"  {k}: {v}")
    print()

    print(f"Affected edges: {result['impact_ratio']} (consumers projecting changed fields)")
    for e in result["affected_edges"]:
        compat = "compatible" if e["compatible_currently"] else "incompatible"
        print(f"  - {e['edge']}  ({compat})")
        print(f"      fields_in_play: {e['fields_in_play']}")
    print()

    print(f"Timing: diff {result['diff_ms']}ms, edge query {result['edge_query_ms']}ms, total {result['total_ms']}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
