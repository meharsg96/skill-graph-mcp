"""Emit a Claude Code settings.json `hooks` fragment from db.constraints.

The output matches the schema documented at https://code.claude.com/docs/en/hooks:

    {
      "hooks": {
        "<EventName>": [
          {
            "matcher": "<tool-name | tool|tool | regex>",
            "hooks": [
              {"type": "command", "command": "..."}
            ]
          }
        ]
      }
    }

The matcher is a string. Plain alphanumeric/underscore/pipe = exact tool
name or pipe-list (e.g. "Bash", "Edit|Write"). Anything containing other
characters is interpreted as a JavaScript regex. Pattern-matching against
tool input fields is NOT done by the matcher — it's done inside the hook
script after stdin is read.

Hook command type: `command` — runs a shell command. Exit code conventions:
  exit 0 = allow (the call proceeds)
  exit 2 = block (stderr is shown to Claude as the reason)

DENY-ONLY ENFORCEMENT — the hook script never returns `updatedInput`,
so it cannot silently rewrite tool calls. Per design.

Usage:
    python scripts/emit_hooks.py --output .claude/hooks.generated.json
    python scripts/emit_hooks.py --print
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections import defaultdict
from pathlib import Path

from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = str(REPO_ROOT / "scripts" / "hooks" / "check_constraint.py")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
PYTHON_BIN = os.environ.get("HOOK_PYTHON", str(REPO_ROOT / "venv" / "bin" / "python"))

# Maps constraint_id → {event, matcher}. The matcher is a Claude Code
# tool-name pattern: a tool name, pipe-list, or regex if it contains
# regex chars. The hook script does the actual pattern matching against
# tool_input (e.g. command-line text) — the matcher only narrows which
# tools the hook fires on.
MATCHERS: dict[str, dict] = {
    "constraint:claude-code:bash:no-verify-bypass": {
        "event": "PreToolUse",
        "matcher": "Bash",
    },
    "constraint:claude-code:bash:no-force-push-main": {
        "event": "PreToolUse",
        "matcher": "Bash",
    },
    "constraint:claude-code:bash:destructive-requires-confirm": {
        "event": "PreToolUse",
        "matcher": "Bash",
    },
    "constraint:claude-code:web:no-generated-urls": {
        "event": "PreToolUse",
        "matcher": "WebFetch",
    },
    "constraint:claude-code:agent:no-delegate-understanding": {
        "event": "PreToolUse",
        # Claude Code names the subagent-spawning tool "Task". Match either.
        "matcher": "Task|Agent",
    },
    "constraint:claude-code:agent:parallel-independent-only": {
        "event": "PreToolUse",
        "matcher": "Task|Agent",
    },
}


def _build_command(constraint_id: str) -> str:
    """Shell command that runs the hook script with required env."""
    return (
        f"CONSTRAINT_ID={shlex.quote(constraint_id)} "
        f"MONGODB_URI={shlex.quote(MONGODB_URI)} "
        f"SKILL_GRAPH_DB={shlex.quote(DB_NAME)} "
        f"{shlex.quote(PYTHON_BIN)} {shlex.quote(HOOK_SCRIPT)}"
    )


def emit_hooks(db) -> dict:
    """Return a Claude Code settings.json-compatible `{hooks: {...}}` fragment."""
    # Group constraint_ids by (event, matcher). All constraints sharing a
    # (event, matcher) collapse into one matcher entry with multiple commands
    # in its `hooks` array — that way Bash matches once and dispatches to
    # every relevant constraint check in sequence.
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    skipped: list[tuple[str, str]] = []

    # Path 1: canonical claude-code constraints
    for c in db.constraints.find({"skill_id": {"$regex": "^skill:claude-code:"}}):
        cid = c["_id"]
        if cid not in MATCHERS:
            skipped.append((cid, "no matcher entry"))
            continue
        m = MATCHERS[cid]
        grouped[(m["event"], m["matcher"])].append(cid)

    # Path 2: local-overlay constraints with inline hook_config
    local_count = 0
    for c in db.constraints.find({
        "_id": {"$regex": "^constraint:local:"},
        "hook_config": {"$exists": True},
    }):
        hc = c["hook_config"]
        event = hc.get("event")
        matcher = hc.get("matcher") or hc.get("if") or ""
        if not event or not matcher:
            skipped.append((c["_id"], "hook_config missing event or matcher"))
            continue
        grouped[(event, matcher)].append(c["_id"])
        local_count += 1

    # Render to Claude Code's record shape: {EventName: [{matcher, hooks}]}
    hooks_record: dict[str, list[dict]] = defaultdict(list)
    for (event, matcher), constraint_ids in grouped.items():
        hooks_record[event].append({
            "matcher": matcher,
            "hooks": [
                {"type": "command", "command": _build_command(cid)}
                for cid in constraint_ids
            ],
        })

    fragment = {
        "hooks": dict(hooks_record),
        "_meta": {
            "generated_from": "db.constraints",
            "constraint_count": sum(len(v) for v in grouped.values()),
            "local_count": local_count,
            "skipped": skipped,
            "schema_version": "claude-code-hooks-v1",
        },
    }
    return fragment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args()

    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    fragment = emit_hooks(db)
    client.close()

    text = json.dumps(fragment, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
        n = fragment["_meta"]["constraint_count"]
        events = list(fragment["hooks"].keys())
        print(f"wrote {n} hooks across {len(events)} events ({events}) → {args.output}")
        if fragment["_meta"]["skipped"]:
            print(f"skipped {len(fragment['_meta']['skipped'])} constraints "
                  f"with no matcher entry")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
