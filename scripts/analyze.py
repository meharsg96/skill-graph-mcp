#!/usr/bin/env python3
"""Render measurement tables from db.runs.

The harness logs one document per MCP tool call (server.py:log_tool_call).
This script aggregates those documents into the markdown tables that
appear in the blog series.

Usage:
    python scripts/analyze.py --table blog1
    python scripts/analyze.py --table blog2 --session session:auto-...
    python scripts/analyze.py --all

Environment:
    MONGODB_URI    Mongo connection string (default: mongodb://localhost:27017)
"""

import argparse
import os
import sys
from collections import defaultdict

from pymongo import MongoClient

DB_NAME = "skill_graph"
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")

# Logged tools that route work but do not themselves return artifact bytes.
# Discovery / navigation / validation surface — distinct from "work" tools
# (get_tokens, get_components, get_layouts, get_skill_contract,
# get_skill_instructions) that return the data the agent acts on.
#
# v2.1.x: list_skills and traverse_dependencies were missing from this set
# because the categorization predated v2.1.0. R2 surfaced the gap — its
# routing-ratio of 0.47 was undercounting list_skills as work.
ROUTING_TOOLS = {
    "route_task",
    "validate_chain",
    "search_skills",
    "impact_analysis",
    "list_skills",
    "traverse_dependencies",
}


def _runs_collection():
    return MongoClient(MONGODB_URI)[DB_NAME]["runs"]


def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def _print_table(rows, headers):
    widths = [max(len(h), *(len(str(r[i])) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    sep = " | "
    print(sep.join(h.ljust(w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))
    for r in rows:
        print(sep.join(str(c).ljust(w) for c, w in zip(r, widths)))


def compute_blog1(runs_col, session_id: str | None = None) -> list[dict]:
    """Aggregation behind the Blog 1 table.

    Returns one dict per tool with: tool, calls, errors, avg_tokens, p50, p95.
    Sorted by avg_tokens descending. Pure function — no I/O beyond the
    runs collection cursor — so tests can call it with a fixture-seeded
    collection without touching the printer.
    """
    match: dict = {}
    if session_id:
        match["session_id"] = session_id

    by_tool: dict[str, list[int]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    for d in runs_col.find(match, {"tool": 1, "tokens_returned": 1, "error": 1}):
        by_tool[d["tool"]].append(d.get("tokens_returned") or 0)
        if d.get("error"):
            errors[d["tool"]] += 1

    rows = []
    for tool in sorted(by_tool, key=lambda t: -sum(by_tool[t]) / max(len(by_tool[t]), 1)):
        v = by_tool[tool]
        rows.append({
            "tool": tool,
            "calls": len(v),
            "errors": errors[tool],
            "avg_tokens": round(sum(v) / len(v), 1),
            "p50": _percentile(v, 50),
            "p95": _percentile(v, 95),
        })
    return rows


def compute_blog2(runs_col, session_id: str | None = None) -> list[dict]:
    """Aggregation behind the Blog 2 table.

    Returns one dict per session with: session, routing_calls, work_calls,
    routing_ratio, errors, total_tokens. Sorted by session id.
    """
    match = {}
    if session_id:
        match["session_id"] = session_id
    rows_by_session: dict[str, dict[str, int]] = defaultdict(lambda: {"routing": 0, "work": 0, "errors": 0, "tokens": 0})
    for d in runs_col.find(match, {"tool": 1, "session_id": 1, "tokens_returned": 1, "error": 1}):
        rec = rows_by_session[d.get("session_id", "unknown")]
        if d["tool"] in ROUTING_TOOLS:
            rec["routing"] += 1
        else:
            rec["work"] += 1
        if d.get("error"):
            rec["errors"] += 1
        rec["tokens"] += d.get("tokens_returned") or 0

    rows = []
    for sess in sorted(rows_by_session):
        rec = rows_by_session[sess]
        total_calls = rec["routing"] + rec["work"]
        ratio = round(rec["routing"] / total_calls, 2) if total_calls else 0
        rows.append({
            "session": sess,
            "routing_calls": rec["routing"],
            "work_calls": rec["work"],
            "routing_ratio": ratio,
            "errors": rec["errors"],
            "total_tokens": rec["tokens"],
        })
    return rows


def compute_sessions(runs_col) -> list[dict]:
    """List every distinct session_id in the runs collection with call count
    and last-seen timestamp. Sorted by last_seen descending so the most
    recent session is at the top — matches the question users actually
    ask ("what's the SESSION_ID I just used?")."""
    pipeline = [
        {"$group": {
            "_id": "$session_id",
            "calls": {"$sum": 1},
            "last_seen": {"$max": "$timestamp"},
            "first_seen": {"$min": "$timestamp"},
            "errors": {"$sum": {"$cond": [{"$ne": ["$error", None]}, 1, 0]}},
        }},
        {"$sort": {"last_seen": -1}},
    ]
    return [
        {
            "session": d["_id"],
            "calls": d["calls"],
            "errors": d["errors"],
            "first_seen": d["first_seen"],
            "last_seen": d["last_seen"],
        }
        for d in runs_col.aggregate(pipeline)
    ]


def list_sessions() -> None:
    """Render the session index to stdout."""
    rows = compute_sessions(_runs_collection())
    print("\n## Sessions in db.runs (most recent first)\n")
    if not rows:
        print("  (no runs logged yet — start the server and make at least one tool call)")
        return
    _print_table(
        [[r["session"], r["calls"], r["errors"],
          r["last_seen"].isoformat() if r["last_seen"] else ""]
         for r in rows],
        ["session", "calls", "errors", "last_seen"],
    )


def blog1_table(session_id: str | None = None) -> None:
    """Render the Blog 1 table to stdout."""
    rows = compute_blog1(_runs_collection(), session_id)
    print(f"\n## Blog 1 — token efficiency per tool"
          f"{f' (session={session_id})' if session_id else ''}\n")
    _print_table([list(r.values()) for r in rows],
                 ["tool", "calls", "errors", "avg_tokens", "p50", "p95"])


def blog2_table(session_id: str | None = None) -> None:
    """Render the Blog 2 table to stdout."""
    rows = compute_blog2(_runs_collection(), session_id)
    print(f"\n## Blog 2 — routing usage per session"
          f"{f' (filtered to {session_id})' if session_id else ''}\n")
    _print_table([list(r.values()) for r in rows],
                 ["session", "routing_calls", "work_calls", "routing_ratio", "errors", "total_tokens"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", choices=["blog1", "blog2"], help="Render a single table")
    ap.add_argument("--all", action="store_true", help="Render every table")
    ap.add_argument("--session", help="Filter to a single SESSION_ID")
    ap.add_argument("--list-sessions", action="store_true",
                    help="List every session_id in db.runs with call count and last-seen timestamp")
    args = ap.parse_args()

    if not args.table and not args.all and not args.list_sessions:
        ap.print_help()
        sys.exit(1)

    if args.list_sessions:
        list_sessions()
    if args.all or args.table == "blog1":
        blog1_table(args.session)
    if args.all or args.table == "blog2":
        blog2_table(args.session)


if __name__ == "__main__":
    main()
