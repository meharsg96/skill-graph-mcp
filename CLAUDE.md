# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Requires MongoDB 7.x+ running locally on `mongodb://localhost:27017` and Python 3.10+.
`MONGODB_URI` env var overrides the default; `SESSION_ID` tags every tool-call log.

```bash
pip install -r requirements-dev.txt   # runtime + pytest, testcontainers, ruff
python scripts/seed.py                # drop+recreate skills/edges/parameters; preserves runs collection
python scripts/validate.py            # 5 demo validation cases (v1)
python scripts/route.py ui_components # v2: backward $graphLookup → ordered chain
python scripts/impact.py skill:schema-review   # v2: blast radius
python scripts/analyze.py --all       # v2: render blog measurement tables from db.runs
python server.py                      # start the MCP server (stdio via FastMCP)
mongosh < scripts/queries.js          # raw aggregation examples

scripts/run_session.sh python server.py    # tags every tool call with $SESSION_ID

ruff check .                          # lint
MONGODB_URI=mongodb://localhost:27017 pytest -q   # 39 tests (16 v1 + 23 v2)
```

Register the server with Claude Code: `claude mcp add skill-graph python server.py`.

Tests use `testcontainers[mongodb]` by default but honor a preset `MONGODB_URI` —
no Docker needed locally if you already have Mongo running. CI uses the GH Actions
mongo service container at `localhost:27017`.

## Architecture

This repo is a minimal pattern demo, not a framework. Three layers, intentionally thin:

1. **Data (`schema/skills.json` → MongoDB `skill_graph.skills`)** — Each skill document is a typed contract: `_id`, `input_type`, `output_type`, `lifecycle` (`active`|`inactive`), `version`, `dependencies` (array of skill `_id`s), plus open-ended `domain_fields`. `scripts/seed.py` enforces the required-field shape via a `$jsonSchema` collection validator and creates indexes on `dependencies`, `lifecycle`, `input_type`, `output_type`. The separate `edges` collection is seeded but not currently read by `server.py` or `validate.py` — traversal goes through the `dependencies` array directly.

2. **Graph traversal (`$graphLookup`)** — The core query lives in both `scripts/validate.py:get_downstream_chain` and `server.py:traverse_dependencies`. It walks `_id → dependencies` recursively with `restrictSearchWithMatch: {lifecycle: "active"}`, so inactive skills are pruned mid-traversal rather than filtered after. `depthField` orders the resulting chain. If you change the lifecycle vocabulary or the dependency field name, both call sites must be updated.

3. **MCP gateway (`server.py`)** — Exposes twelve FastMCP tools. v1: `get_skill_contract`, `get_tokens`, `get_components`, `get_layouts`, `validate_chain`, `traverse_dependencies`. v2: `route_task` (backward $graphLookup), `search_skills` (text index, ranked), `list_skills` (declarative enumeration — added v2.1 after R1 found the agent bypassing search_skills for "list-everything" queries), `get_skill_instructions` (reads `skill_path`), `impact_analysis` (forward $graphLookup + edges scan), `get_preferences` (added v2.6 after R4 found the agent flagging the missing tool unprompted — closes the policy-side bypass risk). The deliberate design rule: **agents access the graph through these semantic tools, not raw MongoDB queries**. Every read tool filters by `lifecycle: "active"` so inactive skills are invisible to consumers — preserve that invariant when adding tools.

`validate_chain` performs four independent checks (existence+active, pairwise output→input type compatibility, direct dependencies present earlier in the chain, and `dependency_constraints[dep].version_range` satisfied by the dep's `version`) and accumulates errors rather than short-circuiting. The version-range parser is internal to `server.py` (`_version_range_satisfied`) and supports space-separated `>=`/`<=`/`>`/`<`/`==` clauses on dotted-triple semver cores; unparseable ranges or versions are skipped silently rather than flagged. It does **not** check transitive deps — use `traverse_dependencies` for the full closure. New validation rules should follow the same accumulating-errors pattern.

4. **Instrumentation (`@log_tool_call` in `server.py`)** — Every tool call appends one document to `db.runs` (`tool`, `params`, `tokens_returned ≈ chars/4`, `duration_ms`, `session_id`, `error`, `timestamp`). `SESSION_ID` is captured once at module load; falls back to `session:auto-<pid>-<epoch>` when unset (MCP stdio spec spawns subprocesses with empty env by default). Logger failures never propagate. `runs` has a 90-day TTL and is preserved across re-seeds. `scripts/analyze.py` renders aggregation tables from this data — that is how the blog tables are reproduced.

5. **v2 Contract layer (ABI shape)** — Skill docs carry `input/output {type, schema}` blocks, semver versions, `dependency_constraints`, and `parameter_sources`. `seed.py` derives top-level `input_type` / `output_type` from `input.type` / `output.type` so v1 tools keep working unchanged. `tests/test_v1_compat.py` enforces this. See `docs/MIGRATION_v1_v2.md`.

6. **v2 Tenant precedence** — `get_tokens` / `get_components` / `get_layouts` accept `tenant=`. Lookup order: matching `parameters` document wins → skill's `domain_fields` is the fallback. Responses include a `source` field (`parameters[<tenant>]` or `skill_default`). `tenant="default"` carries the skill's canonical design system (e.g. LeafyGreen); other values are per-deployment overrides (e.g. `client-a`, `client-b`).

7. **v2.1.0 LeafyGreen example** — `skill:leafygreen-ui` is the real-world MongoDB design system as a parameterized skill. Full tokens + 60-component spec live in `params:leafygreen-ui:default`. Demonstrates the architecture against a non-trivial design system; `skills/leafygreen/` carries the original SKILL.md + helper script (the helper agents-bypass demo from Blog 1). Adding it grew the modeled file-read baseline from 15,492 to 60,135 tokens. R2 measured graph-path = **3,577 tokens** → ratio **16.81×** (post-LG measured); this is the canonical pair cited in Blog 1 / Blog 2. See `notes/r2-leafygreen.md`.

8. **v2.2.0 React test chain** — `skill:react-test-writer` (input=`react_artifact`, output=`react_test_suite`) closes the dead-end LeafyGreen had in v2.1.0. Sibling to `skill:test-writer` rather than a union-type extension. Added because R2's agent flagged the gap twice unprompted (`notes/r2-leafygreen.md` F5). The architecture acting as a design tool: agent observation → typed schema change.

9. **v2.3.0 self-documenting meta-skill + richer instructions** — `skill:harness` (input=`meta_query`, output=`system_documentation`) ships the operating manual as a queryable skill. `get_skill_instructions` now returns markdown content + `line_count` + `accessibility_rules` + `related.{dependencies, direct_consumers}` + `source: "graph"` — a strict superset of what `Read` provides, so the agent has a real reason to prefer the graph path. Closes R3's F9 (zero `get_skill_instructions` usage in R1/R2/R3 traced to insufficient response richness). `description` added to the text index for `search_skills`.

10. **v2.4.0 preferences collection** — new `db.preferences` collection separate from `parameters`. Parameters carry data overrides (tokens, components) that change per tenant; preferences carry policy/style/conventions that change per owner. Different lifecycles, different access patterns, different indexes. Schema requires `owner`, `scope` (skill|category|global), `category`, `name`, `version`. Seeded with one example: `pref:owner:lg-flavour-not-cage` (the LG-flavour-not-cage policy on `skill:leafygreen-ui`). Forward-compatible with future per-owner Queryable Encryption (no text indexes on policy body, equality-only on indexed fields).

## Conventions

- Skill IDs use the `skill:<slug>` namespace; preserve this when adding fixtures.
- `domain_fields` is intentionally untyped — it's where skill-specific config lives (design tokens, breakpoints, languages, etc.). Don't promote fields out of `domain_fields` into the top-level schema unless they need to participate in indexed queries or validation.
- `scripts/seed.py` drops collections on every run; treat the database as ephemeral demo state.
