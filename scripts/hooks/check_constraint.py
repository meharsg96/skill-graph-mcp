#!/usr/bin/env python3
"""PreToolUse / SubagentStart / UserPromptExpansion hook runtime.

Looks up the constraint identified by $CONSTRAINT_ID, evaluates the tool
input against constraint-specific logic, returns a JSON outcome:
  {"outcome": "allow"}                                  ← let the call proceed
  {"outcome": "deny", "reason": "..."}                  ← block with explanation
  {"outcome": "ask", "reason": "..."}                   ← surface to user

DENY-ONLY ENFORCEMENT — this hook never modifies tool input. Per design
discussion: silent rewriting masks intent. Block + explain instead.

Reads:
- $CONSTRAINT_ID   constraint document _id
- $MONGODB_URI     mongodb connection string
- $SKILL_GRAPH_DB  database name (default: skill_graph)
- stdin            JSON payload from Claude Code: {tool_name, tool_input, ...}

Writes:
- stdout: JSON outcome
- exit 0: hook completed (the JSON outcome, not exit code, decides allow/deny)
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
    # If pymongo isn't on the hook's PATH, fail-open with a stderr warning.
    # Better to let the call through than block on infrastructure issues.
    print(json.dumps({"outcome": "allow"}))
    print("check_constraint.py: pymongo not available; failing open",
          file=sys.stderr)
    sys.exit(0)


def _log_run(client, db_name: str, *, constraint_id: str, tool_name: str,
             outcome: str, matched: bool) -> None:
    """Log this hook invocation to db.runs for audit. Never raises —
    a failed write must not block the hook outcome."""
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
                "SESSION_ID",
                f"session:hook-{os.getpid()}",
            ),
            "timestamp": datetime.datetime.now(datetime.timezone.utc),
        })
    except Exception as e:  # noqa: BLE001
        print(f"check_constraint.py: audit log failed: {e}", file=sys.stderr)


def _allow() -> dict:
    return {"outcome": "allow"}


def _deny(reason: str) -> dict:
    return {"outcome": "deny", "reason": reason[:500]}


def _ask(reason: str) -> dict:
    return {"outcome": "ask", "reason": reason[:500]}


def _outcome_for(severity: str, reason: str) -> dict:
    if severity == "fail":
        return _deny(reason)
    if severity == "warn":
        return _ask(reason)
    return _allow()


# ── per-constraint evaluators ──────────────────────────────────────────────
# Each evaluator receives (tool_name, tool_input_dict, payload_dict) and
# returns True if the constraint MATCHES (i.e., the call should be denied).

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
    # Match `main`, `master`, or `origin main` / `origin master` token patterns.
    return bool(re.search(r"\b(main|master)\b", cmd))


def _match_destructive_rm(tool_name: str, tool_input: dict, payload: dict) -> bool:
    if tool_name != "Bash":
        return False
    cmd = tool_input.get("command", "")
    # rm -rf or rm -fr with a path that's not narrowly scoped.
    if not re.search(r"\brm\s+-[rf]+\b", cmd):
        return False
    # Allow scoped paths under /tmp or relative ./build, ./dist, ./.cache, etc.
    safe_targets = re.compile(r"\brm\s+-[rf]+\s+(\./)?(build|dist|\.cache|node_modules|/tmp/)")
    return not safe_targets.search(cmd)


def _match_generated_url(tool_name: str, tool_input: dict, payload: dict) -> bool:
    # WebFetch URL provenance can't be verified from the hook payload alone
    # (we'd need conversation history). This evaluator is intentionally
    # advisory: always return False (allow) but log to stderr so emit_hooks
    # consumers can decide whether to wire this up at all.
    return False


def _match_delegate_understanding(tool_name: str, tool_input: dict, payload: dict) -> bool:
    # SubagentStart payload includes the subagent prompt.
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
    # Cross-subagent dependency check requires batch context the hook payload
    # doesn't include. Fail-open; surface as advisory only.
    return False


def _match_fast_mode_opus(tool_name: str, tool_input: dict, payload: dict) -> bool:
    # UserPromptExpansion payload: command_name, expansion_type
    if payload.get("command_name") != "/fast":
        return False
    model = os.environ.get("CLAUDE_MODEL", "")
    return model and "opus-4-7" not in model


EVALUATORS = {
    "constraint:claude-code:bash:no-verify-bypass": _match_no_verify,
    "constraint:claude-code:bash:no-force-push-main": _match_force_push_main,
    "constraint:claude-code:bash:destructive-requires-confirm": _match_destructive_rm,
    "constraint:claude-code:web:no-generated-urls": _match_generated_url,
    "constraint:claude-code:agent:no-delegate-understanding": _match_delegate_understanding,
    "constraint:claude-code:agent:parallel-independent-only": _match_parallel_independent,
    "constraint:claude-code:model:fast-mode-opus-only": _match_fast_mode_opus,
}


def _evaluate_inline(constraint: dict, tool_name: str,
                     tool_input: dict, payload: dict) -> bool:
    """Generic JSON-driven evaluator for local constraints that ship their
    evaluation rule inline on the constraint document under
    .hook_config.evaluator. Avoids requiring a Python EVALUATORS entry for
    every local constraint — the user can express common patterns in JSON.

    Supported evaluator types:

      regex_in_field:
        {type:"regex_in_field", field:"command", pattern:"..."}
        Pulls tool_input[field], runs re.search(pattern, value, IGNORECASE).
        Returns True if it matches.

      always_match:
        {type:"always_match"}
        Trusts the settings.json `if` matcher entirely — denies on every
        invocation that reaches this evaluator. Use sparingly; the
        Claude Code matcher syntax is glob-y, not regex.

      never_match:
        {type:"never_match"}
        Audit-only — logs invocations to db.runs but always returns
        outcome:allow. Useful for observing tool patterns before deciding
        whether to enforce.
    """
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
        print(json.dumps(_allow()))
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    # Connect to MongoDB and look up the rule_text for the explanation.
    mongo_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        constraint = client[db_name].constraints.find_one({"_id": constraint_id})
    except Exception as e:
        # Fail-open: the hook should not block calls when the graph is down.
        print(json.dumps(_allow()))
        print(f"check_constraint.py: mongo unavailable ({e}); failing open",
              file=sys.stderr)
        return 0

    if not constraint:
        print(json.dumps(_allow()))
        client.close()
        return 0

    # Local-overlay constraints (constraint:local:*) carry their evaluator
    # inline on the document — no Python entry needed in EVALUATORS.
    if constraint_id.startswith("constraint:local:"):
        matched = _evaluate_inline(constraint, tool_name, tool_input, payload)
    else:
        evaluator = EVALUATORS.get(constraint_id)
        if not evaluator:
            print(json.dumps(_allow()))
            client.close()
            return 0
        matched = evaluator(tool_name, tool_input, payload)
    if not matched:
        result = _allow()
        print(json.dumps(result))
        # Log even non-matches — they're useful for "constraint X never fires"
        # drift signals. But only if the env explicitly opts in (HOOK_AUDIT=1).
        if os.environ.get("HOOK_AUDIT") == "1":
            _log_run(client, db_name,
                     constraint_id=constraint_id, tool_name=tool_name,
                     outcome="allow", matched=False)
        client.close()
        return 0

    severity = constraint.get("severity", "warn")
    reason = constraint.get("rule_text", "Constraint matched.")
    result = _outcome_for(severity, reason)
    print(json.dumps(result))
    # Always log matches — these are the audit trail of what the runtime
    # blocked or surfaced. Cheap (one write to an existing connection).
    _log_run(client, db_name,
             constraint_id=constraint_id, tool_name=tool_name,
             outcome=result["outcome"], matched=True)
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
