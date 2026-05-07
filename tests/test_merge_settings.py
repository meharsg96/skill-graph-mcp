"""scripts/merge_settings.py — idempotent merge of an emit_hooks fragment
into a Claude Code settings file.

Critical properties:
- Idempotent: running merge N times produces the same output
- User-authored hooks (no env.CONSTRAINT_ID) are preserved
- Graph-owned hooks (with env.CONSTRAINT_ID) are replaced wholesale
- Missing settings file is treated as `{}`
- Settings without a `hooks` key are treated as `hooks: []`
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture
def merge_module():
    import merge_settings  # noqa: WPS433 — local
    return merge_settings


# ── pure merge function ─────────────────────────────────────────────────────

def test_merge_into_empty_settings(merge_module):
    fragment = {
        "hooks": [
            {"event": "PreToolUse", "if": "Bash(*)", "tool": "/x.py",
             "env": {"CONSTRAINT_ID": "constraint:foo"}, "outcome": "deny"},
        ],
    }
    merged, summary = merge_module.merge({}, fragment)
    assert merged["hooks"] == fragment["hooks"]
    assert summary == {
        "user_hooks_preserved": 0,
        "graph_hooks_dropped": 0,
        "graph_hooks_added": 1,
    }


def test_merge_preserves_user_authored_hooks(merge_module):
    """A hook without env.CONSTRAINT_ID is user-authored — must survive."""
    user_hook = {
        "event": "Stop",
        "tool": "/users-own-script.sh",
        # no env.CONSTRAINT_ID — user-authored
    }
    settings = {"hooks": [user_hook]}
    fragment = {
        "hooks": [
            {"event": "PreToolUse", "if": "Bash(*)", "tool": "/x.py",
             "env": {"CONSTRAINT_ID": "constraint:foo"}, "outcome": "deny"},
        ],
    }
    merged, summary = merge_module.merge(settings, fragment)
    assert user_hook in merged["hooks"]
    assert summary["user_hooks_preserved"] == 1
    assert summary["graph_hooks_added"] == 1


def test_merge_replaces_graph_owned_hooks(merge_module):
    """Existing hooks with env.CONSTRAINT_ID must be dropped before adding
    the new fragment — otherwise running emit_hooks twice doubles every
    hook."""
    old_graph_hook = {
        "event": "PreToolUse", "if": "Bash(*--no-verify*)",
        "tool": "/x.py", "env": {"CONSTRAINT_ID": "constraint:foo"},
        "outcome": "deny",
    }
    new_graph_hook = {
        "event": "PreToolUse", "if": "Bash(*--no-verify*)",
        "tool": "/x.py", "env": {"CONSTRAINT_ID": "constraint:foo"},
        "outcome": "deny", "reason_preview": "updated text",
    }
    settings = {"hooks": [old_graph_hook]}
    fragment = {"hooks": [new_graph_hook]}
    merged, summary = merge_module.merge(settings, fragment)
    assert merged["hooks"] == [new_graph_hook]
    assert summary == {
        "user_hooks_preserved": 0,
        "graph_hooks_dropped": 1,
        "graph_hooks_added": 1,
    }


def test_merge_is_idempotent(merge_module):
    """Running merge N times must produce the same output as running once."""
    user_hook = {"event": "Stop", "tool": "/x.sh"}
    fragment = {
        "hooks": [
            {"event": "PreToolUse", "if": "Bash(*)", "tool": "/y.py",
             "env": {"CONSTRAINT_ID": "constraint:a"}, "outcome": "deny"},
            {"event": "SubagentStart", "if": "*", "tool": "/y.py",
             "env": {"CONSTRAINT_ID": "constraint:b"}, "outcome": "ask"},
        ],
    }
    settings = {"hooks": [user_hook]}
    merged1, _ = merge_module.merge(settings, fragment)
    merged2, _ = merge_module.merge(merged1, fragment)
    merged3, _ = merge_module.merge(merged2, fragment)
    assert merged1["hooks"] == merged2["hooks"] == merged3["hooks"]


def test_merge_preserves_non_hooks_keys(merge_module):
    """A real settings.json carries unrelated keys (model, permissionMode,
    etc.). Merge must not touch them."""
    settings = {
        "model": "claude-opus-4-7",
        "permissionMode": "ask",
        "hooks": [],
    }
    fragment = {"hooks": []}
    merged, _ = merge_module.merge(settings, fragment)
    assert merged["model"] == "claude-opus-4-7"
    assert merged["permissionMode"] == "ask"


def test_merge_treats_missing_hooks_key_as_empty(merge_module):
    settings = {"model": "claude-opus-4-7"}  # no hooks key at all
    fragment = {
        "hooks": [
            {"event": "PreToolUse", "if": "*", "tool": "/x.py",
             "env": {"CONSTRAINT_ID": "constraint:a"}, "outcome": "deny"},
        ],
    }
    merged, summary = merge_module.merge(settings, fragment)
    assert len(merged["hooks"]) == 1
    assert summary["user_hooks_preserved"] == 0


def test_is_graph_owned_recognizes_env_constraint_id(merge_module):
    assert merge_module.is_graph_owned({"env": {"CONSTRAINT_ID": "constraint:foo"}})
    assert not merge_module.is_graph_owned({"env": {"CONSTRAINT_ID": ""}})
    assert not merge_module.is_graph_owned({"env": {}})
    assert not merge_module.is_graph_owned({})  # no env key
    assert not merge_module.is_graph_owned({"env": None})


# ── CLI / file IO ───────────────────────────────────────────────────────────

def test_cli_writes_file_when_settings_missing(tmp_path, merge_module, monkeypatch):
    """If settings.local.json doesn't exist, merge creates it from {}."""
    fragment_path = tmp_path / "fragment.json"
    settings_path = tmp_path / ".claude" / "settings.local.json"

    fragment_path.write_text(json.dumps({
        "hooks": [
            {"event": "PreToolUse", "if": "Bash(*)", "tool": "/x.py",
             "env": {"CONSTRAINT_ID": "constraint:foo"}, "outcome": "deny"},
        ],
    }))

    monkeypatch.setattr(sys, "argv", [
        "merge_settings.py",
        "--fragment", str(fragment_path),
        "--settings", str(settings_path),
    ])
    rc = merge_module.main()
    assert rc == 0
    assert settings_path.exists()
    written = json.loads(settings_path.read_text())
    assert len(written["hooks"]) == 1


def test_cli_dry_run_writes_nothing(tmp_path, merge_module, monkeypatch):
    fragment_path = tmp_path / "fragment.json"
    settings_path = tmp_path / "settings.json"
    fragment_path.write_text(json.dumps({"hooks": []}))

    monkeypatch.setattr(sys, "argv", [
        "merge_settings.py",
        "--fragment", str(fragment_path),
        "--settings", str(settings_path),
        "--dry-run",
    ])
    rc = merge_module.main()
    assert rc == 0
    assert not settings_path.exists()


def test_cli_returns_error_when_fragment_missing(tmp_path, merge_module, monkeypatch, capsys):
    """Missing fragment is a config error — exit 1, not silent success."""
    monkeypatch.setattr(sys, "argv", [
        "merge_settings.py",
        "--fragment", str(tmp_path / "does-not-exist.json"),
        "--settings", str(tmp_path / "settings.json"),
    ])
    rc = merge_module.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "fragment file not found" in err or "not found" in err.lower()


# ── round trip with the real emit_hooks output ──────────────────────────────

def test_round_trip_emit_then_merge(seeded, tmp_path, merge_module):
    """End-to-end: emit_hooks → write fragment → merge into empty settings →
    merge again → confirm idempotent + every hook from fragment is present."""
    import os

    import emit_hooks
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    fragment = emit_hooks.emit_hooks(db)
    client.close()

    fragment_path = tmp_path / "hooks.json"
    settings_path = tmp_path / "settings.local.json"
    fragment_path.write_text(json.dumps(fragment))

    # Pre-populate settings with a user-authored hook
    user_hook = {"event": "Stop", "tool": "/users-own-script.sh"}
    settings_path.write_text(json.dumps({"hooks": [user_hook]}))

    initial = json.loads(settings_path.read_text())
    merged_once, _ = merge_module.merge(initial, fragment)
    merged_twice, _ = merge_module.merge(merged_once, fragment)

    assert merged_once == merged_twice
    assert user_hook in merged_once["hooks"]
    # Every emitted hook is in the merged result
    for h in fragment["hooks"]:
        assert h in merged_once["hooks"]
