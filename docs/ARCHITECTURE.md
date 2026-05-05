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
  `get_skill_instructions`, `impact_analysis`

**The load-bearing invariant:** every read tool filters by `lifecycle: "active"`
so inactive skills are invisible to consumers. The four v2 tools enforce this
in the same place — `route_task`'s and `impact_analysis`'s `$graphLookup`
calls use `restrictSearchWithMatch: { lifecycle: "active" }`, and
`search_skills` adds it to the find filter. Preserve this when adding tools.

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

## Conventions

- Skill IDs use the `skill:<slug>` namespace.
- `domain_fields` is intentionally untyped — skill-specific config (design
  tokens, breakpoints, components, languages) lives there. Don't promote
  fields out of `domain_fields` into the top-level schema unless they need
  to participate in indexed queries or validation.
- `seed.py` drops `skills` and `edges` on every run. `runs` is preserved.
