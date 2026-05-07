"""Idempotently merge a graph-emitted hooks fragment into a Claude Code
settings file (settings.local.json by default).

Identification rule for graph-owned hooks:
  any hook entry whose env.CONSTRAINT_ID is set
   ↳ owned by emit_hooks.py — replaceable
  any hook entry without env.CONSTRAINT_ID
   ↳ user-authored — preserved untouched

The merge:
  1. Read existing settings (or start with `{}` if file doesn't exist)
  2. Drop every existing hook with env.CONSTRAINT_ID set
  3. Append every hook from the fragment's "hooks" array
  4. Write back, preserving formatting (indent=2, trailing newline)

Idempotency: running this script N times in a row produces the same
output. The fragment is the source of truth for graph-owned hooks;
user-added hooks survive every merge.

Usage:
    python scripts/merge_settings.py \\
        --fragment .claude/hooks.generated.json \\
        --settings .claude/settings.local.json

    python scripts/merge_settings.py --dry-run    # show diff, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def is_graph_owned(hook: dict) -> bool:
    """A hook is graph-owned iff env.CONSTRAINT_ID is set on it."""
    env = hook.get("env") or {}
    return bool(env.get("CONSTRAINT_ID"))


def merge(settings: dict, fragment: dict) -> tuple[dict, dict]:
    """Return (merged_settings, change_summary).

    Pure function — does not read or write disk.
    """
    user_hooks = [h for h in settings.get("hooks", []) if not is_graph_owned(h)]
    graph_hooks = list(fragment.get("hooks", []))

    merged = dict(settings)
    merged["hooks"] = user_hooks + graph_hooks

    summary = {
        "user_hooks_preserved": len(user_hooks),
        "graph_hooks_dropped": sum(
            1 for h in settings.get("hooks", []) if is_graph_owned(h)
        ),
        "graph_hooks_added": len(graph_hooks),
    }
    return merged, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fragment", type=Path,
                        default=Path(".claude/hooks.generated.json"),
                        help="emit_hooks.py output file")
    parser.add_argument("--settings", type=Path,
                        default=Path(".claude/settings.local.json"),
                        help="Claude Code settings file to merge into")
    parser.add_argument("--dry-run", action="store_true",
                        help="print summary, write nothing")
    args = parser.parse_args()

    if not args.fragment.exists():
        print(f"ERROR: fragment file not found: {args.fragment}", file=sys.stderr)
        print("Run scripts/emit_hooks.py first.", file=sys.stderr)
        return 1

    fragment = json.loads(args.fragment.read_text())

    settings: dict = {}
    if args.settings.exists():
        try:
            settings = json.loads(args.settings.read_text())
        except json.JSONDecodeError as e:
            print(f"ERROR: settings file is not valid JSON: {e}", file=sys.stderr)
            return 1

    merged, summary = merge(settings, fragment)

    print(f"merge plan for {args.settings}:")
    print(f"  user-authored hooks preserved: {summary['user_hooks_preserved']}")
    print(f"  graph-owned hooks replaced:    {summary['graph_hooks_dropped']}")
    print(f"  graph-owned hooks now present: {summary['graph_hooks_added']}")

    if args.dry_run:
        print("\n--dry-run: no file written.")
        return 0

    args.settings.parent.mkdir(parents=True, exist_ok=True)
    args.settings.write_text(json.dumps(merged, indent=2) + "\n")
    print(f"\nwrote {args.settings}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
