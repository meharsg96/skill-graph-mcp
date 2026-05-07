# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Requires MongoDB 7.x+ running locally on `mongodb://localhost:27017` and Python 3.10+.
`MONGODB_URI` env var overrides the default; `SESSION_ID` tags every tool-call log.

`.env` may carry a non-default `MONGODB_URI` if you also run Atlas Local on a
second port (e.g. `:27018`) for `$vectorSearch` work. Scripts that load
`.env` (`seed_constraint_embeddings.py`, `query_layer2.py`) will use that
URI unless overridden — pass `MONGODB_URI=mongodb://localhost:27017 …` to
target the native dev DB explicitly. Shell env wins over `.env` because
`load_dotenv` does not override existing variables.

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
MONGODB_URI=mongodb://localhost:27017 pytest -q   # 194 tests across v1 + v2

python scripts/emit_hooks.py --output .claude/hooks.generated.json
                                      # v2.11: emit Claude Code hook config from db.constraints
```

Register the server with Claude Code: `claude mcp add skill-graph python server.py`.

Tests use `testcontainers[mongodb]` by default but honor a preset `MONGODB_URI` —
no Docker needed locally if you already have Mongo running. CI uses the GH Actions
mongo service container at `localhost:27017`.

## Architecture

This repo is a minimal pattern demo, not a framework. Three layers, intentionally thin:

1. **Data (`schema/skills.json` → MongoDB `skill_graph.skills`)** — Each skill document is a typed contract: `_id`, `input_type`, `output_type`, `lifecycle` (`active`|`inactive`), `version`, `dependencies` (array of skill `_id`s), plus open-ended `domain_fields`. `scripts/seed.py` enforces the required-field shape via a `$jsonSchema` collection validator and creates indexes on `dependencies`, `lifecycle`, `input_type`, `output_type`. The separate `edges` collection is seeded but not currently read by `server.py` or `validate.py` — traversal goes through the `dependencies` array directly.

2. **Graph traversal (`$graphLookup`)** — The core query lives in both `scripts/validate.py:get_downstream_chain` and `server.py:traverse_dependencies`. It walks `_id → dependencies` recursively with `restrictSearchWithMatch: {lifecycle: "active"}`, so inactive skills are pruned mid-traversal rather than filtered after. `depthField` orders the resulting chain. If you change the lifecycle vocabulary or the dependency field name, both call sites must be updated.

3. **MCP gateway (`server.py`)** — Exposes thirteen FastMCP tools. v1: `get_skill_contract`, `get_tokens`, `get_components`, `get_layouts`, `validate_chain`, `traverse_dependencies`. v2: `route_task` (backward $graphLookup), `search_skills` (text index, ranked), `list_skills` (declarative enumeration — added v2.1 after R1 found the agent bypassing search_skills for "list-everything" queries), `get_skill_instructions` (reads `skill_path`), `impact_analysis` (forward $graphLookup + edges scan), `get_preferences` (added v2.6 after R4 found the agent flagging the missing tool unprompted — closes the policy-side bypass risk), `list_preferences` (catalogue-style enumeration — added v2.7 after R5 found per-skill fan-out dominating work-call count for graph-wide preference questions). The deliberate design rule: **agents access the graph through these semantic tools, not raw MongoDB queries**. Every read tool filters by `lifecycle: "active"` so inactive skills are invisible to consumers — preserve that invariant when adding tools.

`validate_chain` performs four independent checks (existence+active, pairwise output→input type compatibility, direct dependencies present earlier in the chain, and `dependency_constraints[dep].version_range` satisfied by the dep's `version`) and accumulates errors rather than short-circuiting. The version-range parser is internal to `server.py` (`_version_range_satisfied`) and supports space-separated `>=`/`<=`/`>`/`<`/`==` clauses on dotted-triple semver cores; unparseable ranges or versions are skipped silently rather than flagged. It does **not** check transitive deps — use `traverse_dependencies` for the full closure. New validation rules should follow the same accumulating-errors pattern.

4. **Instrumentation (`@log_tool_call` in `server.py`)** — Every tool call appends one document to `db.runs` (`tool`, `params`, `tokens_returned ≈ chars/4`, `duration_ms`, `session_id`, `error`, `timestamp`). `SESSION_ID` is captured once at module load; falls back to `session:auto-<pid>-<epoch>` when unset (MCP stdio spec spawns subprocesses with empty env by default). Logger failures never propagate. `runs` has a 90-day TTL and is preserved across re-seeds. `scripts/analyze.py` renders aggregation tables from this data — that is how the blog tables are reproduced.

5. **v2 Contract layer (ABI shape)** — Skill docs carry `input/output {type, schema}` blocks, semver versions, `dependency_constraints`, and `parameter_sources`. `seed.py` derives top-level `input_type` / `output_type` from `input.type` / `output.type` so v1 tools keep working unchanged. `tests/test_v1_compat.py` enforces this. See `docs/MIGRATION_v1_v2.md`.

6. **v2 Tenant precedence** — `get_tokens` / `get_components` / `get_layouts` accept `tenant=`. Lookup order: matching `parameters` document wins → skill's `domain_fields` is the fallback. Responses include a `source` field (`parameters[<tenant>]` or `skill_default`). `tenant="default"` carries the skill's canonical design system (e.g. LeafyGreen); other values are per-deployment overrides (e.g. `client-a`, `client-b`).

7. **v2.1.0 LeafyGreen example** — `skill:leafygreen-ui` is the real-world MongoDB design system as a parameterized skill. Full tokens + 60-component spec live in `params:leafygreen-ui:default`. Demonstrates the architecture against a non-trivial design system; `skills/leafygreen/` carries the original SKILL.md + helper script (the helper agents-bypass demo from Blog 1). Adding it grew the modeled file-read baseline from 15,492 to 60,135 tokens. R2 measured graph-path = **3,577 tokens** → ratio **16.81×** (post-LG measured); this is the canonical pair cited in Blog 1 / Blog 2. See `notes/r2-leafygreen.md`.

8. **v2.2.0 React test chain** — `skill:react-test-writer` (input=`react_artifact`, output=`react_test_suite`) closes the dead-end LeafyGreen had in v2.1.0. Sibling to `skill:test-writer` rather than a union-type extension. Added because R2's agent flagged the gap twice unprompted (`notes/r2-leafygreen.md` F5). The architecture acting as a design tool: agent observation → typed schema change.

9. **v2.3.0 self-documenting meta-skill + richer instructions** — `skill:harness` (input=`meta_query`, output=`system_documentation`) ships the operating manual as a queryable skill. `get_skill_instructions` now returns markdown content + `line_count` + `accessibility_rules` + `related.{dependencies, direct_consumers}` + `source: "graph"` — a strict superset of what `Read` provides, so the agent has a real reason to prefer the graph path. Closes R3's F9 (zero `get_skill_instructions` usage in R1/R2/R3 traced to insufficient response richness). `description` added to the text index for `search_skills`.

10. **v2.4.0 preferences collection** — new `db.preferences` collection separate from `parameters`. Parameters carry data overrides (tokens, components) that change per tenant; preferences carry policy/style/conventions that change per owner. Different lifecycles, different access patterns, different indexes. Schema requires `owner`, `scope` (skill|category|global), `category`, `name`, `version`. Seeded with one example: `pref:cyborg:lg-flavour-not-cage` (the LG-flavour-not-cage policy on `skill:leafygreen-ui`). Forward-compatible with future per-owner Queryable Encryption (no text indexes on policy body, equality-only on indexed fields).

11. **v2.5.0 local overlay** — `seed.py` reads from `$SKILL_GRAPH_LOCAL_DIR` (default `~/.skill-graph-local/`) *after* the canonical seed and upserts each doc by `_id`. Hard guards: local skill `_id`s must start with `skill:local:`; local parameters must reference local skills; local preferences with `scope=skill` must apply to a local skill. `LOCAL_DIR` defaults outside the git tree so `git add` can't see it. `get_skill_instructions` accepts paths under either trusted root (repo root or `LOCAL_DIR`) and rejects everything else as a traversal attempt. See `notes/PLAN-local-overlay.md` and the harness skill's "Local-only skills" section. `tests/test_local_overlay.py` covers the guards.

12. **v2.6.0 / v2.7.0 preference accessors** — `get_preferences(skill_id=, owner=)` (v2.6, after R4 found the agent flagging the missing tool unprompted — closed the policy-side bypass risk) and `list_preferences(scope=, owner=)` (v2.7, after R5 found per-skill fan-out dominating work-call count for graph-wide preference questions). Both filter by `lifecycle:"active"`. The catalogue-style `list_preferences` is the declarative pair to `get_preferences`'s point lookup, mirroring `list_skills` ↔ `search_skills`.

13. **v2.8.0 ui_spec consumer + spec-to-components** — `skill:requirements-to-component` (output=`ui_spec`) had no active consumer; agents were misrouting requirements-backed UI tasks to `skill:leafygreen-ui` because `route_task` couldn't find a `ui_spec` chain end. Closed by `skill:spec-to-components` (input=`ui_spec`, output=`react_artifact`). Sibling pattern to v2.2.0 react-test-writer — closes a typed dead-end without union-typing the input. R5 F4/F5 trigger.

14. **v2.9.0 constraints collection (Layer 2 foundation)** — new `db.constraints` collection holding artifact-validation rules. Each document carries `rule_text` (human-facing), `violation_paraphrase` (positive description of what a violation looks like — embeds well, unlike negation-phrased rules), `examples.{violating, compliant}`, `severity` (fail|warn|note), `category`, and a placeholder `constraint_embedding`. The two-representation design (raw artifact text + canonical fact summary; rule text + violation paraphrase) is required because raw embeddings measure topic proximity, not logical compliance — see `memory/layer2_semantic.md`. Atlas Vector Search index seeded; embeddings populated by `scripts/seed_constraint_embeddings.py`.

15. **v2.10.x Layer 2 vector search pipeline** — `scripts/seed_constraint_embeddings.py` populates `constraint_embedding` via Voyage AI; `voyage-4 @ 256 dims, int8` is the production config (mean retrieval gap +0.0664, 77.8% correct on the in-house benchmark, 256B/vec — 16× smaller than `voyage-4 @ 1024 float`; matryoshka peak is 256 dims). `scripts/extract_fact_summary.py` produces canonical artifact summaries via LLM. `check_constraints` MCP tool runs `$vectorSearch` with filter inside the stage (Atlas requires this — must be first stage). Calibration set + threshold scoring are scoped for v3. Multi-commit series: 206b8dc, 0e221fe, 96fc9cf, 569215b. See `notes/voyage-benchmark-findings.md`.

16. **v2.11.0 claude-code subgraph + runtime hook emission** — the Claude Code CLI runtime is itself modeled as a typed skill cluster. 19 new skills under `skill:claude-code:*` (3 root: `permission-mode`, `model-selection`, `hooks`; 8 tools: `bash`, `read`, `edit`, `write`, `agent`, `mcp`, `web`, plus `slash-commands`; 8 subagent types: `general-purpose`, `explore`, `plan`, `code-review-judge`, `agentic-systems-architect`, `frontend-design-elevate`, `claude-code-guide`, `statusline-setup`). Subagent skills declare `domain_fields.tool_whitelist` so capability scoping is queryable. 7 new constraints under `constraint:claude-code:*` (no-verify-bypass, no-force-push-main, destructive-requires-confirm, fast-mode-opus-only, no-delegate-understanding, parallel-independent-only, no-generated-urls). `scripts/emit_hooks.py` translates these constraints into a `settings.json` `hooks` fragment; `scripts/hooks/check_constraint.py` is the runtime evaluator (deny-only — never modifies tool input). `scripts/merge_settings.py` is the idempotent merger that combines the fragment into a live `settings.local.json` while preserving user-added hooks. Maps severity:fail → `outcome:deny`, severity:warn → `outcome:ask`. Constraints with no entry in the `MATCHERS` dispatch table appear in `_meta.skipped` rather than being silently dropped. Operating manual: `skills/claude-code/SKILL.md`; emission workflow: `skills/harness/SKILL.md` § "Emitting hook config from constraints".

## Conventions

- Skill IDs use the `skill:<slug>` namespace; preserve this when adding fixtures.
- `domain_fields` is intentionally untyped — it's where skill-specific config lives (design tokens, breakpoints, languages, etc.). Don't promote fields out of `domain_fields` into the top-level schema unless they need to participate in indexed queries or validation.
- `scripts/seed.py` drops collections on every run; treat the database as ephemeral demo state.
