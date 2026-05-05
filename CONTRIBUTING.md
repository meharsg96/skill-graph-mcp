# Contributing

Thanks for the interest. This is a teaching template that follows the blog
series; structural changes should track the series, not race ahead of it.

## Dev setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Or with `uv`:
```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements-dev.txt
```

Every `python …` command in the docs assumes the venv is activated, or that
you're using `./venv/bin/python …` or `uv run python …` explicitly. A bare
`python` without an activated env misses pymongo and fastmcp. (`uv python`
is the install-management subcommand, not the runner — use `uv run python`.)

Tests need either Docker (for `testcontainers`) or a local Mongo on
`mongodb://localhost:27017`. If `MONGODB_URI` is set in the environment, the
test suite uses that instead of starting a container — useful for CI and for
local runs without Docker.

```bash
ruff check .
MONGODB_URI=mongodb://localhost:27017 pytest -q                      # 39 tests (16 v1 + 23 v2)
python scripts/seed.py && python scripts/validate.py                 # v1 smoke
python scripts/route.py ui_components                                # v2 smoke
python scripts/impact.py skill:schema-review                         # v2 smoke
python scripts/analyze.py --all                                      # render the blog tables
```

End-to-end through the real MCP wire (recommended before tagging):

```bash
SESSION_ID=session:dev scripts/run_session.sh python scripts/mcp_host.py route_task ui_components
python scripts/analyze.py --session session:dev --all
```

## PR conventions

- One concern per PR. The repo evolves through tagged releases; keep changes
  small enough to land cleanly on the next tag.
- Don't add tools to `server.py` without a docstring stating return shape and
  the lifecycle invariant (every read filters `lifecycle: "active"`).
- Don't drop the `runs` collection on re-seed.
- New external runtime dependencies need justification in the PR body.
- Adding a tool? Write the test in `tests/test_<tool>.py` using the `seeded`
  and `call` fixtures from `conftest.py`. Don't import across test files.
- Touching the schema? Update `seed.py` derivation, add a `tests/test_v1_compat.py`
  case if the change is observable to v1 callers.
