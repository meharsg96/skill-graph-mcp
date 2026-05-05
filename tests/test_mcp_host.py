"""Real-wire E2E: spawn server.py through MCP stdio via scripts/mcp_host.py,
verify that SESSION_ID actually crosses the spec's empty-env boundary into the
runs log. This is the test that pytest-in-process can't write — the whole
point of mcp_host.py is to opt into env forwarding that MCP would otherwise
strip.

If this test breaks, we've shipped a regression that pytest-only CI cannot see."""

import os
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_mcp_host_forwards_session_id_to_runs(seeded):
    """Wire path: shell SESSION_ID -> mcp_host.py -> StdioTransport(env=...)
    -> server.py subprocess -> @log_tool_call -> db.runs."""
    session_id = f"session:e2e-{int(time.time() * 1000)}"
    env = {
        **os.environ,
        "SESSION_ID": session_id,
        "MONGODB_URI": os.environ["MONGODB_URI"],
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [sys.executable, "scripts/mcp_host.py", "route_task", "ui_components"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"mcp_host.py exited {result.returncode}: {result.stderr}"

    runs = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]["runs"]
    docs = list(runs.find({"session_id": session_id}))
    runs.database.client.close()

    # Exactly one runs doc with this session_id, written by route_task,
    # tagged with the session_id we set in the parent shell.
    assert len(docs) == 1, f"expected 1 run for {session_id}, got {len(docs)}: {docs}"
    assert docs[0]["tool"] == "route_task"
    assert docs[0]["session_id"] == session_id
    # Auto-fallback should NOT have fired
    assert not docs[0]["session_id"].startswith("session:auto-")


def test_mcp_host_without_session_id_falls_back(seeded):
    """If SESSION_ID is unset, server.py should auto-generate one. Confirms
    the fallback works through the real wire as well as in-process."""
    env = {k: v for k, v in os.environ.items() if k != "SESSION_ID"}
    env["MONGODB_URI"] = os.environ["MONGODB_URI"]
    before = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]["runs"].count_documents({})

    result = subprocess.run(
        [sys.executable, "scripts/mcp_host.py", "get_skill_contract", "skill_id=skill:query-analysis"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    runs_col = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]["runs"]
    after_docs = list(runs_col.find().sort("timestamp", -1).limit(runs_col.count_documents({}) - before))
    runs_col.database.client.close()

    assert len(after_docs) >= 1
    new_doc = next((d for d in after_docs if d["tool"] == "get_skill_contract"), None)
    assert new_doc is not None
    assert new_doc["session_id"].startswith("session:auto-")
