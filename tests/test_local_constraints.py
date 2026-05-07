"""Local-overlay constraints (v2.11.x): users can ship personal-only
constraints in $SKILL_GRAPH_LOCAL_DIR/constraints.json without forking
the canonical seed. The runtime hook evaluates them via JSON-described
evaluators (no Python code shipped with the local data).

Covers:
- Namespace guard: _id must start with `constraint:local:`
- Skill_id flexibility: local constraints may target canonical skills
  (lets users add personal rules to public skills)
- Inline evaluator types: regex_in_field, always_match, never_match
- emit_hooks emission for local constraints
- Runtime evaluation of inline evaluators end-to-end
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
    """Insert a local constraint, yield, clean up. Bypasses the seed.py
    overlay loader (which reads from disk) — these tests insert directly."""
    inserted_ids = []

    def _insert(doc: dict) -> dict:
        db.constraints.insert_one(doc)
        inserted_ids.append(doc["_id"])
        return doc

    yield _insert

    if inserted_ids:
        db.constraints.delete_many({"_id": {"$in": inserted_ids}})


def _run_hook(constraint_id: str, payload: dict, *, audit: bool = False) -> tuple[dict, str]:
    env = {
        **os.environ,
        "CONSTRAINT_ID": constraint_id,
        "MONGODB_URI": os.environ["MONGODB_URI"],
        "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
    }
    if audit:
        env["HOOK_AUDIT"] = "1"
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


# ── inline evaluator types ──────────────────────────────────────────────────

def test_local_constraint_regex_in_field_matches(seeded, insert_local_constraint):
    """A local constraint with a regex_in_field evaluator denies when the
    pattern matches the named field of tool_input."""
    insert_local_constraint({
        "_id": "constraint:local:test:no-deploy-prod",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Never deploy to production without explicit approval.",
        "violation_paraphrase": "deploy command targeting production environment",
        "examples": {"violating": "kubectl apply -n prod ...", "compliant": "..."},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "safety",
        "hook_config": {
            "event": "PreToolUse",
            "if": "Bash(*deploy*prod*)",
            "evaluator": {
                "type": "regex_in_field",
                "field": "command",
                "pattern": r"deploy.*prod",
            },
        },
    })
    out, _ = _run_hook(
        "constraint:local:test:no-deploy-prod",
        {"tool_name": "Bash", "tool_input": {"command": "./deploy.sh prod"}},
    )
    assert out["outcome"] == "deny"
    assert "production" in out["reason"]


def test_local_constraint_regex_in_field_does_not_match(seeded, insert_local_constraint):
    insert_local_constraint({
        "_id": "constraint:local:test:no-deploy-prod-2",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Never deploy to production without approval.",
        "violation_paraphrase": "deploy command",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "safety",
        "hook_config": {
            "event": "PreToolUse",
            "if": "Bash(*deploy*)",
            "evaluator": {
                "type": "regex_in_field",
                "field": "command",
                "pattern": r"deploy.*prod",
            },
        },
    })
    # `deploy.sh staging` doesn't match the pattern
    out, _ = _run_hook(
        "constraint:local:test:no-deploy-prod-2",
        {"tool_name": "Bash", "tool_input": {"command": "./deploy.sh staging"}},
    )
    assert out["outcome"] == "allow"


def test_local_constraint_always_match_denies(seeded, insert_local_constraint):
    """always_match: trust the matcher entirely; deny every reaching call."""
    insert_local_constraint({
        "_id": "constraint:local:test:always",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Test rule that always denies once it matches.",
        "violation_paraphrase": "anything reaching this evaluator",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "test",
        "hook_config": {
            "event": "PreToolUse",
            "if": "Bash(*foo*)",
            "evaluator": {"type": "always_match"},
        },
    })
    out, _ = _run_hook(
        "constraint:local:test:always",
        {"tool_name": "Bash", "tool_input": {"command": "echo foo"}},
    )
    assert out["outcome"] == "deny"


def test_local_constraint_never_match_audit_only(seeded, insert_local_constraint):
    """never_match: audit-only. Always allows, but the runtime can still
    log to db.runs for "what would have triggered" analysis."""
    insert_local_constraint({
        "_id": "constraint:local:test:never",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Audit-only rule.",
        "violation_paraphrase": "anything",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "warn",
        "category": "test",
        "hook_config": {
            "event": "PreToolUse",
            "if": "Bash(*)",
            "evaluator": {"type": "never_match"},
        },
    })
    out, _ = _run_hook(
        "constraint:local:test:never",
        {"tool_name": "Bash", "tool_input": {"command": "echo anything"}},
    )
    assert out["outcome"] == "allow"


# ── seed.py overlay loader guards ───────────────────────────────────────────

def test_seed_rejects_local_constraint_with_canonical_id(tmp_path, monkeypatch):
    """Local constraints must use constraint:local:* namespace. A doc with
    constraint:claude-code:* in a local file is rejected by the guard."""
    import importlib

    import scripts.seed as seed_mod
    importlib.reload(seed_mod)

    # Build a fake local dir with one canonical-namespace doc
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    (local_dir / "constraints.json").write_text(json.dumps({
        "constraints": [{
            "_id": "constraint:claude-code:fake",  # ← wrong namespace
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
    # Capture stdout to verify the rejection message
    from io import StringIO
    import contextlib
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        seed_mod._load_local_overlay(db)
    out = buf.getvalue()
    client.close()

    assert "REJECTED" in out
    assert "constraint:local:" in out


# ── emit_hooks picks up local constraints ───────────────────────────────────

def test_emit_hooks_emits_local_constraints(seeded, insert_local_constraint):
    """emit_hooks must emit a hook for any local constraint with hook_config
    set (in addition to the canonical claude-code constraints)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import emit_hooks as eh

    insert_local_constraint({
        "_id": "constraint:local:test:emitted",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Test rule that should be emitted.",
        "violation_paraphrase": "test pattern",
        "examples": {"violating": "x", "compliant": "y"},
        "constraint_embedding": None,
        "severity": "fail",
        "category": "test",
        "hook_config": {
            "event": "PreToolUse",
            "if": "Bash(*localtest*)",
            "evaluator": {"type": "always_match"},
        },
    })

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = eh.emit_hooks(db)
    client.close()

    emitted_ids = {h["env"]["CONSTRAINT_ID"] for h in fragment["hooks"]}
    assert "constraint:local:test:emitted" in emitted_ids
    # Severity:fail → outcome:deny
    local_hook = next(
        h for h in fragment["hooks"]
        if h["env"]["CONSTRAINT_ID"] == "constraint:local:test:emitted"
    )
    assert local_hook["outcome"] == "deny"
    assert local_hook["if"] == "Bash(*localtest*)"
    # _meta.local_count reflects local constraint emission
    assert fragment["_meta"]["local_count"] >= 1


def test_emit_hooks_skips_local_with_incomplete_hook_config(seeded, insert_local_constraint):
    """A local constraint with hook_config set but missing event/if must
    appear in _meta.skipped, not silently emit a broken hook."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import emit_hooks as eh

    insert_local_constraint({
        "_id": "constraint:local:test:incomplete",
        "skill_id": "skill:claude-code:tool:bash",
        "rule_text": "Incomplete hook config.",
        "violation_paraphrase": "x",
        "examples": {"violating": "v", "compliant": "c"},
        "constraint_embedding": None,
        "severity": "warn",
        "category": "test",
        "hook_config": {"evaluator": {"type": "always_match"}},  # missing event, if
    })

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = eh.emit_hooks(db)
    client.close()

    skipped_ids = {sid for sid, _ in fragment["_meta"]["skipped"]}
    assert "constraint:local:test:incomplete" in skipped_ids


# ── audit logging to db.runs ────────────────────────────────────────────────

def test_hook_logs_match_to_db_runs(seeded):
    """When a constraint matches, the hook writes a row to db.runs with
    the constraint_id, outcome, and session_id from $SESSION_ID env."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    sid = "session:audit-test-match"
    db.runs.delete_many({"session_id": sid})

    env = {
        **os.environ,
        "CONSTRAINT_ID": "constraint:claude-code:bash:no-verify-bypass",
        "MONGODB_URI": os.environ["MONGODB_URI"],
        "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
        "SESSION_ID": sid,
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": "git commit --no-verify"}}),
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["outcome"] == "deny"

    rows = list(db.runs.find({"session_id": sid}))
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "hook:check_constraint"
    assert row["params"]["constraint_id"] == "constraint:claude-code:bash:no-verify-bypass"
    assert row["params"]["tool_name"] == "Bash"
    assert row["outcome"] == "deny"
    assert row["matched"] is True
    db.runs.delete_many({"session_id": sid})
    client.close()


def test_hook_does_not_log_non_match_by_default(seeded):
    """Default behavior: don't flood db.runs with allow rows. Only matched
    constraints are logged. HOOK_AUDIT=1 opts into logging non-matches."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    sid = "session:audit-test-nomatch"
    db.runs.delete_many({"session_id": sid})

    env = {
        **os.environ,
        "CONSTRAINT_ID": "constraint:claude-code:bash:no-verify-bypass",
        "MONGODB_URI": os.environ["MONGODB_URI"],
        "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
        "SESSION_ID": sid,
    }
    # Benign command — no match
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert proc.returncode == 0

    rows = list(db.runs.find({"session_id": sid}))
    assert len(rows) == 0  # no audit row for clean call
    client.close()


def test_hook_logs_non_match_when_audit_opted_in(seeded):
    """HOOK_AUDIT=1 → log every invocation for drift analysis."""
    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    sid = "session:audit-test-opt-in"
    db.runs.delete_many({"session_id": sid})

    env = {
        **os.environ,
        "CONSTRAINT_ID": "constraint:claude-code:bash:no-verify-bypass",
        "MONGODB_URI": os.environ["MONGODB_URI"],
        "SKILL_GRAPH_DB": os.environ.get("SKILL_GRAPH_DB", "skill_graph_test"),
        "SESSION_ID": sid,
        "HOOK_AUDIT": "1",
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert proc.returncode == 0

    rows = list(db.runs.find({"session_id": sid}))
    assert len(rows) == 1
    assert rows[0]["matched"] is False
    assert rows[0]["outcome"] == "allow"
    db.runs.delete_many({"session_id": sid})
    client.close()
