"""Emit a Claude Code settings.json hooks fragment from db.constraints.

Reads constraints whose skill_id starts with `skill:claude-code:` and translates
them into PreToolUse / UserPromptSubmit hook entries pointing at
scripts/hooks/check_constraint.py.

Design choices:
- DENY-ONLY. Hooks return outcome="deny" with rule_text as the reason.
  Silent input rewriting is intentionally not supported — masking intent
  is more dangerous than blocking with an explanation.
- Severity:fail → outcome:deny. Severity:warn → outcome:ask
  (still surfaces the constraint, lets the user decide).
- Matchers are derived from a small dispatch table keyed by constraint_id.
  If a constraint has no entry, it's skipped with a comment.
- Output is a JSON fragment, not a full settings.json — caller merges it
  into their existing settings under .hooks.

Usage:
    python scripts/emit_hooks.py --output .claude/hooks.generated.json
    python scripts/emit_hooks.py --print
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = str(REPO_ROOT / "scripts" / "hooks" / "check_constraint.py")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")

# Maps constraint_id → hook config. Each entry produces one hook entry
# in settings.json. Keep this table small and explicit; expressing every
# constraint as a regex matcher is brittle, so the runtime hook script
# does the real evaluation. The matcher here is a pre-filter only.
MATCHERS: dict[str, dict] = {
    "constraint:claude-code:bash:no-verify-bypass": {
        "event": "PreToolUse",
        "if": "Bash(*--no-verify*)",
    },
    "constraint:claude-code:bash:no-force-push-main": {
        "event": "PreToolUse",
        "if": "Bash(*push*--force*)",
    },
    "constraint:claude-code:bash:destructive-requires-confirm": {
        "event": "PreToolUse",
        "if": "Bash(rm -rf*)",
    },
    "constraint:claude-code:web:no-generated-urls": {
        "event": "PreToolUse",
        "if": "WebFetch(*)",
    },
    "constraint:claude-code:agent:no-delegate-understanding": {
        "event": "SubagentStart",
        "if": "*",
    },
    "constraint:claude-code:agent:parallel-independent-only": {
        "event": "SubagentStart",
        "if": "*",
    },
    "constraint:claude-code:model:fast-mode-opus-only": {
        "event": "UserPromptExpansion",
        "if": "/fast",
    },
}


def _outcome_for(severity: str) -> str:
    """fail → deny (block). warn → ask (surface to user). note → allow (log only)."""
    return {"fail": "deny", "warn": "ask", "note": "allow"}.get(severity, "ask")


def emit_hooks(db) -> dict:
    """Return a settings.json-compatible {hooks: [...]} fragment."""
    fragment = {"hooks": []}
    skipped: list[tuple[str, str]] = []

    cursor = db.constraints.find({"skill_id": {"$regex": "^skill:claude-code:"}})
    for c in cursor:
        cid = c["_id"]
        if cid not in MATCHERS:
            skipped.append((cid, "no matcher entry"))
            continue
        m = MATCHERS[cid]
        fragment["hooks"].append({
            "event": m["event"],
            "if": m["if"],
            "tool": HOOK_SCRIPT,
            "timeout": 5000,
            "env": {
                "CONSTRAINT_ID": cid,
                "MONGODB_URI": MONGODB_URI,
                "SKILL_GRAPH_DB": DB_NAME,
            },
            "outcome": _outcome_for(c["severity"]),
            "reason_preview": c["rule_text"][:140],
        })

    fragment["_meta"] = {
        "generated_from": "db.constraints",
        "constraint_count": len(fragment["hooks"]),
        "skipped": skipped,
    }
    return fragment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None,
                        help="write fragment to this path (default: stdout)")
    parser.add_argument("--print", action="store_true",
                        help="print fragment to stdout")
    args = parser.parse_args()

    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    fragment = emit_hooks(db)
    client.close()

    text = json.dumps(fragment, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
        print(f"wrote {len(fragment['hooks'])} hooks → {args.output}")
        if fragment["_meta"]["skipped"]:
            print(f"skipped {len(fragment['_meta']['skipped'])} constraints "
                  f"with no matcher entry (see _meta.skipped)")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
