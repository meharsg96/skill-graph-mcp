#!/usr/bin/env bash
# Tag the SESSION_ID for everything that follows in this shell, then run
# whatever command was passed. Every MCP tool call captured by server.py's
# @log_tool_call decorator will land in db.runs with this session_id.
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
