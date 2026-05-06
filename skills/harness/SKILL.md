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

## Local-only skills

Beyond the canonical skills shipped in this repo, individual users may
load additional skills that are local to their machine — never
committed, never synchronized, never visible to anyone reading the
public graph.

**Mechanism:** set `SKILL_GRAPH_LOCAL_DIR` (default
`~/.skill-graph-local/`) to point at a directory containing the same
shape as `schema/` (`skills.json`, `parameters.json`,
`preferences.json`, plus a `skills/local/<slug>/SKILL.md` body per
skill). `seed.py` reads from there *after* the canonical seed and
upserts each doc by `_id` (idempotent).

**Hard guards** at seed time:
- All local skill `_id` values must start with `skill:local:` —
  canonical namespaces in the local overlay are rejected
- Local parameter docs must reference local skills
- Local preferences with `scope=skill` must apply to a local skill

**Path resolution:** `skill_path` for local skills can be absolute
(typically `~/.skill-graph-local/skills/local/<slug>/SKILL.md`).
`get_skill_instructions` accepts paths under either trusted root —
the repo root or `LOCAL_DIR` — and rejects everything else as a
traversal attempt.

**Why outside the repo:** the canonical location for `LOCAL_DIR` is
*outside* the git working tree. This makes git leakage physically
impossible — `git add` from inside the repo cannot see files at
`~/.skill-graph-local/`. Defense-in-depth `.gitignore` entries cover
the case where local content is accidentally placed inside the repo.

For details on populating `LOCAL_DIR`, consult your own setup notes —
local content is, by design, not documented in this repo.

---

## Common misreadings

Real ones observed in agent sessions; not bugs, but easy to mistake for bugs.

**Empty `chain` from `traverse_dependencies` on terminal skills.** Tools
like `skill:test-writer`, `skill:ui-builder`, `skill:react-test-writer`,
`skill:harness` produce outputs nothing else consumes — they're graph
leaves. `traverse_dependencies` walks *downstream* (consumers of this
skill's output), so an empty chain is the correct answer for any leaf.
Cross-check: `impact_analysis(skill_id).direct_consumers == []`.

**`route_task` returning "No active skill produces X" for a known type.**
The most common cause is a stale server process that started before
the schema migrated to the v2 ABI shape (where `output.type` is the
nested field route_task matches against). Restart Claude Code so the
user-scope MCP server respawns; the apparent failure goes away.
Other causes: typo in the type name, querying with `input_type`
instead of `output_type`, or the type genuinely has no producer.

**`get_skill_instructions` errors with "instruction file not present".**
The `skill_path` field references a markdown file that ships with some
skills (harness, leafygreen-ui) and not others (test-writer,
ui-builder are placeholders). For skills without a body, the response
still carries `accessibility_rules` and `related` — use those.

**Apparent disagreement between two tools for the same skill.**
`list_skills` shows declarative metadata; `impact_analysis` walks the
dependency graph; `get_skill_instructions` reads the markdown body.
If `list_skills` shows a skill but `get_skill_instructions` errors,
the skill is registered but its body file isn't present — that's
what the error shape tells you. Different tools surface different
projections of the same skill.

**Routing-ratio looks wrong after a tool addition.** `ROUTING_TOOLS`
must include any new discovery/navigation tool. Without that, a new
tool gets bucketed as "work" and the ratio drifts. Fixed cases so
far: v2.1.x added `list_skills` to ROUTING_TOOLS;
`traverse_dependencies` was added at the same time. If you add a
new routing/discovery tool, update `scripts/analyze.py` ROUTING_TOOLS
and the pin test in `tests/test_analyze.py`.

---

## Related skills

- `skill:query-analysis`, `skill:schema-review`, `skill:code-gen`,
  `skill:ui-builder`, `skill:test-writer` — the core application chain
- `skill:leafygreen-ui` — MongoDB design system as a parameterized skill
- `skill:react-test-writer` — closes LeafyGreen's downstream test path

This skill (`skill:harness`) is independent — no dependencies, no
downstream consumers. It exists to be queried, not chained into
production work.
