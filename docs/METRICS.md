# Metrics inventory

Every observable property of the skill graph and its harness, mapped to
the tool or query that surfaces it. Useful when instrumenting adoption,
producing blog evidence, or auditing the system's state without writing
custom aggregations.

Two domains:
- **Graph metrics** — derived from the skills/edges/parameters/preferences
  collections via the MCP tool surface. No shell access needed.
- **Harness metrics** — derived from `db.runs` via `scripts/analyze.py`.
  No MCP tool exposes runs directly (by design — the harness measures
  the harness, separate concern).

---

## Graph metrics (via MCP tools)

| Tool | Metrics extractable |
|------|---------------------|
| `list_skills(lifecycle=)` | Skill count by lifecycle; version inventory; complete input/output type registry; parameter source presence |
| `list_skills(input_type=, output_type=)` | Skills consuming type X; skills producing type Y; chain-entry vs chain-exit nodes |
| `get_skill_contract(skill_id)` | Version constraint tightness (e.g. `>=3.0.0 <4.0.0`); declared parameter sources; ABI shape (input/output schema versions) |
| `get_skill_instructions(skill_id)` | SKILL.md line count; accessibility rule count and severity; declared dependencies and direct consumers |
| `route_task(target_output_type)` | Chain length to a target; entry skill; whether the chain is reachable at all (error if not) |
| `traverse_dependencies(skill_id)` | Downstream depth from a skill; transitive consumer count; **terminal-skill detection** (empty chain = no consumers; correct, not a bug) |
| `impact_analysis(skill_id)` | Direct consumer count; transitive downstream tree; incompatible edges involving the skill |
| `validate_chain(skill_ids)` | Chain validity boolean; type-mismatch errors; missing-dependency errors; not-active errors. Each error category accumulates separately |
| `search_skills(query, limit=)` | Skills matching a text query, ranked by relevance score |
| `get_tokens` / `get_components` / `get_layouts` | Per-tenant retrieval; `source` field tells you whether the response came from `parameters[<tenant>]` or `skill_default` |

### Derived metrics (compose multiple tools)

| Composition | Yields |
|-------------|--------|
| `list_skills() + impact_analysis(each)` | Full graph topology: roots (zero deps), leaves (zero consumers), forks (>1 consumer) |
| `list_skills() + get_skill_contract(each)` | Schema-version drift across skills; finding skills not on the latest ABI |
| `list_skills(input_type=X) + list_skills(output_type=X)` | Bottlenecks at type X; producers vs consumers count for that type |
| `route_task` for every distinct `output.type` in the graph | All canonical chains; chain-length distribution |

---

## Harness metrics (via `scripts/analyze.py`)

`db.runs` carries one document per MCP tool call. The schema:

```json
{
  "tool": "<tool name>",
  "params": { ... kwargs ... },
  "tokens_returned": <chars // 4>,
  "duration_ms": <float>,
  "session_id": "<tag>",
  "error": null | "<ExceptionClassName>",
  "timestamp": <utc ISO>
}
```

`analyze.py` exposes three views:

| Command | Metric |
|---------|--------|
| `analyze.py --list-sessions` | All session ids; per-session call count, error count, last-seen timestamp |
| `analyze.py --table blog1 [--session ID]` | Per-tool: call count, error count, avg/p50/p95 tokens returned |
| `analyze.py --table blog2 [--session ID]` | Per-session: routing-vs-retrieval ratio, error rate, total tokens |

`ROUTING_TOOLS` (counted as routing in `blog2_table`):
`route_task`, `validate_chain`, `search_skills`, `impact_analysis`,
`list_skills`, `traverse_dependencies`. Everything else counts as work.

### Raw runs queries (when analyze.py isn't enough)

```bash
mongosh skill_graph --eval 'db.runs.aggregate([
  {$group: {_id: "$tool",
            avg_ms: {$avg: "$duration_ms"},
            p95_ms: {$percentile: {input: "$duration_ms", p: [0.95], method: "approximate"}}}}
])'
```

Other extractable raw signals:
- `error` field is non-null for failed calls — error class is the exception name
- `params` carries the kwargs the tool was called with (some fields redacted per `_REDACTED_PARAMS` in `server.py`)
- `timestamp` is UTC; combine with `session_id` to reconstruct call ordering within a session

---

## Combined views (graph × harness)

| Question | How to answer |
|----------|---------------|
| Which skills are *used* most often? | Filter `db.runs` for tools that take `skill_id` (`get_skill_contract`, `get_skill_instructions`, `get_tokens`, `get_components`, `impact_analysis`, `traverse_dependencies`); group by `params.skill_id` |
| Which skill-graph features are dormant? | Cross-reference `list_skills()` output with `runs` distinct skill_ids — skills that exist but were never touched |
| Did a skill change cause increased error rate? | Compare blog1 error rates before vs after the change (filter by date range on `runs.timestamp`) |
| Are descriptions doing disambiguation work? | Look for `search_skills` calls that succeeded vs returned-nothing in the same session; high success rate = descriptions match user intent |

---

## What's *not* observable

Honest gaps in current observability:

- **Token cost on the input side.** `tokens_returned` measures the response payload only. The agent's prompt/context tokens that triggered the call are not captured. To get full per-call cost you'd need to instrument at the MCP host, not the server.
- **Tool selection bias.** The runs corpus shows what was called, not what *could have been* called. If the agent chose `Read` over `get_skill_instructions`, the runs corpus shows zero `get_skill_instructions` calls for that prompt — looks like the tool was discoverable and rejected when really it wasn't reached. F9 had to be diagnosed by reading transcripts.
- **Cross-skill latency.** Each call's `duration_ms` is per-call; chain-execution latency (route_task → validate → execute → analyze) is not directly logged.
- **Cache effects.** When the agent reuses prior context to answer a prompt with zero new tool calls (R1 prompt 6, R3 prompt 15), the runs corpus undercounts the work. F3 documents this.

---

## Pointers

- `notes/r1-results.md`, `notes/r2-leafygreen.md`, `notes/r3-airtight.md` (gitignored) — three real measurement runs using these metrics
- `scripts/analyze.py` — source of truth for the analyze tables
- `skills/harness/SKILL.md` — operating procedures for the harness itself
