"""analyze.py aggregations: blog1 (per-tool) and blog2 (per-session)."""

from datetime import datetime, timezone

from pymongo import MongoClient
import os

import analyze


def _seed_runs(reset_db, docs):
    """Insert a fixture set of runs documents bypassing server.py."""
    db = MongoClient(os.environ["MONGODB_URI"])[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    if docs:
        db.runs.insert_many(docs)
    return db.runs


def test_compute_blog1_groups_by_tool_and_orders_by_avg(reset_db):
    runs = _seed_runs(reset_db, [
        {"tool": "get_tokens",     "tokens_returned": 100, "error": None,
         "session_id": "s1", "timestamp": datetime.now(timezone.utc)},
        {"tool": "get_tokens",     "tokens_returned": 200, "error": None,
         "session_id": "s1", "timestamp": datetime.now(timezone.utc)},
        {"tool": "validate_chain", "tokens_returned": 50,  "error": None,
         "session_id": "s1", "timestamp": datetime.now(timezone.utc)},
    ])
    rows = analyze.compute_blog1(runs)
    by_tool = {r["tool"]: r for r in rows}

    assert by_tool["get_tokens"]["calls"] == 2
    assert by_tool["get_tokens"]["avg_tokens"] == 150.0
    assert by_tool["validate_chain"]["calls"] == 1
    assert by_tool["validate_chain"]["avg_tokens"] == 50.0

    # Ordered by avg_tokens descending
    assert rows[0]["tool"] == "get_tokens"
    assert rows[1]["tool"] == "validate_chain"


def test_compute_blog1_counts_errors_separately(reset_db):
    runs = _seed_runs(reset_db, [
        {"tool": "get_tokens", "tokens_returned": 0,  "error": "RuntimeError",
         "session_id": "s1", "timestamp": datetime.now(timezone.utc)},
        {"tool": "get_tokens", "tokens_returned": 80, "error": None,
         "session_id": "s1", "timestamp": datetime.now(timezone.utc)},
    ])
    rows = analyze.compute_blog1(runs)
    assert rows[0]["calls"] == 2
    assert rows[0]["errors"] == 1


def test_compute_blog1_session_filter(reset_db):
    runs = _seed_runs(reset_db, [
        {"tool": "get_tokens", "tokens_returned": 100, "error": None,
         "session_id": "keep", "timestamp": datetime.now(timezone.utc)},
        {"tool": "get_tokens", "tokens_returned": 999, "error": None,
         "session_id": "drop", "timestamp": datetime.now(timezone.utc)},
    ])
    rows = analyze.compute_blog1(runs, session_id="keep")
    assert len(rows) == 1
    assert rows[0]["avg_tokens"] == 100.0  # the 999 from "drop" was excluded


def test_compute_blog2_buckets_routing_vs_work(reset_db):
    """ROUTING_TOOLS classifies discovery/navigation/validation tools as
    routing; everything else (data retrieval) counts as work.

    v2.1.x added list_skills + traverse_dependencies to ROUTING_TOOLS
    after R2 surfaced the gap (its routing-ratio of 0.47 was undercounting
    list_skills as work). This test pins the corrected categorization."""
    runs = _seed_runs(reset_db, [
        {"tool": "route_task",            "tokens_returned": 10,  "error": None,
         "session_id": "demo", "timestamp": datetime.now(timezone.utc)},
        {"tool": "validate_chain",        "tokens_returned": 10,  "error": None,
         "session_id": "demo", "timestamp": datetime.now(timezone.utc)},
        {"tool": "list_skills",           "tokens_returned": 50,  "error": None,
         "session_id": "demo", "timestamp": datetime.now(timezone.utc)},
        {"tool": "traverse_dependencies", "tokens_returned": 80,  "error": None,
         "session_id": "demo", "timestamp": datetime.now(timezone.utc)},
        {"tool": "get_tokens",            "tokens_returned": 100, "error": None,
         "session_id": "demo", "timestamp": datetime.now(timezone.utc)},
        {"tool": "get_components",        "tokens_returned": 100, "error": None,
         "session_id": "demo", "timestamp": datetime.now(timezone.utc)},
        {"tool": "get_components",        "tokens_returned": 100, "error": None,
         "session_id": "demo", "timestamp": datetime.now(timezone.utc)},
    ])
    rows = analyze.compute_blog2(runs)
    assert len(rows) == 1
    r = rows[0]
    assert r["session"] == "demo"
    assert r["routing_calls"] == 4
    assert r["work_calls"] == 3
    assert r["routing_ratio"] == round(4 / 7, 2)
    assert r["total_tokens"] == 450


def test_routing_tools_includes_v2_1_discovery_surface():
    """Regression net for the v2.1.x classification fix. If a future tool
    is added that's clearly discovery/navigation, this test forces
    contributors to either add it here or document why it's work."""
    expected = {"route_task", "validate_chain", "search_skills",
                "impact_analysis", "list_skills", "list_preferences",
                "traverse_dependencies"}
    assert analyze.ROUTING_TOOLS == expected


def test_compute_blog2_session_filter(reset_db):
    runs = _seed_runs(reset_db, [
        {"tool": "route_task", "tokens_returned": 1, "error": None,
         "session_id": "a", "timestamp": datetime.now(timezone.utc)},
        {"tool": "route_task", "tokens_returned": 1, "error": None,
         "session_id": "b", "timestamp": datetime.now(timezone.utc)},
    ])
    rows = analyze.compute_blog2(runs, session_id="a")
    assert {r["session"] for r in rows} == {"a"}


def test_compute_blog1_handles_empty_runs(reset_db):
    runs = _seed_runs(reset_db, [])
    assert analyze.compute_blog1(runs) == []
    assert analyze.compute_blog2(runs) == []


def test_compute_sessions_orders_by_last_seen_desc(reset_db):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    runs = _seed_runs(reset_db, [
        {"tool": "route_task", "tokens_returned": 10, "error": None,
         "session_id": "older", "timestamp": now - timedelta(hours=2)},
        {"tool": "get_tokens", "tokens_returned": 80, "error": None,
         "session_id": "older", "timestamp": now - timedelta(hours=1)},
        {"tool": "route_task", "tokens_returned": 70, "error": "RuntimeError",
         "session_id": "newer", "timestamp": now},
    ])
    rows = analyze.compute_sessions(runs)
    assert [r["session"] for r in rows] == ["newer", "older"]
    assert rows[0]["calls"] == 1 and rows[0]["errors"] == 1
    assert rows[1]["calls"] == 2 and rows[1]["errors"] == 0


def test_compute_sessions_empty_runs(reset_db):
    runs = _seed_runs(reset_db, [])
    assert analyze.compute_sessions(runs) == []


def test_list_sessions_prints_table(reset_db, capsys):
    from datetime import datetime, timezone
    _seed_runs(reset_db, [
        {"tool": "route_task", "tokens_returned": 10, "error": None,
         "session_id": "session:demo-r2", "timestamp": datetime.now(timezone.utc)},
    ])
    analyze.list_sessions()
    out = capsys.readouterr().out
    assert "session:demo-r2" in out
    assert "calls" in out and "last_seen" in out


def test_list_sessions_empty_message(reset_db, capsys):
    _seed_runs(reset_db, [])
    analyze.list_sessions()
    out = capsys.readouterr().out
    assert "no runs logged yet" in out
