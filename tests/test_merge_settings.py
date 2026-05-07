"""scripts/merge_settings.py — idempotent merge of an emit_hooks fragment
into Claude Code's settings.local.json hooks block.

Schema (per https://code.claude.com/docs/en/hooks):
  hooks: {<EventName>: [{matcher, hooks: [{type, command}]}]}

Identification rule for graph-owned hook commands: command string contains
`CONSTRAINT_ID=constraint:`. User-authored commands lack that signature
and are preserved untouched.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture
def merge_module():
    import merge_settings
    return merge_settings


def _graph_command(constraint_id: str = "constraint:foo:bar") -> dict:
    return {
        "type": "command",
        "command": (
            f"CONSTRAINT_ID={constraint_id} MONGODB_URI=mongodb://x "
            f"/path/to/check_constraint.py"
        ),
    }


def _user_command() -> dict:
    return {"type": "command", "command": "/users/own/script.sh"}


# ── pure merge function ─────────────────────────────────────────────────────

def test_merge_into_empty_settings(merge_module):
    fragment = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_graph_command("constraint:a")]},
            ],
        },
    }
    merged, summary = merge_module.merge({}, fragment)
    assert merged["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert summary["graph_hooks_added"] == 1
    assert summary["user_hooks_preserved"] == 0


def test_merge_preserves_user_authored_hooks(merge_module):
    settings = {
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [_user_command()]},
            ],
        },
    }
    fragment = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_graph_command("constraint:a")]},
            ],
        },
    }
    merged, summary = merge_module.merge(settings, fragment)
    # User Stop hook intact
    stop_entries = merged["hooks"]["Stop"]
    assert any(
        h.get("command") == "/users/own/script.sh"
        for entry in stop_entries
        for h in entry.get("hooks", [])
    )
    # Graph PreToolUse hook added
    assert "PreToolUse" in merged["hooks"]
    assert summary["user_hooks_preserved"] >= 1


def test_merge_replaces_graph_owned_hooks(merge_module):
    """Existing graph-owned hooks must be dropped before adding the new
    fragment — running emit twice must not duplicate."""
    old = _graph_command("constraint:a")
    new = {
        "type": "command",
        "command": "CONSTRAINT_ID=constraint:a MONGODB_URI=mongodb://NEW /x.py",
    }
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [old]},
            ],
        },
    }
    fragment = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [new]},
            ],
        },
    }
    merged, summary = merge_module.merge(settings, fragment)
    # Old gone, new present
    bash_entries = [
        e for e in merged["hooks"]["PreToolUse"] if e["matcher"] == "Bash"
    ]
    assert len(bash_entries) == 1
    cmds = [h["command"] for h in bash_entries[0]["hooks"]]
    assert all("MONGODB_URI=mongodb://NEW" in c for c in cmds)
    assert summary["graph_hooks_dropped"] == 1
    assert summary["graph_hooks_added"] == 1


def test_merge_is_idempotent(merge_module):
    fragment = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    _graph_command("constraint:a"),
                    _graph_command("constraint:b"),
                ]},
            ],
        },
    }
    settings = {"hooks": {"Stop": [{"matcher": "", "hooks": [_user_command()]}]}}
    m1, _ = merge_module.merge(settings, fragment)
    m2, _ = merge_module.merge(m1, fragment)
    m3, _ = merge_module.merge(m2, fragment)
    assert m1["hooks"] == m2["hooks"] == m3["hooks"]


def test_merge_collapses_same_matcher_across_user_and_graph(merge_module):
    """User has Bash hook, graph emits Bash hook → one Bash matcher entry
    with both commands listed inside."""
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_user_command()]},
            ],
        },
    }
    fragment = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_graph_command("constraint:a")]},
            ],
        },
    }
    merged, _ = merge_module.merge(settings, fragment)
    bash_entries = [
        e for e in merged["hooks"]["PreToolUse"] if e["matcher"] == "Bash"
    ]
    assert len(bash_entries) == 1, "same-matcher entries should collapse"
    cmds = [h["command"] for h in bash_entries[0]["hooks"]]
    # Both user and graph commands present
    assert any("CONSTRAINT_ID=" in c for c in cmds)
    assert any("/users/own/script.sh" in c for c in cmds)


def test_merge_preserves_non_hooks_keys(merge_module):
    settings = {
        "model": "claude-opus-4-7",
        "permissions": {"allow": ["Read"]},
        "hooks": {},
    }
    fragment = {"hooks": {}}
    merged, _ = merge_module.merge(settings, fragment)
    assert merged["model"] == "claude-opus-4-7"
    assert merged["permissions"] == {"allow": ["Read"]}


def test_merge_treats_missing_hooks_key_as_empty(merge_module):
    settings = {"model": "claude-opus-4-7"}
    fragment = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_graph_command("constraint:a")]},
            ],
        },
    }
    merged, _ = merge_module.merge(settings, fragment)
    assert "PreToolUse" in merged["hooks"]


def test_is_graph_owned_recognizes_constraint_id_marker(merge_module):
    assert merge_module.is_graph_owned(_graph_command())
    assert not merge_module.is_graph_owned(_user_command())
    assert not merge_module.is_graph_owned({"type": "command", "command": ""})
    assert not merge_module.is_graph_owned({"type": "http", "url": "..."})


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_writes_file_when_settings_missing(tmp_path, merge_module, monkeypatch):
    fragment_path = tmp_path / "fragment.json"
    settings_path = tmp_path / ".claude" / "settings.local.json"
    fragment_path.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_graph_command("constraint:a")]},
            ],
        },
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
    assert "PreToolUse" in written["hooks"]


def test_cli_dry_run_writes_nothing(tmp_path, merge_module, monkeypatch):
    fragment_path = tmp_path / "fragment.json"
    settings_path = tmp_path / "settings.json"
    fragment_path.write_text(json.dumps({"hooks": {}}))
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
    monkeypatch.setattr(sys, "argv", [
        "merge_settings.py",
        "--fragment", str(tmp_path / "missing.json"),
        "--settings", str(tmp_path / "settings.json"),
    ])
    rc = merge_module.main()
    assert rc == 1


# ── round trip with real emit_hooks output ──────────────────────────────────

def test_round_trip_emit_then_merge(seeded, tmp_path, merge_module):
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

    user_event_hook = {"matcher": "", "hooks": [_user_command()]}
    settings_path.write_text(json.dumps({"hooks": {"Stop": [user_event_hook]}}))

    initial = json.loads(settings_path.read_text())
    merged_once, _ = merge_module.merge(initial, fragment)
    merged_twice, _ = merge_module.merge(merged_once, fragment)
    assert merged_once == merged_twice
    # User Stop hook still there
    assert any(
        "/users/own/script.sh" == h.get("command")
        for entry in merged_once["hooks"]["Stop"]
        for h in entry.get("hooks", [])
    )
