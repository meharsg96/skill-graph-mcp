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

## Emitting hook config from constraints (v2.11.0)

The `db.constraints` collection holds Layer 2 semantic rules for
artifact validation (e.g. LeafyGreen accessibility, ui-builder layout).
A subset of those constraints — anything under `skill:claude-code:*` —
describes runtime behavior of Claude Code itself, and can be translated
deterministically into a `settings.json` `hooks` fragment.

The pipeline:

```
db.constraints  ──► scripts/emit_hooks.py  ──► .claude/hooks.generated.json
                                                       │
                              merge into .claude/settings.local.json
                                                       │
              Claude Code at session start reads hooks
                                                       │
PreToolUse / SubagentStart / UserPromptExpansion fire ─►
              scripts/hooks/check_constraint.py
                          │
        looks up the constraint in db.constraints,
        evaluates the per-constraint matcher,
        returns {outcome: allow|ask|deny, reason: rule_text}
```

**Generate the fragment:**

```bash
python scripts/emit_hooks.py --output .claude/hooks.generated.json
python scripts/emit_hooks.py --print            # stdout, no file
```

The output shape is `{hooks: [...], _meta: {...}}`. Merge the `hooks`
array into `.claude/settings.local.json`'s `hooks` block. `_meta.skipped`
lists every claude-code constraint that has no entry in the script's
`MATCHERS` dispatch table — surfaced rather than silently dropped.

**Severity → outcome mapping:**

| Constraint `severity` | Hook `outcome` | Effect in Claude Code |
|---|---|---|
| `fail` | `deny` | Block the call, show `rule_text` as reason |
| `warn` | `ask` | Surface to user with `rule_text`, await confirm |
| `note` | `allow` | Log only (informational) |

**Design rule: deny-only enforcement.** The runtime hook never returns
`updatedInput` — it does not silently modify tool calls, even when a
"safer" rewrite would be possible. Silent input rewriting masks intent;
block-with-explanation is the agreed contract. If you need to rewrite
a call, write the constraint as `warn` (→ `ask`) so the user can
re-issue the corrected form themselves.

**Adding a new claude-code constraint:**

1. Append a constraint document to `CONSTRAINTS_SEED` in `scripts/seed.py`
   — `_id` starts with `constraint:claude-code:<skill-slug>:<rule>`,
   `skill_id` references the relevant `skill:claude-code:*` node.
2. Add a corresponding entry to `MATCHERS` in `scripts/emit_hooks.py`
   keyed by the same `_id`. The matcher is a pre-filter only (e.g.
   `Bash(*--no-verify*)`); the real evaluation lives in
   `scripts/hooks/check_constraint.py`.
3. Add a per-constraint evaluator function to `EVALUATORS` in
   `scripts/hooks/check_constraint.py` that returns `True` when the
   tool input matches the violation pattern.
4. Reseed: `python scripts/seed.py`.
5. Re-emit: `python scripts/emit_hooks.py --output .claude/hooks.generated.json`.
6. Merge the new entry into your live `settings.local.json`.

A constraint with steps 1 + 2 but no `EVALUATORS` entry will fail-open
at runtime (returns `outcome: allow` with a stderr warning). Useful for
constraints whose violation pattern can't be detected from the hook
payload alone — emit the matcher as advisory, evaluator returns False.

**Failure modes** (all fail-open by design — the hook must never block
tool calls due to infrastructure issues):
- `pymongo` not on the hook's PATH → outcome=allow, stderr warning
- MongoDB unreachable (2s timeout) → outcome=allow, stderr warning
- Constraint not found in `db.constraints` → outcome=allow
- Stdin payload malformed JSON → outcome=allow
- `CONSTRAINT_ID` env var missing → outcome=allow

**Cross-machine policy distribution:** the constraint set in MongoDB +
the `MATCHERS` dispatch in `emit_hooks.py` together form the deployable
artifact. Clone the repo, `seed.py`, `emit_hooks.py`, merge — every
contributor gets identical guardrails. The constraints corpus is the
team's safety policy, version-controlled as code.

**Audit log (v2.11.x):** the runtime hook script writes one row to
`db.runs` per matched constraint, so you can ask "which constraints
fired in session X?" via `analyze.py`. Schema:

```json
{
  "tool": "hook:check_constraint",
  "params": {"constraint_id": "...", "tool_name": "Bash"},
  "outcome": "deny",
  "matched": true,
  "session_id": "session:..."
}
```

Default behavior logs only matches. Set `HOOK_AUDIT=1` in the hook env
block to log every invocation including allows — useful for drift
analysis ("constraint X never fires in practice; consider tightening
the matcher").

---

## Local-only constraints (v2.11.x)

You can ship personal-only constraints via the local-overlay path
(see "Local-only skills" above). Drop a `constraints.json` into
`$SKILL_GRAPH_LOCAL_DIR/`:

```json
{
  "constraints": [
    {
      "_id": "constraint:local:cadenza:no-prod-deploy-friday",
      "skill_id": "skill:claude-code:tool:bash",
      "rule_text": "Never deploy to cadenza prod on Fridays.",
      "violation_paraphrase": "kubectl apply or deploy.sh targeting prod environment with weekday=Friday",
      "examples": {
        "violating": "./deploy.sh prod  (run on Friday)",
        "compliant": "./deploy.sh staging"
      },
      "constraint_embedding": null,
      "severity": "fail",
      "category": "safety",
      "hook_config": {
        "event": "PreToolUse",
        "if": "Bash(*deploy*prod*)",
        "evaluator": {
          "type": "regex_in_field",
          "field": "command",
          "pattern": "deploy.*prod"
        }
      }
    }
  ]
}
```

**Hard guards** (enforced at seed time):
- `_id` must start with `constraint:local:` — canonical-namespace local
  constraints are rejected by `seed.py:_load_local_overlay`
- `skill_id` MAY reference canonical skills (e.g., `skill:claude-code:tool:bash`).
  This is intentional — the local namespace tags the *author* of the
  constraint, not the *target*. You can add personal rules to public skills.

**Inline evaluator types** (no Python code needed):

| Type | Behavior | Use when |
|---|---|---|
| `regex_in_field` | `re.search(pattern, tool_input[field], IGNORECASE)` | Most common — match a regex against a tool input field |
| `always_match` | Trust the matcher entirely; deny every reaching call | The Claude Code `if` matcher is sufficient on its own |
| `never_match` | Never matches; outcome is always allow | Audit-only — observe pattern frequency before enforcing |

For evaluators more complex than regex (e.g., date/time logic, AST
parsing), add a Python entry to `EVALUATORS` in `check_constraint.py`
keyed by your local constraint_id. The runtime falls back to that
EVALUATORS lookup when no inline evaluator is set.

**Workflow:**

```bash
# 1. Edit your local constraints file
vim ~/.skill-graph-local/constraints.json

# 2. Reseed (your local constraints upserted into db.constraints)
python scripts/seed.py

# 3. Re-emit hooks (your local entries appear in the fragment)
python scripts/emit_hooks.py --output .claude/hooks.generated.json

# 4. Merge into Claude Code settings (idempotent)
python scripts/merge_settings.py
```

**Optional: Layer 2 vector search on local constraints.** If you also
run `seed_constraint_embeddings.py`, your local constraints get
voyage-4 embeddings and are queryable via `check_constraints` —
useful for fuzzy semantic checks ("is this command violating any of
my personal rules?") without requiring a regex pre-filter to fire.

---

## Querying the claude-code subgraph

The 19 `skill:claude-code:*` nodes are visible to all the standard
graph tools:

```python
list_skills()                         # all 33 active skills, including claude-code
list_skills(input_type="task_description")
                                       # claude-code:agent:general-purpose, :plan, etc.
search_skills("subagent")             # ranked by relevance to subagent-related text
get_skill_contract("skill:claude-code:tool:bash")
                                       # → full ABI shape, dangerous_patterns, etc.
get_skill_instructions("skill:claude-code:tool:agent")
                                       # → skills/claude-code/SKILL.md content
traverse_dependencies("skill:claude-code:agent:explore")
                                       # → walks back to permission-mode root
impact_analysis("skill:claude-code:permission-mode")
                                       # → all 18 downstream tool/agent skills
route_task("read-only codebase exploration")
                                       # → resolves to claude-code:agent:explore chain
```

Use these instead of reading `skills/claude-code/SKILL.md` or
`schema/skills.json` directly — the graph path is strictly more
informative (returns related skills, version, lifecycle, source
provenance).

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
