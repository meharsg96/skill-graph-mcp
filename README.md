# Typed Skill Graphs for LLM Orchestration with MongoDB

A minimal template for building typed skill parameter retrieval and composition validation using MongoDB and `$graphLookup`.

**The pattern**: Store skill contracts (input types, output types, lifecycle states, dependencies) and parameterized configuration as MongoDB documents. Use `$graphLookup` to traverse dependency chains and validate composition before execution. Expose the graph through semantic MCP tools, not raw database access.

Read the companion blog post: *[Typed Skill Graphs for LLM Orchestration with MongoDB]*

## Quick start

```bash
git clone https://github.com/meharsg96/skill-graph-mcp.git
cd skill-graph-mcp

# Requires: MongoDB 7.x+ running locally, Python 3.10+
pip install pymongo fastmcp

python scripts/seed.py       # Seed the database
python scripts/validate.py   # Run validation examples
python server.py             # Start the MCP server

# Or use mongosh
mongosh < scripts/queries.js
```

## What's in the box

```
skill-graph-mcp/
├── server.py              # MCP server — the graph gateway
├── schema/
│   └── skills.json        # 6 skills (5 active, 1 inactive) + 5 edges
├── scripts/
│   ├── seed.py            # Seeds MongoDB with schema validation
│   ├── validate.py        # Plan validation with $graphLookup
│   └── queries.js         # mongosh examples
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

## License

MIT
