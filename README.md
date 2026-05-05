# Typed Skill Graphs for LLM Orchestration with MongoDB

A minimal template for building typed skill parameter retrieval and composition validation using MongoDB and `$graphLookup`.

**The pattern**: Store skill contracts (input types, output types, lifecycle states, dependencies) and parameterized configuration as MongoDB documents. Use `$graphLookup` to traverse dependency chains and validate composition before execution. Expose the graph through semantic MCP tools, not raw database access.

![Architecture: from skill files to typed graph queries](images/architecture-evolution.svg)

Read the companion blog post: *[Typed Skill Graphs for LLM Orchestration with MongoDB]*

## Quick start

```bash
git clone https://github.com/meharsg96/skill-graph-mcp.git
cd skill-graph-mcp
git checkout v1                         # pin to the Blog 1 release

# Requires: MongoDB 7.x+ running locally, Python 3.10+
cp .env.example .env                    # MONGODB_URI defaults to mongodb://localhost:27017
pip install -r requirements.txt

python scripts/seed.py                  # Seed the database
python scripts/validate.py              # Run validation examples
python server.py                        # Start the MCP server

# Or use mongosh
mongosh < scripts/queries.js
```

## What's in the box

```
skill-graph-mcp/
├── server.py              # MCP server — the graph gateway (6 tools, instrumented)
├── schema/
│   └── skills.json        # 6 skills (5 active, 1 inactive) + 5 edges
├── scripts/
│   ├── seed.py            # Seeds MongoDB with schema validation; preserves runs collection
│   ├── validate.py        # Plan validation with $graphLookup
│   └── queries.js         # mongosh examples
├── tests/                 # pytest smoke + bug-fix invariants
├── docs/ARCHITECTURE.md   # the load-bearing invariants
└── README.md
```

## The key query

`$graphLookup` traverses the dependency chain, filtering by lifecycle during recursion:

```javascript
db.skills.aggregate([
  { $match: { _id: "skill:query-analysis" } },
  { $graphLookup: {
      from: "skills",
      startWith: "$_id",
      connectFromField: "_id",
      connectToField: "dependencies",
      as: "downstream",
      maxDepth: 10,
      depthField: "depth",
      restrictSearchWithMatch: { lifecycle: "active" }
  }}
])
```

Inactive skills never appear in results. Type mismatches are caught before execution.

![$graphLookup traversal with lifecycle filtering](images/graphlookup-validation.svg)

## MCP server

`server.py` exposes six tools backed by MongoDB. These are semantic graph operations, not raw database access:

```
get_skill_contract(skill_id)           # base contract for a skill
get_tokens(skill_id, theme)            # design tokens, filtered
get_components(skill_id, category)     # domain fields, filtered
get_layouts(skill_id, breakpoint)      # layout config, filtered
validate_chain(skill_ids)              # type + lifecycle + dependency check
traverse_dependencies(skill_id)        # $graphLookup dependency closure
```

To connect to Claude Code:

```bash
claude mcp add skill-graph python server.py
```

## Instrumentation

Every tool call is logged to `db.runs` automatically — one document per call with
`tool`, `params`, `tokens_returned`, `duration_ms`, `session_id`, and `error`.

`SESSION_ID` is captured once at server startup. If unset, the server falls back
to `session:auto-<pid>-<epoch>` so calls within one server process still group.
Standalone runs:

```bash
export SESSION_ID="session:$(date +%s)"
python server.py
```

When the server is launched by an MCP host (e.g. `claude mcp add`), MCP's stdio
transport does **not** forward parent env vars by default — you must list them
explicitly in the host's MCP server config (`env: { "SESSION_ID": "..." }`).
Without that, the auto fallback gives you per-process grouping for free.

The `runs` collection is created lazily and **never dropped on re-seed**, so
historical sessions accumulate. Documents expire after 90 days via a TTL index.

## Series

| Tag | Blog | Adds |
|-----|------|------|
| [`v1`](https://github.com/meharsg96/skill-graph-mcp/tree/v1) | Your Agent Reads Around Your Skill Files | typed skill graph, 6 tools |
| `v2` *(in progress)* | Agent Skills Need Contracts, Not Just Descriptions | ABI shape, routing, impact analysis, tenant params |
| `v3` *(planned)* | TBD | artifact validation + repair |

## License

MIT
