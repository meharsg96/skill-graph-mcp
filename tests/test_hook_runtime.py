"""Edge-case tests for scripts/hooks/check_constraint.py.

Exit-code semantics (per Claude Code hook contract):
  0 = allow
  2 = block (stderr is shown to the user)
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
         extra_env: dict = None) -> tuple[int, str]:
    """Returns (returncode, stderr_text)."""
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
    return proc.returncode, proc.stderr


# ── fail-open contract ──────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_hook_fails_open_when_mongodb_unreachable(seeded):
    bad_uri = f"mongodb://127.0.0.1:{_free_port()}/?serverSelectionTimeoutMS=2000"
    rc, stderr = _run(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify"}},
        mongo_uri=bad_uri,
    )
    assert rc == 0
    assert "mongo" in stderr.lower() or "fail" in stderr.lower()


def test_hook_fails_open_when_constraint_not_found(seeded):
    rc, _ = _run(
        "constraint:does-not-exist:fictional",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
    )
    assert rc == 0


def test_hook_fails_open_with_no_constraint_id_env():
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {}}),
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "CONSTRAINT_ID"},
        timeout=10,
    )
    assert proc.returncode == 0


def test_hook_fails_open_on_garbage_stdin(seeded):
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


# ── matcher / evaluator drift catching ─────────────────────────────────────

def test_every_matcher_has_corresponding_evaluator():
    """Every constraint_id in emit_hooks.MATCHERS must have an EVALUATORS
    entry in check_constraint.py."""
    import emit_hooks
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "hooks"))
    import check_constraint

    matcher_ids = set(emit_hooks.MATCHERS.keys())
    evaluator_ids = set(check_constraint.EVALUATORS.keys())
    missing_evaluators = matcher_ids - evaluator_ids
    assert not missing_evaluators, (
        f"MATCHERS reference constraint_ids without EVALUATORS: {missing_evaluators}"
    )


def test_every_matcher_has_seeded_constraint(seeded):
    import emit_hooks
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    seeded_ids = {c["_id"] for c in db.constraints.find({}, {"_id": 1})}
    client.close()

    matcher_ids = set(emit_hooks.MATCHERS.keys())
    orphan_matchers = matcher_ids - seeded_ids
    assert not orphan_matchers


def test_emitted_command_paths_exist(seeded, tmp_path):
    """Every command string the emitter produces must reference a script
    that exists. Otherwise Claude Code logs a confusing 'hook not found'."""
    import emit_hooks
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks.emit_hooks(db)
    client.close()

    for entries in fragment["hooks"].values():
        for entry in entries:
            for h in entry["hooks"]:
                # The command is shell-quoted; the script path is the last token
                # ending in .py
                cmd = h["command"]
                assert "check_constraint.py" in cmd
                # Extract the script path between the last quote pair before .py
                import re
                m = re.search(r"'(/[^']*check_constraint\.py)'", cmd)
                assert m, f"could not extract script path from: {cmd}"
                assert Path(m.group(1)).exists()


# ── bash matcher edge cases ─────────────────────────────────────────────────

def test_force_push_force_with_lease_to_main_blocks(seeded):
    rc, _ = _run(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash",
         "tool_input": {"command": "git push --force-with-lease origin main"}},
    )
    assert rc == 2


def test_force_push_to_master_blocks(seeded):
    rc, _ = _run(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin master"}},
    )
    assert rc == 2


def test_force_push_branch_named_mainline_does_not_match(seeded):
    rc, _ = _run(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin mainline"}},
    )
    assert rc == 0


def test_destructive_rm_under_safe_path_node_modules_allows(seeded):
    rc, _ = _run(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf node_modules"}},
    )
    assert rc == 0


def test_destructive_rm_under_dot_cache_allows(seeded):
    rc, _ = _run(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf ./.cache"}},
    )
    assert rc == 0


def test_destructive_rm_root_path_blocks(seeded):
    rc, _ = _run(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /usr/local/bin"}},
    )
    # severity:warn → exit 2 with WARN: prefix
    assert rc == 2


def test_destructive_rm_with_quoted_path(seeded):
    rc, _ = _run(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash",
         "tool_input": {"command": 'rm -rf "/Users/x/some dir/data"'}},
    )
    assert rc == 2


def test_no_verify_only_matches_bash(seeded):
    """Tool name dispatch — a WebFetch with '--no-verify' in URL allows."""
    rc, _ = _run(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "WebFetch",
         "tool_input": {"url": "https://example.com/path?--no-verify=1"}},
    )
    assert rc == 0


# ── subagent prompt edge cases ──────────────────────────────────────────────

def test_delegate_understanding_case_insensitive(seeded):
    rc, _ = _run(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Task",
         "tool_input": {"prompt": "BASED ON YOUR FINDINGS, fix the bug"}},
    )
    assert rc == 2


def test_delegate_understanding_with_no_prompt_field_allows(seeded):
    rc, _ = _run(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Task", "tool_input": {}},
    )
    assert rc == 0


def test_delegate_understanding_alternate_phrasing(seeded):
    rc, _ = _run(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Task",
         "tool_input": {"prompt": "After you investigate, implement the fix"}},
    )
    assert rc == 2


def test_delegate_understanding_works_for_agent_tool_name_too(seeded):
    """Subagent tool may be reported as either Task or Agent — both should match."""
    rc, _ = _run(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Agent",
         "tool_input": {"prompt": "Based on your findings, fix it"}},
    )
    assert rc == 2
