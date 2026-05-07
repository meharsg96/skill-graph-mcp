"""emit_hooks.py + runtime hook script (scripts/hooks/check_constraint.py).

Schema matches https://code.claude.com/docs/en/hooks:
- hooks is a record keyed by event name
- each event maps to an array of {matcher, hooks} entries
- hooks entries have type "command" and a shell command string
- runtime exit code: 0=allow, 2=block (stderr is the reason)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "check_constraint.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture
def emit_hooks_module():
    import emit_hooks
    return emit_hooks


# ── emit_hooks output schema ────────────────────────────────────────────────

def test_emit_hooks_top_level_hooks_is_record(seeded, emit_hooks_module):
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()
    assert isinstance(fragment["hooks"], dict), (
        "Claude Code requires hooks to be a record keyed by event name, not an array"
    )


def test_emit_hooks_event_keys_are_real_event_names(seeded, emit_hooks_module):
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()
    valid_events = {
        "SessionStart", "Setup", "UserPromptSubmit", "UserPromptExpansion",
        "PreToolUse", "PermissionRequest", "PermissionDenied", "PostToolUse",
        "PostToolUseFailure", "PostToolBatch", "Notification", "SubagentStart",
        "SubagentStop", "TaskCreated", "TaskCompleted", "Stop", "StopFailure",
        "TeammateIdle", "InstructionsLoaded", "ConfigChange", "CwdChanged",
        "FileChanged", "WorktreeCreate", "WorktreeRemove", "PreCompact",
        "PostCompact", "Elicitation", "ElicitationResult", "SessionEnd",
    }
    for event in fragment["hooks"]:
        assert event in valid_events, f"emitted hook for unknown event: {event}"


def test_emit_hooks_matcher_entries_have_correct_shape(seeded, emit_hooks_module):
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()
    for event, entries in fragment["hooks"].items():
        assert isinstance(entries, list), f"{event} should be an array"
        for entry in entries:
            assert "matcher" in entry, f"{event} entry missing matcher"
            assert "hooks" in entry, f"{event} entry missing hooks array"
            assert isinstance(entry["hooks"], list)
            for h in entry["hooks"]:
                assert h.get("type") == "command", (
                    f"only 'command' hook type is supported, got {h.get('type')}"
                )
                assert "command" in h


def test_emit_hooks_collapses_same_matcher(seeded, emit_hooks_module):
    """Three Bash constraints should produce ONE matcher entry with three
    hooks inside it, not three separate matcher entries."""
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()
    pretool = fragment["hooks"].get("PreToolUse", [])
    bash_entries = [e for e in pretool if e["matcher"] == "Bash"]
    assert len(bash_entries) == 1, (
        f"expected one Bash matcher entry, got {len(bash_entries)}"
    )
    assert len(bash_entries[0]["hooks"]) == 3  # no-verify, force-push, destructive-rm


def test_emit_hooks_command_carries_constraint_id_env(seeded, emit_hooks_module):
    """Each emitted command must include CONSTRAINT_ID=constraint:... so
    the runtime hook knows which constraint it's evaluating, AND so
    merge_settings can identify graph-owned commands later."""
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()
    for entries in fragment["hooks"].values():
        for entry in entries:
            for h in entry["hooks"]:
                assert "CONSTRAINT_ID=constraint:" in h["command"]
                assert "MONGODB_URI=" in h["command"]


def test_emit_hooks_skips_constraints_without_matcher_entry(seeded, emit_hooks_module):
    """fast-mode-opus-only has no MATCHERS entry — must appear in skipped."""
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()
    skipped_ids = {sid for sid, _ in fragment["_meta"]["skipped"]}
    assert "constraint:claude-code:model:fast-mode-opus-only" in skipped_ids


def test_emit_hooks_emitted_plus_skipped_equals_claude_code_total(seeded, emit_hooks_module):
    """No claude-code constraint is silently dropped."""
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    total = db.constraints.count_documents(
        {"skill_id": {"$regex": "^skill:claude-code:"}}
    )
    client.close()
    emitted = fragment["_meta"]["constraint_count"]
    skipped = len(fragment["_meta"]["skipped"])
    assert emitted + skipped == total


# ── runtime hook (exit-code semantics) ──────────────────────────────────────

def _run_hook(constraint_id: str, payload: dict, *, extra_env: dict = None):
    """Run check_constraint.py; return (returncode, stderr_text)."""
    env = {
        **os.environ,
        "CONSTRAINT_ID": constraint_id,
        "MONGODB_URI": os.environ["MONGODB_URI"],
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


def test_hook_no_verify_blocks_with_exit_2(seeded):
    rc, stderr = _run_hook(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m fix"}},
    )
    assert rc == 2
    assert "no-verify" in stderr


def test_hook_no_verify_allows_clean_commit(seeded):
    rc, _ = _run_hook(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m fix"}},
    )
    assert rc == 0


def test_hook_force_push_main_blocks(seeded):
    rc, _ = _run_hook(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}},
    )
    assert rc == 2


def test_hook_force_push_feature_allows(seeded):
    rc, _ = _run_hook(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin feature-x"}},
    )
    assert rc == 0


def test_hook_destructive_rm_scoped_allows(seeded):
    rc, _ = _run_hook(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf ./dist"}},
    )
    assert rc == 0


def test_hook_destructive_rm_unscoped_blocks_with_warn_prefix(seeded):
    """severity:warn → exit 2 (block) with WARN: prefix on stderr."""
    rc, stderr = _run_hook(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /etc/foo"}},
    )
    assert rc == 2
    assert "WARN:" in stderr


def test_hook_delegated_subagent_blocks_with_warn_prefix(seeded):
    rc, stderr = _run_hook(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Task",
         "tool_input": {"prompt": "Explore the auth then based on your findings fix it"}},
    )
    assert rc == 2
    assert "WARN:" in stderr


def test_hook_concrete_subagent_prompt_allows(seeded):
    rc, _ = _run_hook(
        "constraint:claude-code:agent:no-delegate-understanding",
        {"tool_name": "Task",
         "tool_input": {"prompt": "In auth/middleware.py:47 change > to >="}},
    )
    assert rc == 0


def test_hook_unknown_constraint_fails_open(seeded):
    rc, _ = _run_hook(
        "constraint:does-not-exist",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
    )
    assert rc == 0


def test_hook_no_input_fails_open(seeded):
    """Empty stdin → outcome=allow (not crash)."""
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input="",
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


def test_emit_then_evaluate_round_trip(seeded, emit_hooks_module, tmp_path):
    """End-to-end smoke: emit → reload → invoke each command with a benign
    payload → confirm none crash and each returns exit 0 or 2."""
    import os as _os
    from pymongo import MongoClient

    client = MongoClient(_os.environ["MONGODB_URI"])
    db = client[_os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()

    out_path = tmp_path / "hooks.json"
    out_path.write_text(json.dumps(fragment))
    reloaded = json.loads(out_path.read_text())

    benign_payload = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}
    for entries in reloaded["hooks"].values():
        for entry in entries:
            for h in entry["hooks"]:
                # Run the command in a shell since it has env-var prefixes
                proc = subprocess.run(
                    h["command"],
                    input=json.dumps(benign_payload),
                    capture_output=True, text=True, shell=True, timeout=10,
                )
                assert proc.returncode in {0, 2}, (
                    f"unexpected exit {proc.returncode} for command:\n"
                    f"{h['command']}\nstderr: {proc.stderr}"
                )
