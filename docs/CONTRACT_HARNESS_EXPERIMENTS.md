# Contract harness — reproducible experiments

The eval-against-contract harness lives at `tests/test_contract_fixtures.py`.
It validates every fixture under `tests/fixtures/contracts/` against the
JSON Schema files under `schema/contracts/` keyed by each skill's declared
`input.schema` / `output.schema` identifiers.

This page documents three experiments that demonstrate what the harness
catches and how the failures localize. Each one is a single change to a
tracked file, run, then revert. Nothing here is committed.

Prereqs:

```bash
pip install -r requirements-dev.txt
# MongoDB 7.x running on localhost:27017
```

Baseline (all green):

```bash
MONGODB_URI=mongodb://localhost:27017 pytest tests/test_contract_fixtures.py -q
# 3 passed in ~2s
```

---

## Experiment 1 — interface drift (the Tuesday/Friday incident)

Simulate a `schema-review` skill still emitting v1-shape output (no
`indexes`, no `anti_patterns`) while its declared output schema is v2.

```bash
cp tests/fixtures/contracts/skill_schema-review/case-001/output.json /tmp/sr-out-good.json

cat > tests/fixtures/contracts/skill_schema-review/case-001/output.json <<'EOF'
{
  "collection": "users",
  "shape": {
    "_id": "ObjectId",
    "email": "string",
    "name": "string",
    "created_at": "date"
  }
}
EOF

MONGODB_URI=mongodb://localhost:27017 pytest tests/test_contract_fixtures.py -q

# Revert
cp /tmp/sr-out-good.json tests/fixtures/contracts/skill_schema-review/case-001/output.json
```

**Expected result:** 2 failures, 1 pass.

```
FAILED tests/test_contract_fixtures.py::test_every_fixture_validates_against_declared_contracts
FAILED tests/test_contract_fixtures.py::test_chain_edges_are_wireable

ValidationError: 'indexes' is a required property
Failed validating 'required' in schema:
  'required': ['collection', 'shape', 'indexes', 'anti_patterns']
```

Both the per-skill test and the chain-edge test fire in the same run.
The per-skill test says "this fixture is wrong"; the chain-edge test
says "and this is why code-gen is about to break." This is the
Tuesday/Friday incident captured in milliseconds.

---

## Experiment 2 — wrong-type field, right contract

Corrupt an enum field so type validation triggers without removing
any required keys.

```bash
cp tests/fixtures/contracts/skill_schema-review/case-001/input.json /tmp/sr-in-good.json

python -c "
import json
p='tests/fixtures/contracts/skill_schema-review/case-001/input.json'
d=json.load(open(p))
d['queries'][0]['frequency']=42
json.dump(d,open(p,'w'),indent=2)
"

MONGODB_URI=mongodb://localhost:27017 pytest tests/test_contract_fixtures.py -q

# Revert
cp /tmp/sr-in-good.json tests/fixtures/contracts/skill_schema-review/case-001/input.json
```

**Expected result:** 1 failure, 2 passes.

```
FAILED tests/test_contract_fixtures.py::test_every_fixture_validates_against_declared_contracts

ValidationError: 42 is not of type 'string'
Failed validating 'type' in schema['properties']['queries']['items']['properties']['frequency']
On instance['queries'][0]['frequency']
```

Localization is byte-level: file, array index, field name, expected
type, actual value. This is the differentiator vs behavioral evals,
which usually report "output is wrong" without telling you which byte.

---

## Experiment 3 — schema tightening, blast radius

Add a new required field to `schema-recommendation.v2` and see how
many fixtures need updating.

```bash
cp schema/contracts/schema-recommendation.v2.json /tmp/v2-good.json

python -c "
import json
p='schema/contracts/schema-recommendation.v2.json'
d=json.load(open(p))
d['required'].append('partition_strategy')
d['properties']['partition_strategy']={'type':'string','enum':['hashed','ranged','none']}
json.dump(d,open(p,'w'),indent=2)
"

MONGODB_URI=mongodb://localhost:27017 pytest tests/test_contract_fixtures.py -q

# Revert
cp /tmp/v2-good.json schema/contracts/schema-recommendation.v2.json
```

**Expected result:** the per-skill test fails on every fixture using
`schema:schema-recommendation:v2` — schema-review's output AND
code-gen's input simultaneously.

```
ValidationError: 'partition_strategy' is a required property
```

Before merging a schema tightening, you see every fixture you will
need to update in one test run. This is `impact_analysis` in eval
form: forward `$graphLookup` walks dependencies; contract
re-validation walks fixtures.

---

## What the harness does NOT do (yet)

- It tests fixtures, not LLM output. A contract being satisfied by
  the seeded fixture does not prove the agent will produce
  schema-conformant output at runtime.
- It covers 3 skills out of 9 active. Adding a new skill to the
  harness is a manual JSON Schema + 1-2 fixture pairs. No generator
  yet.
- Schema identifiers are resolved by string convention
  (`schema:<name>:<version>` -> `contracts/<name>.<version>.json`).
  No formal registry.

These are scoped limitations, not bugs. The harness exists to catch
interface drift between adjacent skills before it surfaces as
runtime failure two steps downstream. That single class of failure is
exactly what evals miss and what every shipped change to a typed
output schema should be checked against.
