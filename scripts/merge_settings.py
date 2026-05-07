"""Idempotently merge a graph-emitted hooks fragment into a Claude Code
settings file (settings.local.json by default).

Output schema (per https://code.claude.com/docs/en/hooks):

    "hooks": {
      "<EventName>": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "..."}]}
      ]
    }

Identification rule for graph-owned commands: a hook command string that
contains `CONSTRAINT_ID=constraint:` is graph-owned. User-authored
commands lack that signature and are preserved untouched.

Merge algorithm (per event):
  1. For each event in the existing settings hooks record, walk its
     matcher entries.
  2. Within each entry, drop any `hooks[]` command that is graph-owned.
  3. If the matcher entry's `hooks[]` becomes empty, drop the matcher
     entry entirely (it was wholly graph-owned).
  4. Add the fragment's matcher entries on top, merging by matcher
     string (so user `Bash` and graph `Bash` entries collapse into one).

Idempotency: running this script N times in a row produces the same
output. The fragment is the source of truth for graph-owned hooks;
user-added hooks survive every merge.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def is_graph_owned(command_entry: dict) -> bool:
    """A hook command entry is graph-owned iff its `command` string carries
    the CONSTRAINT_ID env-var marker."""
    if command_entry.get("type") != "command":
        return False
    cmd = command_entry.get("command") or ""
    return "CONSTRAINT_ID=constraint:" in cmd


def _strip_graph_owned(matcher_entries: list) -> list:
    """Remove graph-owned commands from each matcher entry. If a matcher
    entry's hooks list becomes empty, drop it entirely."""
    cleaned = []
    for entry in matcher_entries:
        sub_hooks = entry.get("hooks") or []
        kept = [h for h in sub_hooks if not is_graph_owned(h)]
        if kept:
            new_entry = dict(entry)
            new_entry["hooks"] = kept
            cleaned.append(new_entry)
    return cleaned


def _merge_event_entries(existing: list, fragment: list) -> list:
    """Merge two arrays of {matcher, hooks} entries. Same `matcher` string
    collapses into one entry whose `hooks` list is the concatenation."""
    by_matcher: dict[str, dict] = {}
    out: list[dict] = []
    for entry in existing + fragment:
        m = entry.get("matcher", "")
        if m not in by_matcher:
            new_entry = {"matcher": m, "hooks": list(entry.get("hooks") or [])}
            by_matcher[m] = new_entry
            out.append(new_entry)
        else:
            by_matcher[m]["hooks"].extend(entry.get("hooks") or [])
    return out


def merge(settings: dict, fragment: dict) -> tuple[dict, dict]:
    """Pure merge function — does not read or write disk."""
    existing_hooks = settings.get("hooks") or {}
    fragment_hooks = fragment.get("hooks") or {}

    # Count what's about to be dropped (for the summary)
    dropped = 0
    for event_entries in existing_hooks.values():
        for entry in event_entries:
            for h in entry.get("hooks") or []:
                if is_graph_owned(h):
                    dropped += 1

    user_kept = 0
    merged_hooks: dict[str, list] = {}
    all_events = set(existing_hooks.keys()) | set(fragment_hooks.keys())
    for event in all_events:
        existing_for_event = _strip_graph_owned(existing_hooks.get(event, []))
        for entry in existing_for_event:
            user_kept += len(entry.get("hooks") or [])
        fragment_for_event = fragment_hooks.get(event, [])
        merged_hooks[event] = _merge_event_entries(
            existing_for_event, fragment_for_event,
        )

    added = sum(
        len(h.get("hooks") or [])
        for entries in fragment_hooks.values()
        for h in entries
    )

    merged = dict(settings)
    merged["hooks"] = merged_hooks

    summary = {
        "user_hooks_preserved": user_kept,
        "graph_hooks_dropped": dropped,
        "graph_hooks_added": added,
        "events": sorted(merged_hooks.keys()),
    }
    return merged, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fragment", type=Path,
                        default=Path(".claude/hooks.generated.json"))
    parser.add_argument("--settings", type=Path,
                        default=Path(".claude/settings.local.json"))
    parser.add_argument("--dry-run", action="store_true")
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
    print(f"  events: {summary['events']}")

    if args.dry_run:
        print("\n--dry-run: no file written.")
        return 0

    args.settings.parent.mkdir(parents=True, exist_ok=True)
    args.settings.write_text(json.dumps(merged, indent=2) + "\n")
    print(f"\nwrote {args.settings}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
