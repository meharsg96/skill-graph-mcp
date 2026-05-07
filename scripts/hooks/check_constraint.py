#!/usr/bin/env python3
"""Claude Code PreToolUse hook runtime.

Reads the constraint identified by $CONSTRAINT_ID, evaluates the tool
input, and signals to Claude Code via exit code:

    exit 0  → allow (the call proceeds)
    exit 2  → block (stderr is shown to Claude as the reason)

DENY-ONLY ENFORCEMENT — this hook never returns `updatedInput`. Per
design: silent rewriting masks intent; block-with-explanation is the
agreed contract.

Severity → exit-code mapping:
    fail → exit 2 (block, with rule_text)
    warn → exit 2 (block, with WARN: prefix on rule_text)
    note → exit 0, stderr-only note (informational, not blocking)

Reads (env):
    CONSTRAINT_ID    constraint document _id  (required, else fail-open)
    MONGODB_URI      mongodb connection string
    SKILL_GRAPH_DB   database name (default: skill_graph)
    SESSION_ID       optional — tags the audit row in db.runs
    HOOK_AUDIT       set to "1" to log every invocation (matches and non-matches)

Reads (stdin): JSON payload from Claude Code with
    tool_name, tool_input, session_id, transcript_path, cwd, ...

Fail-open contract: ANY infrastructure failure (mongodb unreachable,
pymongo missing, garbage stdin, missing env, unknown constraint) results
in exit 0. The hook must never block tool calls due to plumbing.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys

try:
    from pymongo import MongoClient
except ImportError:
    print("check_constraint.py: pymongo not available; failing open",
          file=sys.stderr)
    sys.exit(0)


def _allow() -> int:
    return 0


def _deny(reason: str) -> int:
    """Block with the rule_text on stderr. Claude shows it to the user."""
    print(reason[:500], file=sys.stderr)
    return 2


def _exit_for(severity: str, reason: str) -> int:
    if severity == "fail":
        return _deny(reason)
    if severity == "warn":
        return _deny(f"WARN: {reason}")
    # note: log to stderr but don't block
    print(f"NOTE: {reason[:300]}", file=sys.stderr)
    return 0


def _log_run(client, db_name: str, *, constraint_id: str, tool_name: str,
             outcome: str, matched: bool) -> None:
    """Audit row to db.runs. Never raises — failed write must not block."""
    try:
        client[db_name].runs.insert_one({
            "tool": "hook:check_constraint",
            "params": {
                "constraint_id": constraint_id,
                "tool_name": tool_name,
            },
            "outcome": outcome,
            "matched": matched,
            "session_id": os.environ.get(
                "SESSION_ID", f"session:hook-{os.getpid()}"),
            "timestamp": datetime.datetime.now(datetime.timezone.utc),
        })
    except Exception as e:  # noqa: BLE001
        print(f"check_constraint.py: audit log failed: {e}", file=sys.stderr)


# ── per-constraint evaluators (return True if violation is matched) ─────────

def _match_no_verify(tool_name: str, tool_input: dict, payload: dict) -> bool:
    if tool_name != "Bash":
        return False
    cmd = tool_input.get("command", "")
    return "--no-verify" in cmd


def _match_force_push_main(tool_name: str, tool_input: dict, payload: dict) -> bool:
    if tool_name != "Bash":
        return False
    cmd = tool_input.get("command", "")
    if "git push" not in cmd:
        return False
    if "--force" not in cmd and "--force-with-lease" not in cmd:
        return False
    return bool(re.search(r"\b(main|master)\b", cmd))


def _match_destructive_rm(tool_name: str, tool_input: dict, payload: dict) -> bool:
    if tool_name != "Bash":
        return False
    cmd = tool_input.get("command", "")
    if not re.search(r"\brm\s+-[rf]+\b", cmd):
        return False
    safe_targets = re.compile(
        r"\brm\s+-[rf]+\s+(?:\"|')?(\./)?(build|dist|\.cache|node_modules|/tmp/)"
    )
    return not safe_targets.search(cmd)


def _match_generated_url(tool_name: str, tool_input: dict, payload: dict) -> bool:
    # URL provenance can't be verified from the hook payload alone — fail-open.
    return False


def _match_delegate_understanding(tool_name: str, tool_input: dict, payload: dict) -> bool:
    if tool_name not in {"Task", "Agent"}:
        return False
    prompt = tool_input.get("prompt") or payload.get("prompt", "")
    if not prompt:
        return False
    delegated_phrases = [
        r"based on (?:your|the) findings",
        r"based on (?:your|the) research",
        r"using what you (?:discovered|found)",
        r"after you investigate,?\s+(?:fix|implement|build)",
    ]
    return any(re.search(p, prompt, re.IGNORECASE) for p in delegated_phrases)


def _match_parallel_independent(tool_name: str, tool_input: dict, payload: dict) -> bool:
    # Cross-batch dependency check requires context the hook payload doesn't carry.
    return False


EVALUATORS = {
    "constraint:claude-code:bash:no-verify-bypass": _match_no_verify,
    "constraint:claude-code:bash:no-force-push-main": _match_force_push_main,
    "constraint:claude-code:bash:destructive-requires-confirm": _match_destructive_rm,
    "constraint:claude-code:web:no-generated-urls": _match_generated_url,
    "constraint:claude-code:agent:no-delegate-understanding": _match_delegate_understanding,
    "constraint:claude-code:agent:parallel-independent-only": _match_parallel_independent,
}


def _evaluate_inline(constraint: dict, tool_name: str,
                     tool_input: dict, payload: dict) -> bool:
    """JSON-driven evaluator for local-overlay constraints. See
    skills/harness/SKILL.md § Local-only constraints for the schema."""
    hc = constraint.get("hook_config") or {}
    ev = hc.get("evaluator") or {}
    kind = ev.get("type")
    if kind == "regex_in_field":
        field = ev.get("field", "command")
        pattern = ev.get("pattern", "")
        if not pattern:
            return False
        value = tool_input.get(field, "")
        if not isinstance(value, str):
            value = str(value)
        return bool(re.search(pattern, value, re.IGNORECASE))
    if kind == "always_match":
        return True
    if kind == "never_match":
        return False
    return False


def main() -> int:
    constraint_id = os.environ.get("CONSTRAINT_ID")
    if not constraint_id:
        return _allow()

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    mongo_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("SKILL_GRAPH_DB", "skill_graph")

    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        constraint = client[db_name].constraints.find_one({"_id": constraint_id})
    except Exception as e:  # noqa: BLE001
        print(f"check_constraint.py: mongo unavailable ({e}); failing open",
              file=sys.stderr)
        return _allow()

    if not constraint:
        client.close()
        return _allow()

    # Pick evaluator: inline for local constraints, EVALUATORS for canonical.
    if constraint_id.startswith("constraint:local:"):
        matched = _evaluate_inline(constraint, tool_name, tool_input, payload)
    else:
        evaluator = EVALUATORS.get(constraint_id)
        if not evaluator:
            client.close()
            return _allow()
        matched = evaluator(tool_name, tool_input, payload)

    if not matched:
        if os.environ.get("HOOK_AUDIT") == "1":
            _log_run(client, db_name,
                     constraint_id=constraint_id, tool_name=tool_name,
                     outcome="allow", matched=False)
        client.close()
        return _allow()

    severity = constraint.get("severity", "warn")
    reason = constraint.get("rule_text", "Constraint matched.")
    exit_code = _exit_for(severity, reason)
    outcome_label = "deny" if exit_code == 2 else "allow"
    _log_run(client, db_name,
             constraint_id=constraint_id, tool_name=tool_name,
             outcome=outcome_label, matched=True)
    client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
