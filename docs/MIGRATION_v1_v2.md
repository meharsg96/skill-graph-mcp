# Migrating from v1 to v2

**TL;DR:** v1 callers need zero changes. The schema gained `input/output`
blocks, semver, dependency constraints, and `parameter_sources`; the
top-level `input_type` / `output_type` fields are still written by
`seed.py` so v1 tools keep working unchanged.

## What changed in the skill document

```diff
 {
   "_id": "skill:schema-review",
-  "version": "2.1",
+  "version": "2.1.0",                                       # semver
-  "input_type":  "query_patterns",
-  "output_type": "schema_recommendation",
+  "input":  {"type": "query_patterns",        "schema": "schema:query-patterns:v1"},
+  "output": {"type": "schema_recommendation", "schema": "schema:schema-recommendation:v2"},
   "lifecycle": "active",
   "dependencies": ["skill:query-analysis"],
+  "dependency_constraints": {
+    "skill:query-analysis": {"version_range": ">=1.0.0 <2.0.0"}
+  },
+  "parameter_sources": ["params:index-rules", "params:anti-patterns"],
   "domain_fields": {...}
 }
```

**Backward compatibility is at the database level.** The canonical form in
`schema/skills.json` is the v2 ABI. `seed.py` derives `input_type` and
`output_type` from `input.type` / `output.type` at insert time, so:

- Every v1 tool (`get_skill_contract`, `validate_chain`,
  `traverse_dependencies`, …) continues to read top-level type fields and
  returns the same shape it always did.
- `tests/test_v1_compat.py` enforces this at the response-shape level —
  it will fail if a v1 tool's payload drifts.

## What's new

### `parameters` collection (multi-tenant)

```json
{
  "_id": "params:ui-builder:client-a",
  "skill_id": "skill:ui-builder",
  "tenant": "client-a",
  "design_tokens": { ... },
  "component_overrides": { "button_radius": 8 }
}
```

Unique compound index on `(skill_id, tenant)`.

### Tenant precedence in retrieval tools

`get_tokens`, `get_components`, `get_layouts` now accept `tenant=`. When
provided, the matching `parameters` document wins; absent it, the skill's
`domain_fields` is the fallback. Responses include a `source` field
(`parameters[<tenant>]` or `skill_default`) so callers can reason about
which path was taken.

### Four new tools

| Tool | Pattern |
|------|---------|
| `route_task(target_output_type)` | backward `$graphLookup` from `output.type == target` → ordered chain (root deps first) |
| `search_skills(query, limit=)` | Mongo text index over `name` + `domain_fields` |
| `get_skill_instructions(skill_id)` | reads the skill's `skill_path` markdown body; bounded |
| `impact_analysis(skill_id)` | direct + transitive consumers + `edges.compatible:false` |

### `edges` collection becomes meaningful

The `edges` documents (seeded since v1) are now read by `impact_analysis`
to flag explicit incompatibilities (e.g. `schema-review-v1 → code-gen`).

## Upgrade steps

```bash
git fetch --tags
git checkout v2
pip install -r requirements.txt
python scripts/seed.py    # re-seed with the new shape; runs collection preserved
```

Existing MCP clients keep working. Add `tenant=` arguments where useful.

## What did NOT change

- The `lifecycle: "active"` filtering invariant (every read tool, including
  the four new ones, prunes inactive skills mid-traversal).
- The instrumentation harness (`@log_tool_call` → `db.runs`).
- The `runs` collection (preserved on re-seed; same TTL and indexes).
- The validator schema for `runs`.
- The `MONGODB_URI` and `SESSION_ID` env-var contracts.
