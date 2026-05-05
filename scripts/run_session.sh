#!/usr/bin/env bash
# Tag the SESSION_ID for everything that follows in this shell, then run
# whatever command was passed. Every MCP tool call captured by server.py's
# @log_tool_call decorator lands in db.runs with this session_id.
#
# Works for: in-process tool calls — `scripts/route.py`, `scripts/impact.py`,
# any Python that imports `server` directly, or running `server.py` standalone
# (the wrapped subprocess inherits the env).
#
# Does NOT propagate when an MCP host (e.g. `claude mcp add`, FastMCP `Client`)
# spawns server.py: MCP's stdio spec hands subprocesses an empty env by
# default, so SESSION_ID is dropped. For that case, list it explicitly in the
# host's MCP server config (the `env: { "SESSION_ID": "..." }` block), or
# launch via `scripts/mcp_host.py` (in-process — env crosses naturally).
#
# Usage:
#     scripts/run_session.sh python server.py
#     scripts/run_session.sh python scripts/route.py ui_components
#     SESSION_ID=session:my-experiment scripts/run_session.sh ...   # override
#
# After the session, summarise:
#     python scripts/analyze.py --session "$SESSION_ID" --all

set -euo pipefail

if [[ -z "${SESSION_ID:-}" ]]; then
  export SESSION_ID="session:$(date +%s)"
fi

echo "SESSION_ID=$SESSION_ID" >&2
exec "$@"
