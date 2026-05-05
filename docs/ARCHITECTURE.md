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

Six FastMCP tools wrap MongoDB queries:
`get_skill_contract`, `get_tokens`, `get_components`, `get_layouts`,
`validate_chain`, `traverse_dependencies`.

**The load-bearing invariant:** every read tool filters by `lifecycle: "active"`
so inactive skills are invisible to consumers. Preserve this when adding tools.

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

## Conventions

- Skill IDs use the `skill:<slug>` namespace.
- `domain_fields` is intentionally untyped — skill-specific config (design
  tokens, breakpoints, components, languages) lives there. Don't promote
  fields out of `domain_fields` into the top-level schema unless they need
  to participate in indexed queries or validation.
- `seed.py` drops `skills` and `edges` on every run. `runs` is preserved.
