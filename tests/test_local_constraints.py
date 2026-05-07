"""Local-overlay constraints + audit logging tests.

Exit-code semantics: 0 = allow, 2 = block (stderr is the reason).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "check_constraint.py"


@pytest.fixture
def db(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    yield client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    client.close()


@pytest.fixture
def insert_local_constraint(db):
    inserted_ids = []
    def _insert(doc):
        db.constraints.insert_one(doc)
        inserted_ids.append(doc["_id"])
        return doc
    yield _insert
    if inserted_ids:
        db.constraints.delete_many({"_id": {"$in": inserted_ids}})


def _run_hook(constraint_id: str, payload: dict, *, audit: bool = False,
              session_id: str = None) -> tuple[int, str]:
    """Returns (returncode, stderr)."""
    env = {
        **os.environ,
        "CONSTRAINT_ID": constraint_id,
        "MONGODB_URI": os.environ["MONGODB_URI"],
        "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
    }
    if audit:
        env["HOOK_AUDIT"] = "1"
    if session_id:
        env["SESSION_ID"] = session_id
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stderr


# ── inline evaluator types ──────────────────────────────────────────────────

def test_local_regex_in_field_blocks_on_match(seeded, insert_local_constraint):
    insert_local_constraint({
        "_id": "constraint:local:test:no-deploy-prod",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Never deploy to production without explicit approval.",
        "violation_paraphrase": "deploy command targeting production",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "safety",
        "hook_config": {
            "event": "PreToolUse",
            "matcher": "Bash",
            "evaluator": {"type": "regex_in_field", "field": "command",
                          "pattern": r"deploy.*prod"},
        },
    })
    rc, stderr = _run_hook(
        "constraint:local:test:no-deploy-prod",
        {"tool_name": "Bash", "tool_input": {"command": "./deploy.sh prod"}},
    )
    assert rc == 2
    assert "production" in stderr


def test_local_regex_in_field_allows_no_match(seeded, insert_local_constraint):
    insert_local_constraint({
        "_id": "constraint:local:test:no-deploy-prod-2",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Never deploy to production.",
        "violation_paraphrase": "deploy command",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "safety",
        "hook_config": {
            "event": "PreToolUse",
            "matcher": "Bash",
            "evaluator": {"type": "regex_in_field", "field": "command",
                          "pattern": r"deploy.*prod"},
        },
    })
    rc, _ = _run_hook(
        "constraint:local:test:no-deploy-prod-2",
        {"tool_name": "Bash", "tool_input": {"command": "./deploy.sh staging"}},
    )
    assert rc == 0


def test_local_always_match_blocks(seeded, insert_local_constraint):
    insert_local_constraint({
        "_id": "constraint:local:test:always",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Test rule that always blocks.",
        "violation_paraphrase": "anything",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "test",
        "hook_config": {
            "event": "PreToolUse",
            "matcher": "Bash",
            "evaluator": {"type": "always_match"},
        },
    })
    rc, _ = _run_hook(
        "constraint:local:test:always",
        {"tool_name": "Bash", "tool_input": {"command": "echo foo"}},
    )
    assert rc == 2


def test_local_never_match_audit_only(seeded, insert_local_constraint):
    insert_local_constraint({
        "_id": "constraint:local:test:never",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Audit-only.",
        "violation_paraphrase": "anything",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "warn",
        "category": "test",
        "hook_config": {
            "event": "PreToolUse",
            "matcher": "Bash",
            "evaluator": {"type": "never_match"},
        },
    })
    rc, _ = _run_hook(
        "constraint:local:test:never",
        {"tool_name": "Bash", "tool_input": {"command": "echo anything"}},
    )
    assert rc == 0


# ── seed namespace guard ────────────────────────────────────────────────────

def test_seed_rejects_canonical_namespace_local_constraint(tmp_path, monkeypatch):
    import importlib
    import scripts.seed as seed_mod
    importlib.reload(seed_mod)

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    (local_dir / "constraints.json").write_text(json.dumps({
        "constraints": [{
            "_id": "constraint:claude-code:fake",  # wrong namespace
            "skill_id": "skill:foo",
            "rule_text": "x",
            "violation_paraphrase": "y",
            "examples": {"violating": "v", "compliant": "c"},
            "constraint_embedding": None,
            "severity": "warn",
            "category": "test",
        }],
    }))
    monkeypatch.setattr(seed_mod, "LOCAL_DIR", local_dir)

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    from io import StringIO
    import contextlib
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        seed_mod._load_local_overlay(db)
    out = buf.getvalue()
    client.close()
    assert "REJECTED" in out
    assert "constraint:local:" in out


# ── emit_hooks emits local constraints ──────────────────────────────────────

def test_emit_hooks_emits_local_constraint(seeded, insert_local_constraint):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import emit_hooks as eh

    insert_local_constraint({
        "_id": "constraint:local:test:emitted",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Test rule.",
        "violation_paraphrase": "x",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "test",
        "hook_config": {
            "event": "PreToolUse",
            "matcher": "Bash",
            "evaluator": {"type": "always_match"},
        },
    })

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = eh.emit_hooks(db)
    client.close()

    # The local constraint should appear as a command under PreToolUse > Bash
    bash_entries = [e for e in fragment["hooks"]["PreToolUse"]
                    if e["matcher"] == "Bash"]
    assert len(bash_entries) == 1
    bash_commands = bash_entries[0]["hooks"]
    assert any(
        "constraint:local:test:emitted" in h["command"]
        for h in bash_commands
    )
    assert fragment["_meta"]["local_count"] >= 1


def test_emit_hooks_skips_local_with_incomplete_hook_config(seeded, insert_local_constraint):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import emit_hooks as eh

    insert_local_constraint({
        "_id": "constraint:local:test:incomplete",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Incomplete.",
        "violation_paraphrase": "x",
        "examples": {"violating": "v", "compliant": "c"},
        "constraint_embedding": None,
        "severity": "warn",
        "category": "test",
        "hook_config": {"evaluator": {"type": "always_match"}},  # missing event/matcher
    })

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = eh.emit_hooks(db)
    client.close()
    skipped_ids = {sid for sid, _ in fragment["_meta"]["skipped"]}
    assert "constraint:local:test:incomplete" in skipped_ids


# ── audit logging ───────────────────────────────────────────────────────────

def test_hook_logs_block_to_db_runs(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    sid = "session:audit-test-block"
    db.runs.delete_many({"session_id": sid})

    rc, _ = _run_hook(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify"}},
        session_id=sid,
    )
    assert rc == 2

    rows = list(db.runs.find({"session_id": sid}))
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "hook:check_constraint"
    assert row["params"]["constraint_id"] == "constraint:claude-code:bash:no-verify-bypass"
    assert row["outcome"] == "deny"
    assert row["matched"] is True
    db.runs.delete_many({"session_id": sid})
    client.close()


def test_hook_does_not_log_non_match_by_default(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    sid = "session:audit-test-nomatch"
    db.runs.delete_many({"session_id": sid})

    rc, _ = _run_hook(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        session_id=sid,
    )
    assert rc == 0
    rows = list(db.runs.find({"session_id": sid}))
    assert len(rows) == 0
    client.close()


def test_hook_logs_non_match_when_audit_opted_in(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    sid = "session:audit-test-opt-in"
    db.runs.delete_many({"session_id": sid})

    rc, _ = _run_hook(
        "constraint:claude-code:bash:no-verify-bypass",
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        session_id=sid, audit=True,
    )
    assert rc == 0
    rows = list(db.runs.find({"session_id": sid}))
    assert len(rows) == 1
    assert rows[0]["matched"] is False
    db.runs.delete_many({"session_id": sid})
    client.close()
