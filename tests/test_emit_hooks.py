"""emit_hooks.py + runtime hook script (scripts/hooks/check_constraint.py).

The graph emits Claude Code settings.json hook entries derived from
db.constraints. Runtime hook evaluates the constraint and returns
deny/ask/allow with rule_text as the reason. Deny-only enforcement —
hooks never modify tool input.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture
def emit_hooks_module():
    import emit_hooks  # noqa: WPS433 — local import for test
    return emit_hooks


def test_emit_hooks_emits_one_per_known_constraint(seeded, emit_hooks_module):
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()

    assert "hooks" in fragment
    assert "_meta" in fragment
    # Every entry in MATCHERS that has a corresponding seeded constraint
    # should produce one hook entry.
    expected_ids = set(emit_hooks_module.MATCHERS.keys())
    emitted_ids = {h["env"]["CONSTRAINT_ID"] for h in fragment["hooks"]}
    assert emitted_ids == expected_ids


def test_emit_hooks_severity_to_outcome_mapping(seeded, emit_hooks_module):
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()

    by_id = {h["env"]["CONSTRAINT_ID"]: h for h in fragment["hooks"]}
    # fail-severity → deny
    assert by_id["constraint:claude-code:bash:no-verify-bypass"]["outcome"] == "deny"
    assert by_id["constraint:claude-code:bash:no-force-push-main"]["outcome"] == "deny"
    # warn-severity → ask
    assert by_id["constraint:claude-code:bash:destructive-requires-confirm"]["outcome"] == "ask"
    assert by_id["constraint:claude-code:agent:no-delegate-understanding"]["outcome"] == "ask"


def test_emit_hooks_skips_unknown_constraints(seeded, emit_hooks_module):
    """Constraints without a MATCHERS entry must NOT silently emit a hook."""
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()

    # Each emitted hook's CONSTRAINT_ID must appear in MATCHERS.
    for h in fragment["hooks"]:
        assert h["env"]["CONSTRAINT_ID"] in emit_hooks_module.MATCHERS


def test_emitted_plus_skipped_equals_total_claude_code_constraints(seeded, emit_hooks_module):
    """The script must account for every claude-code constraint — either
    by emitting a hook or by listing it in _meta.skipped. Catches the case
    where emit_hooks silently loses a constraint."""
    from pymongo import MongoClient
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    total = db.constraints.count_documents(
        {"skill_id": {"$regex": "^skill:claude-code:"}}
    )
    client.close()

    emitted = len(fragment["hooks"])
    skipped = len(fragment["_meta"]["skipped"])
    assert emitted + skipped == total, (
        f"claude-code constraints unaccounted for: total={total}, "
        f"emitted={emitted}, skipped={skipped}"
    )


# ── runtime hook script (scripts/hooks/check_constraint.py) ────────────────

HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "check_constraint.py"


def _run_hook(constraint_id: str, payload: dict) -> dict:
    env = {
        **os.environ,
        "CONSTRAINT_ID": constraint_id,
        "MONGODB_URI": os.environ["MONGODB_URI"],
        "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, f"hook crashed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_hook_no_verify_denies(seeded):
    out = _run_hook(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m fix"}},
    )
    assert out["outcome"] == "deny"
    assert "no-verify" in out["reason"]


def test_hook_no_verify_allows_clean_commit(seeded):
    out = _run_hook(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m fix"}},
    )
    assert out["outcome"] == "allow"


def test_hook_force_push_main_denies(seeded):
    out = _run_hook(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}},
    )
    assert out["outcome"] == "deny"


def test_hook_force_push_feature_allows(seeded):
    out = _run_hook(
        "constraint:claude-code:bash:no-force-push-main",
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin feature-x"}},
    )
    assert out["outcome"] == "allow"


def test_hook_destructive_rm_scoped_allows(seeded):
    out = _run_hook(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf ./dist"}},
    )
    assert out["outcome"] == "allow"


def test_hook_destructive_rm_unscoped_asks(seeded):
    out = _run_hook(
        "constraint:claude-code:bash:destructive-requires-confirm",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /etc/foo"}},
    )
    # severity=warn → ask outcome (surface to user)
    assert out["outcome"] == "ask"


def test_hook_delegated_subagent_prompt_asks(seeded):
    out = _run_hook(
        "constraint:claude-code:agent:no-delegate-understanding",
        {
            "tool_name": "Agent",
            "tool_input": {
                "prompt": "Explore the auth module then based on your findings fix the bug",
            },
        },
    )
    assert out["outcome"] == "ask"
    assert "delegate" in out["reason"].lower() or "synthesis" in out["reason"].lower()


def test_hook_concrete_subagent_prompt_allows(seeded):
    out = _run_hook(
        "constraint:claude-code:agent:no-delegate-understanding",
        {
            "tool_name": "Agent",
            "tool_input": {
                "prompt": "In auth/middleware.py:47 change > to >= and add a test",
            },
        },
    )
    assert out["outcome"] == "allow"


def test_hook_unknown_constraint_fails_open(seeded):
    out = _run_hook(
        "constraint:does-not-exist",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
    )
    assert out["outcome"] == "allow"


def test_emit_then_evaluate_round_trip(seeded, emit_hooks_module, tmp_path):
    """End-to-end smoke: emit_hooks → write → reload → run check_constraint.py
    against every emitted hook with a benign payload → confirm none crash and
    each returns a valid outcome string. Sanity check that the emitted config
    references no broken hook script paths."""
    import os as _os
    from pymongo import MongoClient

    client = MongoClient(_os.environ["MONGODB_URI"])
    db = client[_os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks_module.emit_hooks(db)
    client.close()

    out_path = tmp_path / "hooks.json"
    out_path.write_text(json.dumps(fragment))
    reloaded = json.loads(out_path.read_text())
    assert reloaded["hooks"] == fragment["hooks"]

    benign_payload = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}
    for h in reloaded["hooks"]:
        constraint_id = h["env"]["CONSTRAINT_ID"]
        proc = subprocess.run(
            [sys.executable, h["tool"]],
            input=json.dumps(benign_payload),
            capture_output=True,
            text=True,
            env={**_os.environ,
                 "CONSTRAINT_ID": constraint_id,
                 "MONGODB_URI": _os.environ["MONGODB_URI"],
                 "SKILL_GRAPH_DB": _os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")},
            timeout=10,
        )
        assert proc.returncode == 0, (
            f"hook crashed for {constraint_id}: {proc.stderr}"
        )
        out = json.loads(proc.stdout)
        assert out["outcome"] in {"allow", "ask", "deny"}, (
            f"unexpected outcome from {constraint_id}: {out}"
        )


def test_hook_no_input_fails_open(seeded):
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
    out = json.loads(proc.stdout)
    assert out["outcome"] == "allow"
