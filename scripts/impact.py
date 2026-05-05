#!/usr/bin/env python3
"""CLI wrapper around server.impact_analysis — print the blast radius of a skill.

Usage:
    python scripts/impact.py skill:schema-review
    python scripts/impact.py skill:schema-review-v1

Environment:
    MONGODB_URI    Mongo connection string (default: mongodb://localhost:27017)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import impact_analysis  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print("usage: impact.py <skill_id>", file=sys.stderr)
        sys.exit(2)
    skill_id = sys.argv[1]
    fn = getattr(impact_analysis, "fn", impact_analysis)
    result = fn(skill_id=skill_id)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        sys.exit(1)
    print(f"skill: {result['skill']}  (output_type: {result['output_type']})")
    print(f"\ndirect consumers ({len(result['direct_consumers'])}):")
    for c in result["direct_consumers"]:
        print(f"  {c['_id']:30s} {c['name']}  ({c['input_type']} -> {c['output_type']})")
    print(f"\ntransitive downstream ({len(result['transitive_downstream'])}):")
    for c in result["transitive_downstream"]:
        print(f"  depth {c['depth']}  {c['_id']:25s} {c['name']}")
    print(f"\nincompatible edges ({len(result['incompatible_edges'])}):")
    for e in result["incompatible_edges"]:
        note = f"  — {e['note']}" if e.get("note") else ""
        print(f"  {e['from_skill']} -> {e['to_skill']}{note}")


if __name__ == "__main__":
    main()
