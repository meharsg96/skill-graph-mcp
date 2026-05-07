"""Edge-case tests for scripts/hooks/check_constraint.py.

Covers the failure-mode contract (always fail-open), drift between
emit_hooks MATCHERS and check_constraint EVALUATORS, and payload
shapes that aren't already covered by tests/test_emit_hooks.py.
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "check_constraint.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _run(constraint_id: str, payload: dict, *, mongo_uri: str = None,
         extra_env: dict = None) -> tuple[dict, str]:
    """Run check_constraint.py; return (parsed_outcome, stderr_text)."""
    env = {
        **os.environ,
        "CONSTRAINT_ID": constraint_id,
        "MONGODB_URI": mongo_uri or os.environ["MONGODB_URI"],
        "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
    }
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, f"hook crashed: {proc.stderr}"
    return json.loads(proc.stdout), proc.stderr


# ── fail-open under infrastructure failure ─────────────────────────────────

def _free_port() -> int:
    """Find an unused port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_hook_fails_open_when_mongodb_unreachable(seeded):
    """The hook must NEVER block tool calls because MongoDB is down.
    Returns outcome=allow with a stderr warning."""
    bad_uri = f"mongodb://127.0.0.1:{_free_port()}/?serverSelectionTimeoutMS=2000"
    out, stderr = _run(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify"}},
        mongo_uri=bad_uri,
    )
    assert out["outcome"] == "allow"
    assert "mongo" in stderr.lower() or "fail" in stderr.lower()


def test_hook_fails_open_when_constraint_not_found(seeded):
    out, _ = _run(
        "constraint:does-not-exist:fictional",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
    )
    assert out["outcome"] == "allow"


def test_hook_fails_open_with_no_constraint_id_env():
    """No CONSTRAINT_ID env var → outcome=allow."""
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {}}),
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "CONSTRAINT_ID"},
        timeout=10,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["outcome"] == "allow"


def test_hook_fails_open_on_garbage_stdin(seeded):
    """Malformed JSON payload → outcome=allow (not crash)."""
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input="this is not json {[",
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CONSTRAINT_ID": "constraint:claude-code:bash:no-verify-bypass",
            "MONGODB_URI": os.environ["MONGODB_URI"],
            "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
        },
        timeout=10,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["outcome"] == "allow"


# ── matcher / evaluator drift ───────────────────────────────────────────────

def test_every_matcher_has_corresponding_evaluator():
    """Every constraint_id in emit_hooks.MATCHERS must have an EVALUATORS
    entry in check_constraint.py — or be intentionally fail-open with a
    comment. Catches the case where someone adds a matcher and forgets the
    runtime evaluator."""
    import emit_hooks

    sys.path.insert(0, str(REPO_ROOT / "scripts" / "hooks"))
    import check_constraint

    matcher_ids = set(emit_hooks.MATCHERS.keys())
    evaluator_ids = set(check_constraint.EVALUATORS.keys())
    missing_evaluators = matcher_ids - evaluator_ids
    assert not missing_evaluators, (
        f"MATCHERS reference constraint_ids without EVALUATORS: {missing_evaluators}. "
        "Add an entry to EVALUATORS in scripts/hooks/check_constraint.py "
        "(it can be intentionally fail-open with a comment)."
    )


def test_every_matcher_has_seeded_constraint(seeded):
    """Every constraint_id in emit_hooks.MATCHERS must exist as a real
    document in db.constraints. Catches the case where a matcher
    references a constraint that was renamed or deleted."""
    import emit_hooks
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    seeded_ids = {c["_id"] for c in db.constraints.find({}, {"_id": 1})}
    client.close()

    matcher_ids = set(emit_hooks.MATCHERS.keys())
    orphan_matchers = matcher_ids - seeded_ids
    assert not orphan_matchers, (
        f"MATCHERS reference constraint_ids not in db.constraints: {orphan_matchers}"
    )


def test_emitted_hook_paths_exist(seeded, tmp_path):
    """The hook 'tool' path emitted into settings.json must point to
    a file that exists. Otherwise Claude Code logs a confusing
    'hook not found' at runtime."""
    import emit_hooks
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks.emit_hooks(db)
    client.close()

    for h in fragment["hooks"]:
        path = Path(h["tool"])
        assert path.exists(), f"hook script not found: {path}"
        assert path.is_file(), f"hook 'tool' is not a regular file: {path}"


# ── bash matcher edge cases ─────────────────────────────────────────────────

def test_force_push_force_with_lease_to_main_denies(seeded):
    """--force-with-lease should be treated as force-push for main."""
    out, _ = _run(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash",
         "tool_input": {"command": "git push --force-with-lease origin main"}},
    )
    assert out["outcome"] == "deny"


def test_force_push_to_master_denies(seeded):
    """master is treated identically to main."""
    out, _ = _run(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin master"}},
    )
    assert out["outcome"] == "deny"


def test_force_push_branch_named_mainline_does_not_match(seeded):
    """Word boundary: 'mainline' should not trigger the main matcher."""
    out, _ = _run(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin mainline"}},
    )
    assert out["outcome"] == "allow"


def test_destructive_rm_under_safe_path_node_modules_allows(seeded):
    out, _ = _run(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf node_modules"}},
    )
    assert out["outcome"] == "allow"


def test_destructive_rm_under_dot_cache_allows(seeded):
    out, _ = _run(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf ./.cache"}},
    )
    assert out["outcome"] == "allow"


def test_destructive_rm_root_path_asks(seeded):
    out, _ = _run(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /usr/local/bin"}},
    )
    assert out["outcome"] == "ask"


def test_no_verify_only_matches_bash(seeded):
    """The matcher entry is `Bash(*--no-verify*)`, but the evaluator also
    checks tool_name. A WebFetch with '--no-verify' in url should NOT match."""
    out, _ = _run(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "WebFetch",
         "tool_input": {"url": "https://example.com/path?--no-verify=1"}},
    )
    assert out["outcome"] == "allow"


# ── subagent prompt edge cases ──────────────────────────────────────────────

def test_delegate_understanding_case_insensitive(seeded):
    out, _ = _run(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Agent",
         "tool_input": {"prompt": "BASED ON YOUR FINDINGS, fix the bug"}},
    )
    assert out["outcome"] == "ask"


def test_delegate_understanding_with_no_prompt_field_allows(seeded):
    """SubagentStart payload without a prompt field → outcome=allow."""
    out, _ = _run(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Agent", "tool_input": {}},
    )
    assert out["outcome"] == "allow"


def test_delegate_understanding_alternate_phrasing_after_you_investigate(seeded):
    """The evaluator covers 4 phrase variants — verify one of the alternates."""
    out, _ = _run(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Agent",
         "tool_input": {"prompt": "After you investigate, implement the fix"}},
    )
    assert out["outcome"] == "ask"
