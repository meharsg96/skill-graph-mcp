# Architecture

Three layers, intentionally thin.

## 1. Data — `schema/skills.json` → `db.skills`

Each skill document is a typed contract: `_id`, `input_type`, `output_type`,
`lifecycle` (`active` | `inactive`), `version`, `dependencies` (array of skill
`_id`s), plus open-ended `domain_fields`. `scripts/seed.py` enforces the
required shape via a `$jsonSchema` collection validator and creates indexes on
`dependencies`, `lifecycle`, `input_type`, `output_type`.

The separate `edges` collection is seeded for impact-analysis use in v2; v1
tools do not read it.

## 2. Traversal — `$graphLookup`

`scripts/validate.py:get_downstream_chain` and `server.py:traverse_dependencies`
walk `_id → dependencies` recursively with
`restrictSearchWithMatch: { lifecycle: "active" }`. Inactive skills are pruned
*mid-traversal* rather than filtered after. `depthField` orders the result.

If you change the lifecycle vocabulary or the dependency field name, both call
sites must be updated.

## 3. Gateway — `server.py`

Ten FastMCP tools wrap MongoDB queries:

- v1 (parameter retrieval, validation, traversal): `get_skill_contract`,
  `get_tokens`, `get_components`, `get_layouts`, `validate_chain`,
  `traverse_dependencies`
- v2 (contracts, routing, impact, tenants): `route_task`, `search_skills`,
  `list_skills`, `get_skill_instructions`, `impact_analysis`

**The load-bearing invariant:** every read tool filters by `lifecycle: "active"`
*by default* so inactive skills are invisible to consumers. The five v2 tools
enforce this in the same place — `route_task`'s and `impact_analysis`'s
`$graphLookup` calls use `restrictSearchWithMatch: { lifecycle: "active" }`,
`search_skills` adds it to the find filter, `list_skills` defaults to
`lifecycle="active"` and only includes inactive skills if the caller explicitly
asks (`lifecycle="inactive"` or `lifecycle="any"`). Preserve this when adding
tools.

**Discovery vs search:** `search_skills` is text-index based — use it for
ranked relevance against keywords. `list_skills` is declarative — use it for
enumeration (`"every active skill"`, `"who consumes application_code?"`).
The distinction was added in v2.1 after R1 found the agent reading
`schema/skills.json` directly because text-index search couldn't answer
"list everything" queries.

## 4. Contract layer (v2) — ABI shape

Skill documents carry an explicit application binary interface:

```json
{
  "_id": "skill:schema-review",
  "version": "2.1.0",
  "input":  {"type": "query_patterns",        "schema": "schema:query-patterns:v1"},
  "output": {"type": "schema_recommendation", "schema": "schema:schema-recommendation:v2"},
  "dependencies": ["skill:query-analysis"],
  "dependency_constraints": {
    "skill:query-analysis": {"version_range": ">=1.0.0 <2.0.0"}
  },
  "parameter_sources": ["params:index-rules", "params:anti-patterns"]
}
```

`seed.py` derives top-level `input_type` / `output_type` from the new
`input.type` / `output.type` blocks at insert time so v1 tools keep working
unchanged. v2 tools (`route_task`, `impact_analysis`) read the canonical
`input.type` / `output.type`. See `docs/MIGRATION_v1_v2.md`.

## 5. Tenant precedence (v2)

`get_tokens` / `get_components` / `get_layouts` accept an optional
`tenant=` argument. Lookup order:

1. `db.parameters` document matching `(skill_id, tenant)` — wins if present
2. The skill's own `domain_fields` — fallback

Responses include a `source` field (`parameters[<tenant>]` or
`skill_default`) so callers can reason about which path was taken without
repeating the lookup.

The `tenant` field carries two roles by convention:

- `tenant="default"` — the skill's canonical design system / config (e.g.
  LeafyGreen's full token set lives in `params:leafygreen-ui:default`)
- any other value — a per-deployment override (e.g. `client-a`, `client-b`)

`get_components`'s `parameters[tenant].components` shape can be either the
flat single-level form used by `skill:ui-builder` (`{buttons: [...]}`) or
LeafyGreen's two-level form (`{categories: {form: {button: {...}}}}`).
The tool unwraps `categories` if present so callers see the same outer
shape for both.

## 5b. Closing the dead-end (v2.2.0)

R2 found that `skill:leafygreen-ui`'s `react_artifact` had no
downstream consumers — a dead-end chain. The agent flagged it twice
unprompted as an architectural gap. v2.2.0 closes it with
`skill:react-test-writer` (`input.type=react_artifact` →
`output.type=react_test_suite`).

Design choice: a sibling skill rather than generalizing
`skill:test-writer` to accept multiple input types. Keeps the ABI
strictly single-type per skill; matches the `react_artifact` /
`ui_components` distinction we already made for the producer side.
`route_task('test_suite')` and `route_task('react_test_suite')`
return different chains — both discoverable via `list_skills` and
`route_task`.

This is the architecture acting as a *design tool*: the agent's
unprompted observation in R2 became a typed, testable schema change
in v2.2.0. See `notes/r2-leafygreen.md` F5 for the full thread.

## 6. Edges collection

Used by `impact_analysis` to surface explicit incompatibilities flagged with
`compatible: false` (with an optional `note`). Seeded since v1; activated by
v2.

## Instrumentation

`@log_tool_call` wraps every tool. One document per call into `db.runs`:

```json
{
  "tool": "get_tokens",
  "params": {"skill_id": "skill:ui-builder", "theme": "dark"},
  "tokens_returned": 82,
  "duration_ms": 3.5,
  "session_id": "session:1719450000",
  "error": null,
  "timestamp": "2026-05-05T..."
}
```

Failures inside the logger never propagate to the caller — instrumentation must
not break the tool surface.

`tokens_returned` is `len(json.dumps(result)) // 4` — a `cl100k_base`-ish
approximation that needs no `tiktoken` dependency.

`runs` has a TTL index (90 days) and a `(session_id, timestamp)` index for
session-scoped aggregations. It is never dropped on re-seed.

`SESSION_ID` is read **once at module load**, not per call. Reading it per call
would silently break grouping when MCP hosts spawn the server with empty env
(the spec default). The fallback `session:auto-<pid>-<epoch>` ensures every
server process produces a stable, queryable session id even when no `SESSION_ID`
is forwarded; an explicit `SESSION_ID` always wins when provided.

**SESSION_ID propagation paths:**

| Launch path | SESSION_ID source |
|---|---|
| `python server.py` standalone (env exported in shell) | inherits parent env ✅ |
| `python scripts/route.py …` (in-process import) | inherits parent env ✅ |
| `scripts/run_session.sh python server.py` | inherits via `exec` ✅ |
| MCP host (`claude mcp add`, FastMCP `Client('server.py')`) | empty env by spec — auto fallback ⚠️ |
| MCP host with explicit `env:` config block | inherits from forwarded env ✅ |
| `scripts/mcp_host.py` (in-repo helper) | forwards `MONGODB_URI` + `SESSION_ID` explicitly ✅ |

`scripts/mcp_host.py` exists specifically because the bare FastMCP `Client`
path drops env. It uses `StdioTransport(env=…)` to opt in.

## Conventions

- Skill IDs use the `skill:<slug>` namespace.
- `domain_fields` is intentionally untyped — skill-specific config (design
  tokens, breakpoints, components, languages) lives there. Don't promote
  fields out of `domain_fields` into the top-level schema unless they need
  to participate in indexed queries or validation.
- `seed.py` drops `skills` and `edges` on every run. `runs` is preserved.
