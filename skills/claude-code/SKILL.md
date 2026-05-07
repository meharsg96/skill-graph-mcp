---
name: claude-code
description: >
  The Claude Code CLI runtime modeled as a typed skill graph. Covers permission
  modes (auto/ask/manual), model selection (Opus/Sonnet/Haiku + fast mode),
  and every built-in tool (Bash, Read, Edit, Write, Agent, MCP, Web).
  Trigger when the agent is asked about what Claude Code can do, which model
  to use, what a permission mode means, or which tool to reach for.
---

# Claude Code Skill Graph

Claude Code's runtime capabilities expressed as typed, versioned contracts.
The same architecture this repo uses to model LeafyGreen or a MongoDB schema
chain applies to the CLI itself — permission modes gate tool access, model
selection gates agent spawning, tool outputs are typed inputs for the next tool.

---

## Permission modes

| Mode   | Default? | Tool calls auto-approved | Danger ops require confirm |
|--------|----------|--------------------------|----------------------------|
| auto   | No       | All                      | Still — git push, rm -rf   |
| ask    | Yes      | Read-only (Read, Glob)   | Yes                        |
| manual | No       | None                     | Yes                        |

Set mode per-session: `/permissions` in the REPL, or `--permission-mode auto`
on the CLI. Stored in `settings.json` under `permissionMode`.

Destructive operations (force-push to main, `rm -rf`, `--no-verify`) need
explicit user confirmation regardless of mode.

---

## Model availability matrix

| Model ID                        | Family  | Context | Fast mode |
|---------------------------------|---------|---------|-----------|
| claude-opus-4-7                 | Opus 4  | 200K    | Yes (Opus 4.6 base) |
| claude-sonnet-4-6               | Sonnet 4| 200K    | No        |
| claude-haiku-4-5-20251001       | Haiku 4 | 200K    | No        |

**Fast mode**: toggled with `/fast`; uses Opus 4.6 with faster token
generation. Only available on Opus 4.7 sessions. Does NOT downgrade model
family.

**Default model**: claude-sonnet-4-6 in new sessions unless overridden via
`--model` flag or `model` in `settings.json`.

---

## Tool capability matrix

| Tool        | Skill ID                          | Requires permission | Side-effectful |
|-------------|-----------------------------------|---------------------|----------------|
| Bash        | skill:claude-code:tool:bash       | ask/auto            | Yes            |
| Read        | skill:claude-code:tool:read       | ask/auto            | No             |
| Edit        | skill:claude-code:tool:edit       | ask/auto            | Yes            |
| Write       | skill:claude-code:tool:write      | ask/auto            | Yes            |
| Agent       | skill:claude-code:tool:agent      | ask/auto + model    | Yes            |
| MCP tools   | skill:claude-code:tool:mcp        | ask/auto + server   | Varies         |
| WebSearch   | skill:claude-code:tool:web        | ask/auto            | No             |
| WebFetch    | skill:claude-code:tool:web        | ask/auto            | No             |

Read tool supersedes `cat`/`head`/`tail` — always prefer it.
Edit tool requires a prior Read in the same conversation.

---

## Skill graph structure

```
permission-mode ──┬──► tool:bash
                  ├──► tool:read ──► tool:edit
                  ├──► tool:write
                  ├──► tool:web
                  ├──► tool:mcp
                  ├──► hooks
                  └──► tool:agent ◄── model-selection
                                  ◄── permission-mode

model-selection ──► slash-commands
```

`permission-mode` is the root of the tool dependency chain.
`model-selection` gates agent spawning and fast-mode availability.
`tool:edit` depends on `tool:read` (read before edit invariant).

---

## Slash commands

| Command     | Effect                                      | Requires             |
|-------------|---------------------------------------------|----------------------|
| /help       | Show help overview                          | —                    |
| /clear      | Clear conversation context                  | —                    |
| /fast       | Toggle fast mode (Opus 4.6 faster output)   | Opus 4.7 session     |
| /compact    | Compact conversation with summary           | —                    |
| /permissions| Show/edit permission settings               | —                    |
| /review     | Code review of staged changes               | git repo             |
| /ultrareview| Multi-agent cloud review (billed)           | git repo + remote    |

---

## Hooks system

Hooks execute shell commands in response to tool-call events:
- `PreToolUse` — runs before a tool call; non-zero exit blocks the call
- `PostToolUse` — runs after a tool call; receives tool output in env
- `Notification` — fires on agent notifications
- `Stop` — fires when agent loop ends

Configure in `settings.json` under `hooks`. Hook failures are surfaced as
blocked tool messages — treat them as user-side configuration, not code bugs.

---

## Safe vs. dangerous operations

### Always safe (no confirm needed in any mode)
- Read, Glob, Grep (read-only)
- WebSearch, WebFetch (no side effects, no publication)

### Requires confirm in auto mode
- `git push` (affects shared state)
- `git push --force` (destructive, especially to main)
- `rm -rf` (irreversible)
- `--no-verify` on git commands (bypasses hooks)
- Creating/closing PRs or issues (visible to others)
- Sending messages to Slack, email, external services

### Never allowed without explicit user instruction
- Force-push to main/master
- Amending published commits
- Dropping database tables in non-ephemeral environments

---

## MCP server integration

Register skill-graph-mcp:
```bash
# Per-directory (only this repo)
claude mcp add skill-graph "$(pwd)/venv/bin/python" "$(pwd)/server.py" \
  -e MONGODB_URI=mongodb://localhost:27017

# User scope (all sessions)
claude mcp add --scope user skill-graph \
  "$(pwd)/venv/bin/python" "$(pwd)/server.py"
```

MCP tools inherit the same permission gating as built-in tools.
MCP server availability is checked at session start — if the server
process fails to start, tools show as unavailable for that session.

---

## Settings file locations

| Scope        | Path                                          |
|--------------|-----------------------------------------------|
| User         | `~/.claude/settings.json`                     |
| Project      | `<repo>/.claude/settings.json`                |
| Local        | `<repo>/.claude/settings.local.json` (gitignored) |

Project settings override user; local settings override project.
`CLAUDE.md` files are instructions, not settings — they affect agent behavior,
not permission/model configuration.
