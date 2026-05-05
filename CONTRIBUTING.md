# Contributing

Thanks for the interest. This is a teaching template that follows the blog
series; structural changes should track the series, not race ahead of it.

## Dev setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Tests need either Docker (for `testcontainers`) or a local Mongo on
`mongodb://localhost:27017`. If `MONGODB_URI` is set in the environment, the
test suite uses that instead of starting a container — useful for CI and for
local runs without Docker.

```bash
ruff check .
MONGODB_URI=mongodb://localhost:27017 pytest -q
python scripts/seed.py && python scripts/validate.py
```

## PR conventions

- One concern per PR. The repo evolves through tagged releases; keep changes
  small enough to land cleanly on the next tag.
- Don't add tools to `server.py` without a docstring stating return shape and
  the lifecycle invariant (every read filters `lifecycle: "active"`).
- Don't drop the `runs` collection on re-seed.
- New external runtime dependencies need justification in the PR body.
