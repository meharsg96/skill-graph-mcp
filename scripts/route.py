#!/usr/bin/env python3
"""CLI wrapper around server.route_task — print the chain that produces a target.

Usage:
    python scripts/route.py ui_components
    python scripts/route.py test_suite

Environment:
    MONGODB_URI    Mongo connection string (default: mongodb://localhost:27017)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import route_task  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print("usage: route.py <target_output_type>", file=sys.stderr)
        sys.exit(2)
    target = sys.argv[1]
    fn = getattr(route_task, "fn", route_task)
    result = fn(target_output_type=target)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        sys.exit(1)
    print(f"target: {result['target']}  (output: {result['target_output']})")
    print("chain:")
    for sid, name in zip(result["chain"], result["chain_names"]):
        marker = " ← target" if sid == result["chain"][-1] else ""
        print(f"  {sid:30s} {name}{marker}")


if __name__ == "__main__":
    main()
