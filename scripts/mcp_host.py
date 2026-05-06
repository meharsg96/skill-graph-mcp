#!/usr/bin/env python3
"""Minimal MCP host that explicitly forwards SESSION_ID to server.py.

This exists because MCP's stdio transport spawns the server with an empty
env by default — so SESSION_ID exported in the parent shell does not reach
the server subprocess unless the host opts in. FastMCP's `Client('server.py')`
inherits that behavior. This script forwards env explicitly so
session-scoped aggregations work end-to-end through the real MCP wire.

Usage:
    SESSION_ID=session:demo scripts/run_session.sh python scripts/mcp_host.py route_task ui_components
    python scripts/mcp_host.py impact_analysis skill:schema-review
    python scripts/mcp_host.py get_tokens skill:ui-builder tenant=client-a theme=dark

Args after the tool name are kw=value pairs; positional `skill_id` and
`target_output_type` are accepted as the first arg if no `=` is present.

Environment forwarded to the server subprocess:
    MONGODB_URI    (required for server-side connection)
    SESSION_ID     (the whole point of this script)
"""

import asyncio
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


def parse_args(argv):
    if not argv:
        print("usage: mcp_host.py <tool> [arg=value ...]", file=sys.stderr)
        sys.exit(2)
    tool, rest = argv[0], argv[1:]
    kwargs = {}
    positional = None
    for a in rest:
        if "=" in a:
            k, v = a.split("=", 1)
            kwargs[k] = v
        else:
            positional = a
    if positional and not kwargs:
        # Heuristic: bare positional → most-likely id arg
        if tool in {"route_task"}:
            kwargs["target_output_type"] = positional
        else:
            kwargs["skill_id"] = positional
    return tool, kwargs


async def run(tool, kwargs):
    forward_keys = ("MONGODB_URI", "SESSION_ID", "SKILL_GRAPH_DB", "SKILL_GRAPH_LOCAL_DIR")
    env = {k: os.environ[k] for k in forward_keys if k in os.environ}
    transport = StdioTransport(command=sys.executable, args=["server.py"], env=env)
    async with Client(transport) as c:
        result = await c.call_tool(tool, kwargs)
        print(json.dumps(result.data, indent=2, default=str))


def main():
    tool, kwargs = parse_args(sys.argv[1:])
    asyncio.run(run(tool, kwargs))


if __name__ == "__main__":
    main()
