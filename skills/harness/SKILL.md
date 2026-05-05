---
name: harness
description: >
  How to operate the skill-graph MCP server itself — registering it, capturing
  measurement data, analyzing tool-call cohorts, archival, scope tradeoffs,
  and the small set of pitfalls that bite real users. Trigger when the agent
  is asked "how do I use this MCP server", "how do I tag a session", "where
  are my runs", "what do these analyze.py columns mean", or any question
  about operating the skill-graph rather than building artifacts with it.
---

# Skill Graph Harness

Self-documenting meta-skill. The skill-graph evangelizes parameterized
skills with typed contracts; this skill applies the same pattern to
the system's own operating instructions. The system documents itself
using its own architecture.

---

## Registering the server

```bash
cd <skill-graph-mcp checkout>

# Per-directory registration (only this repo's sessions see it)
claude mcp add skill-graph "$(pwd)/venv/bin/python" "$(pwd)/server.py" \
  -e MONGODB_URI=mongodb://localhost:27017

# User-scope registration (every Claude Code session, anywhere)
claude mcp add --scope user skill-graph \
  "$(pwd)/venv/bin/python" "$(pwd)/server.py" \
  -e MONGODB_URI=mongodb://localhost:27017
```

User scope is the right ergonomics for daily use. Three caveats:

1. The absolute paths bake in. Move the repo and the registration
   breaks silently. Put the repo somewhere stable (e.g.
   `~/code/skill-graph-mcp`) before user-scope registration.
2. MongoDB must be running. If Mongo's down, every Claude Code session
   tries to spawn the server and the MCP shows as unavailable.
3. Don't fix `SESSION_ID` at user scope (see below).

---

## SESSION_ID conventions

`SESSION_ID` tags every tool call so you can query a cohort later via
`analyze.py --session <id>`.

**Default behavior (no SESSION_ID set):** server falls back to
`session:auto-<pid>-<unix-epoch>` per server-process lifetime. Calls
within one server process group naturally; restarting Claude Code
gives you a new session id.

**Tagged behavior (SESSION_ID set in MCP env):** every tool call lands
in that cohort. Tagged sessions are how blog measurement runs work
(R1, R2, R3 used `session:r1-…`, `session:r2-…`, `session:r3-…`).

When to tag explicitly:
- Measurement runs (you want one filterable cohort)
- A substantial build session you'll analyze later
- Comparing two configurations side-by-side

When NOT to tag:
- Daily ambient use (the auto-fallback gives per-server-process
  grouping for free)
- User-scope registration with a fixed tag (every session goes to
  the same cohort, defeating the per-session filter)

To tag a build session:

```bash
BUILD_ID="session:build-$(date +%Y%m%d)-myproject"
claude mcp remove skill-graph
claude mcp add --scope user skill-graph \
  "$(pwd)/venv/bin/python" "$(pwd)/server.py" \
  -e MONGODB_URI=mongodb://localhost:27017 \
  -e SESSION_ID=$BUILD_ID
echo $BUILD_ID > ~/.last-build-session
```

Restart Claude Code so the new env takes effect. After the build,
`claude mcp remove && claude mcp add ...` without the `SESSION_ID`
to return to ambient-auto behavior.

---

## Analyzing runs

```bash
# What sessions exist? (sorted most-recent-first)
python scripts/analyze.py --list-sessions

# Cohort detail for one session
python scripts/analyze.py --session session:foo --all

# Just one table
python scripts/analyze.py --session session:foo --table blog1
python scripts/analyze.py --session session:foo --table blog2
```

`blog1` is per-tool token efficiency (calls, errors, avg/p50/p95
tokens returned). `blog2` is per-session routing-vs-retrieval ratio.
`ROUTING_TOOLS` are: `route_task`, `validate_chain`, `search_skills`,
`impact_analysis`, `list_skills`, `traverse_dependencies`. Everything
else counts as work.

`measure_baseline.py` models the cost of answering the same prompts
via direct file reads instead of MCP queries. Useful for blog evidence:

```bash
python scripts/measure_baseline.py --r1-tokens <total_from_blog2>
```

---

## TTL and retention

`db.runs` has a 90-day TTL index on `timestamp`. Documents older than
that are removed automatically by Mongo's TTL monitor (within ~60s of
expiry).

Adjust the TTL in `scripts/seed.py` (`RUNS_TTL_SECONDS`) if you need
longer retention. Re-running seed updates the index but does NOT
delete existing runs (the runs collection is preserved across
re-seeds; only `skills`, `edges`, `parameters` are dropped).

---

## Archival

To preserve a session's runs beyond the TTL:

```bash
mongodump --db=skill_graph --collection=runs \
  --query='{"session_id": "session:build-20260506-myproject"}' \
  --out=~/skill-graph-archive/build-myproject
```

Restore later with:

```bash
mongorestore --db=skill_graph --collection=runs \
  ~/skill-graph-archive/build-myproject/skill_graph/runs.bson
```

---

## Common pitfalls

**`uv python scripts/foo.py` fails with `unrecognized subcommand`.**
`uv python` is the install-management subcommand. The runner is
`uv run python scripts/foo.py`.

**`ModuleNotFoundError: pymongo` from a fresh shell.** Either activate
the venv (`source venv/bin/activate`) or invoke the venv's python
directly (`./venv/bin/python ...`).

**Tagged SESSION_ID doesn't propagate from `claude mcp add` env.**
Restart Claude Code. The MCP host reads the env block at server-spawn
time; an existing server process keeps its old env.

**`get_tokens` / `get_components` returns "no design tokens" or
"no components" with no path forward.** v2.2.1 fixed the dead-end
errors — they now include `available_tenants` and a hint at the
right next call. Upgrade to v2.2.1+ if you see the old form.

**Routing-ratio undercounting `list_skills` as work.** Fixed
post-v2.2.0; ROUTING_TOOLS now includes `list_skills` and
`traverse_dependencies`. Pre-fix sessions reanalyzed will show the
corrected ratio.

---

## Related skills

- `skill:query-analysis`, `skill:schema-review`, `skill:code-gen`,
  `skill:ui-builder`, `skill:test-writer` — the core application chain
- `skill:leafygreen-ui` — MongoDB design system as a parameterized skill
- `skill:react-test-writer` — closes LeafyGreen's downstream test path

This skill (`skill:harness`) is independent — no dependencies, no
downstream consumers. It exists to be queried, not chained into
production work.
