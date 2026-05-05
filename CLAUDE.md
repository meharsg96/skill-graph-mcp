# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Requires MongoDB 7.x+ running locally on `mongodb://localhost:27017` and Python 3.10+.
`MONGODB_URI` env var overrides the default; `SESSION_ID` tags every tool-call log.

```bash
pip install -r requirements-dev.txt   # runtime + pytest, testcontainers, ruff
python scripts/seed.py                # drop+recreate skills/edges; preserves runs collection
python scripts/validate.py            # 5 demo validation cases
python server.py                      # start the MCP server (stdio via FastMCP)
mongosh < scripts/queries.js          # raw aggregation examples

ruff check .                          # lint
MONGODB_URI=mongodb://localhost:27017 pytest -q   # 15 smoke tests
```

Register the server with Claude Code: `claude mcp add skill-graph python server.py`.

Tests use `testcontainers[mongodb]` by default but honor a preset `MONGODB_URI` —
no Docker needed locally if you already have Mongo running. CI uses the GH Actions
mongo service container at `localhost:27017`.

## Architecture

This repo is a minimal pattern demo, not a framework. Three layers, intentionally thin:

1. **Data (`schema/skills.json` → MongoDB `skill_graph.skills`)** — Each skill document is a typed contract: `_id`, `input_type`, `output_type`, `lifecycle` (`active`|`inactive`), `version`, `dependencies` (array of skill `_id`s), plus open-ended `domain_fields`. `scripts/seed.py` enforces the required-field shape via a `$jsonSchema` collection validator and creates indexes on `dependencies`, `lifecycle`, `input_type`, `output_type`. The separate `edges` collection is seeded but not currently read by `server.py` or `validate.py` — traversal goes through the `dependencies` array directly.

2. **Graph traversal (`$graphLookup`)** — The core query lives in both `scripts/validate.py:get_downstream_chain` and `server.py:traverse_dependencies`. It walks `_id → dependencies` recursively with `restrictSearchWithMatch: {lifecycle: "active"}`, so inactive skills are pruned mid-traversal rather than filtered after. `depthField` orders the resulting chain. If you change the lifecycle vocabulary or the dependency field name, both call sites must be updated.

3. **MCP gateway (`server.py`)** — Exposes six FastMCP tools (`get_skill_contract`, `get_tokens`, `get_components`, `get_layouts`, `validate_chain`, `traverse_dependencies`). The deliberate design rule from the blog post: **agents must access the graph through these semantic tools, not raw MongoDB queries**. Every read tool filters by `lifecycle: "active"` so inactive skills are invisible to consumers — preserve that invariant when adding tools.

`validate_chain` performs three independent checks (existence+active, pairwise output→input type compatibility, direct dependencies present earlier in the chain) and accumulates errors rather than short-circuiting. It does **not** check transitive deps — use `traverse_dependencies` for the full closure. New validation rules should follow the same accumulating-errors pattern.

4. **Instrumentation (`@log_tool_call` in `server.py`)** — Every tool call appends one document to `db.runs` (`tool`, `params`, `tokens_returned ≈ chars/4`, `duration_ms`, `session_id`, `error`, `timestamp`). Logger failures never propagate. `runs` has a 90-day TTL and is preserved across re-seeds. `scripts/analyze.py` (added in v2) renders aggregation tables from this data — that is how the blog tables are reproduced.

## Conventions

- Skill IDs use the `skill:<slug>` namespace; preserve this when adding fixtures.
- `domain_fields` is intentionally untyped — it's where skill-specific config lives (design tokens, breakpoints, languages, etc.). Don't promote fields out of `domain_fields` into the top-level schema unless they need to participate in indexed queries or validation.
- `scripts/seed.py` drops collections on every run; treat the database as ephemeral demo state.
