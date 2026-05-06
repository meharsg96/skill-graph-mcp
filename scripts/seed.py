#!/usr/bin/env python3
"""Seed MongoDB with example skill graph data.

Drops and recreates `skills`, `edges`, `parameters`, and `preferences`
(demo state — ephemeral). Creates `runs` if missing but never drops it
(instrumentation history is preserved across re-seeds).

The skill schema is the v2 ABI shape (input/output blocks, semver
versions, dependency_constraints, parameter_sources). For v1 backward
compatibility, top-level `input_type` and `output_type` fields are
derived from `input.type`/`output.type` at insert time so existing
v1 tools keep working.

Local-only overlay (v2.5.0): if SKILL_GRAPH_LOCAL_DIR is set and the
directory exists, additional skills/parameters/preferences in that
directory are loaded *after* the canonical seed via idempotent upsert.
Local docs MUST use the `skill:local:<slug>` namespace; canonical
namespaces in the local overlay are rejected. The local directory is
expected to live OUTSIDE the repository (default ~/.skill-graph-local/);
this is the source of the leak-resistance — local content cannot enter
git from a path outside the working tree.

Environment:
    MONGODB_URI            Mongo connection string (default: mongodb://localhost:27017)
    SKILL_GRAPH_LOCAL_DIR  Optional directory of local-only overlay docs
                           (default: ~/.skill-graph-local/)
"""

import json
import os
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, WriteError

DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
SCHEMA_DIR = Path(__file__).parent.parent / "schema"
SKILLS_PATH = SCHEMA_DIR / "skills.json"
PARAMETERS_PATH = SCHEMA_DIR / "parameters.json"
PREFERENCES_PATH = SCHEMA_DIR / "preferences.json"

LOCAL_DIR = Path(
    os.environ.get("SKILL_GRAPH_LOCAL_DIR", str(Path.home() / ".skill-graph-local"))
).expanduser()
LOCAL_NAMESPACE_PREFIX = "skill:local:"

SKILL_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "input", "output", "lifecycle", "version", "dependencies"],
        "properties": {
            "_id": {"bsonType": "string"},
            "name": {"bsonType": "string"},
            "version": {"bsonType": "string"},
            "lifecycle": {"enum": ["active", "inactive"]},
            "input": {
                "bsonType": "object",
                "required": ["type"],
                "properties": {
                    "type":   {"bsonType": "string"},
                    "schema": {"bsonType": "string"}
                }
            },
            "output": {
                "bsonType": "object",
                "required": ["type"],
                "properties": {
                    "type":   {"bsonType": "string"},
                    "schema": {"bsonType": "string"}
                }
            },
            "dependencies": {"bsonType": "array", "items": {"bsonType": "string"}},
            "input_type":  {"bsonType": "string"},
            "output_type": {"bsonType": "string"},
        }
    }
}

PARAMETER_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "skill_id", "tenant"],
        "properties": {
            "_id":      {"bsonType": "string"},
            "skill_id": {"bsonType": "string"},
            "tenant":   {"bsonType": "string"},
        }
    }
}

CONSTRAINT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "skill_id", "rule_text", "violation_paraphrase", "severity"],
        "properties": {
            "_id":                   {"bsonType": "string"},
            "skill_id":              {"bsonType": "string"},
            "rule_text":             {"bsonType": "string"},
            "violation_paraphrase":  {"bsonType": "string"},
            "examples":              {"bsonType": "object"},
            "constraint_embedding":  {"bsonType": ["array", "null"]},
            "severity":              {"enum": ["fail", "warn", "note"]},
            "category":              {"bsonType": "string"},
        }
    }
}

PREFERENCE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "owner", "scope", "category", "name", "version"],
        "properties": {
            "_id":      {"bsonType": "string"},
            "owner":    {"bsonType": "string"},
            "scope":    {"enum": ["skill", "category", "global"]},
            "applies_to_skill_id": {"bsonType": "string"},
            "category": {"bsonType": "string"},
            "name":     {"bsonType": "string"},
            "version":  {"bsonType": "string"},
            "policy":   {"bsonType": "object"},
        }
    }
}

CONSTRAINTS_SEED = [
    {
        "_id": "constraint:leafygreen-ui:no-green-on-white",
        "skill_id": "skill:leafygreen-ui",
        "rule_text": "Green (#00ED64) NEVER used for text on white. Fails WCAG AA contrast.",
        "violation_paraphrase": "text color is #00ED64 (green-base / Spring Green) on white (#FFFFFF) or near-white background",
        "examples": {
            "violating": "color: '#00ED64' with background: 'white' — e.g. <Button style={{color: '#00ED64'}}>Submit</Button>",
            "compliant": "color: '#00684A' (green.dark2) on white — passes WCAG AA contrast ratio"
        },
        "constraint_embedding": None,
        "severity": "fail",
        "category": "accessibility",
    },
    {
        "_id": "constraint:leafygreen-ui:spacing-unit",
        "skill_id": "skill:leafygreen-ui",
        "rule_text": "All spacing values must be multiples of the 4px spacing unit.",
        "violation_paraphrase": "spacing, padding, margin, or gap value is not a multiple of 4 — e.g. 13px, 7px, 5px, 3px",
        "examples": {
            "violating": "padding: '13px' or margin: '7px'",
            "compliant": "padding: '12px' (3×4) or margin: '8px' (2×4)"
        },
        "constraint_embedding": None,
        "severity": "warn",
        "category": "design_tokens",
    },
    {
        "_id": "constraint:ui-builder:primary-color",
        "skill_id": "skill:ui-builder",
        "rule_text": "Primary color must match the tenant's declared token. Default: #2563EB. client-a: #2563EB. client-b differs.",
        "violation_paraphrase": "primary color value does not match the tenant design token — e.g. #3B82F6 used where #2563EB is declared",
        "examples": {
            "violating": "primary: '#3B82F6' when tenant declares primary: '#2563EB'",
            "compliant": "primary: '#2563EB' matching tenant token"
        },
        "constraint_embedding": None,
        "severity": "fail",
        "category": "design_tokens",
    },

    # ── leafygreen-ui: accessibility ─────────────────────────────────────────
    {
        "_id": "constraint:leafygreen-ui:focus-ring-required",
        "skill_id": "skill:leafygreen-ui",
        "rule_text": "Interactive elements must have a visible focus ring. outline:none without a :focus-visible replacement fails WCAG 2.4.7.",
        "violation_paraphrase": "button or interactive element has outline set to none or 0 on focus with no custom focus-visible border or box-shadow replacement",
        "examples": {
            "violating": "button { outline: none } with no :focus-visible override",
            "compliant": "button:focus-visible { box-shadow: 0 0 0 3px #007CAD }"
        },
        "constraint_embedding": None,
        "severity": "fail",
        "category": "accessibility",
    },
    {
        "_id": "constraint:leafygreen-ui:aria-label-icon-button",
        "skill_id": "skill:leafygreen-ui",
        "rule_text": "Icon-only buttons (no visible text) must have aria-label. Screen readers cannot announce unlabeled icon buttons.",
        "violation_paraphrase": "button element contains only an icon or SVG child with no aria-label prop and no visible text content",
        "examples": {
            "violating": "<IconButton><Icon /></IconButton> — no aria-label",
            "compliant": "<IconButton aria-label=\"Close dialog\"><Icon /></IconButton>"
        },
        "constraint_embedding": None,
        "severity": "fail",
        "category": "accessibility",
    },

    # ── leafygreen-ui: design tokens ─────────────────────────────────────────
    {
        "_id": "constraint:leafygreen-ui:font-size-scale",
        "skill_id": "skill:leafygreen-ui",
        "rule_text": "Font sizes must come from the LeafyGreen type scale: 11, 12, 13, 14, 16, 18, 24, 32px. Off-scale sizes break type consistency.",
        "violation_paraphrase": "font-size is set to a value not in the LeafyGreen type scale — off-scale values like 15px, 17px, 20px, or 22px",
        "examples": {
            "violating": "fontSize: '15px' or fontSize: '20px'",
            "compliant": "fontSize: '16px' or fontSize: '14px'"
        },
        "constraint_embedding": None,
        "severity": "warn",
        "category": "design_tokens",
    },
    {
        "_id": "constraint:leafygreen-ui:border-radius-token",
        "skill_id": "skill:leafygreen-ui",
        "rule_text": "border-radius must use a LeafyGreen token value: 2px, 4px, 6px, 8px, 16px, or 100px (pill). Arbitrary values diverge from the design system.",
        "violation_paraphrase": "border-radius is set to an arbitrary value not in the token set: 2px, 4px, 6px, 8px, 16px, 100px — e.g. 3px, 5px, 10px, 12px",
        "examples": {
            "violating": "borderRadius: '10px' or borderRadius: '3px'",
            "compliant": "borderRadius: '8px' or borderRadius: '4px'"
        },
        "constraint_embedding": None,
        "severity": "warn",
        "category": "design_tokens",
    },

    # ── ui-builder: accessibility + layout ───────────────────────────────────
    {
        "_id": "constraint:ui-builder:min-touch-target",
        "skill_id": "skill:ui-builder",
        "rule_text": "Interactive elements must have a minimum touch target of 44×44px (WCAG 2.5.5, iOS/Android HIG).",
        "violation_paraphrase": "button or interactive element has height or width set below 44px — e.g. height:32px, width:36px, or size:sm with no touch-target padding",
        "examples": {
            "violating": "<Button style={{height: '32px', width: '80px'}}>",
            "compliant": "<Button style={{height: '44px', minWidth: '44px'}}>"
        },
        "constraint_embedding": None,
        "severity": "warn",
        "category": "accessibility",
    },
    {
        "_id": "constraint:ui-builder:max-line-length",
        "skill_id": "skill:ui-builder",
        "rule_text": "Text containers must not exceed 75ch or 680px. Longer lines reduce reading comfort and comprehension.",
        "violation_paraphrase": "text container or paragraph element has max-width greater than 680px or 75ch, or has no max-width with flowing prose content",
        "examples": {
            "violating": "<p style={{maxWidth: '900px'}}> or <div class=\"content\"> with no width constraint",
            "compliant": "<p style={{maxWidth: '65ch'}}> or <div style={{maxWidth: '680px'}}>"
        },
        "constraint_embedding": None,
        "severity": "note",
        "category": "layout",
    },
    {
        "_id": "constraint:ui-builder:heading-hierarchy",
        "skill_id": "skill:ui-builder",
        "rule_text": "Heading levels must not skip: h1→h2→h3 in sequence. Skipping levels (e.g. h1→h3) breaks screen reader document outline.",
        "violation_paraphrase": "heading hierarchy skips a level — h1 followed directly by h3, or h2 followed by h4, without the intermediate heading level",
        "examples": {
            "violating": "<h1>Page</h1><h3>Section</h3> — h2 missing",
            "compliant": "<h1>Page</h1><h2>Section</h2><h3>Subsection</h3>"
        },
        "constraint_embedding": None,
        "severity": "fail",
        "category": "accessibility",
    },

    # ── schema-review: schema quality ─────────────────────────────────────────
    {
        "_id": "constraint:schema-review:required-fields-declared",
        "skill_id": "skill:schema-review",
        "rule_text": "JSON Schema objects with properties must declare a required array. Omitting required makes every field silently optional.",
        "violation_paraphrase": "JSON Schema object has defined properties but no required array, or required is an empty array, leaving all properties optional by default",
        "examples": {
            "violating": "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"}}} — no required",
            "compliant": "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"}},\"required\":[\"name\"]}"
        },
        "constraint_embedding": None,
        "severity": "warn",
        "category": "schema_quality",
    },
]

RUNS_TTL_SECONDS = 60 * 60 * 24 * 90


def _derive_compat_fields(skill: dict) -> dict:
    """Add v1 top-level input_type/output_type fields from the v2 input/output blocks."""
    skill = dict(skill)
    if "input" in skill and "type" in skill["input"]:
        skill["input_type"] = skill["input"]["type"]
    if "output" in skill and "type" in skill["output"]:
        skill["output_type"] = skill["output"]["type"]
    return skill


def seed():
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]

    db.skills.drop()
    db.edges.drop()
    db.parameters.drop()
    db.preferences.drop()
    db.constraints.drop()

    for name, validator in [
        ("skills", SKILL_VALIDATOR),
        ("parameters", PARAMETER_VALIDATOR),
        ("preferences", PREFERENCE_VALIDATOR),
        ("constraints", CONSTRAINT_VALIDATOR),
    ]:
        try:
            db.create_collection(name, validator=validator)
        except CollectionInvalid:
            pass

    db.skills.create_index("dependencies")
    db.skills.create_index("lifecycle")
    db.skills.create_index("input_type")
    db.skills.create_index("output_type")
    db.skills.create_index("output.type")
    db.skills.create_index("input.type")
    db.skills.create_index([
        ("name", "text"),
        ("description", "text"),
        ("domain_fields", "text"),
    ])

    db.edges.create_index("from_skill")
    db.edges.create_index("to_skill")
    db.edges.create_index("compatible")

    db.parameters.create_index([("skill_id", 1), ("tenant", 1)], unique=True)
    db.parameters.create_index("tenant")

    # preferences indexes — designed forward-compatible with Queryable
    # Encryption: equality filters only (no text or unique compound on
    # the policy body), per-owner partition for future per-owner DEKs.
    db.preferences.create_index("owner")
    db.preferences.create_index([("scope", 1), ("applies_to_skill_id", 1)])
    db.preferences.create_index("category")

    # constraints indexes — Layer 2 semantic validation.
    # skill_id + category support fast pre-filter inside $vectorSearch.
    # constraint_embedding is the Atlas Vector Search field (populated
    # by scripts/seed_constraint_embeddings.py after embeddings are computed).
    db.constraints.create_index("skill_id")
    db.constraints.create_index("severity")
    db.constraints.create_index([("skill_id", 1), ("category", 1)])

    if "runs" not in db.list_collection_names():
        db.create_collection("runs")
    db.runs.create_index("tool")
    db.runs.create_index([("session_id", 1), ("timestamp", 1)])
    db.runs.create_index("timestamp", expireAfterSeconds=RUNS_TTL_SECONDS)

    skills_data = json.loads(SKILLS_PATH.read_text())

    if skills_data["skills"]:
        compat_docs = [_derive_compat_fields(s) for s in skills_data["skills"]]
        db.skills.insert_many(compat_docs)
        print(f"Inserted {len(compat_docs)} skills")

    if skills_data["edges"]:
        db.edges.insert_many(skills_data["edges"])
        print(f"Inserted {len(skills_data['edges'])} edges")

    if PARAMETERS_PATH.exists():
        params_data = json.loads(PARAMETERS_PATH.read_text())
        if params_data.get("parameters"):
            db.parameters.insert_many(params_data["parameters"])
            print(f"Inserted {len(params_data['parameters'])} parameter docs")

    if PREFERENCES_PATH.exists():
        prefs_data = json.loads(PREFERENCES_PATH.read_text())
        if prefs_data.get("preferences"):
            db.preferences.insert_many(prefs_data["preferences"])
            print(f"Inserted {len(prefs_data['preferences'])} preference docs")

    db.constraints.insert_many(CONSTRAINTS_SEED)
    print(f"Inserted {len(CONSTRAINTS_SEED)} constraint docs (embeddings pending)")
    _resubmit_vector_index(db)

    runs_count = db.runs.estimated_document_count()
    print(f"runs collection preserved: {runs_count} existing documents")

    if LOCAL_DIR.is_dir():
        _load_local_overlay(db)

    for state in ["active", "inactive"]:
        count = db.skills.count_documents({"lifecycle": state})
        if count:
            print(f"  {state}: {count}")

    print("\nDone. Run 'python scripts/validate.py' to test.")


def _resubmit_vector_index(db) -> None:
    """Resubmit the Layer 2 vector search index after the constraints collection
    is dropped and recreated. Silently skips on local MongoDB without mongot
    (OperationFailure) — Layer 2 vector search requires Atlas or MongoDB 8.2+
    with mongot running."""
    try:
        from pymongo.operations import SearchIndexModel
        model = SearchIndexModel(
            definition={
                "fields": [
                    {"type": "vector", "path": "constraint_embedding",
                     "numDimensions": 1024, "similarity": "cosine"},
                    {"type": "filter", "path": "skill_id"},
                ]
            },
            name="constraint_embedding_index",
            type="vectorSearch",
        )
        db.constraints.create_search_index(model)
        print("  vector index: submitted (PENDING → READY in ~1-3 min)")
    except Exception:
        pass  # local mongod without mongot — Layer 2 vector search not available


def _load_local_overlay(db) -> None:
    """Load private skills/parameters/preferences from LOCAL_DIR.

    Idempotent: each doc is upserted by `_id`, so re-seeding updates
    existing local entries rather than duplicating. Schema validation
    failures on individual local docs are logged but do not crash the
    seed — canonical content stays loaded even if a local doc is bad.

    Hard guards:
      - skills.json entries must use `skill:local:` namespace
      - parameters.json entries' skill_id must reference a local skill
      - preferences.json entries' applies_to_skill_id (if set) must
        reference a local skill

    These prevent local content from masquerading as canonical data.
    """
    print(f"\nlocal overlay: loading from {LOCAL_DIR}")
    skill_count = _overlay_load(
        db.skills, LOCAL_DIR / "skills.json", "skills",
        guard=lambda d: d["_id"].startswith(LOCAL_NAMESPACE_PREFIX),
        guard_msg=f"_id must start with '{LOCAL_NAMESPACE_PREFIX}'",
        compat=_derive_compat_fields,
    )
    if skill_count:
        print(f"  skills upserted:      {skill_count}")

    param_count = _overlay_load(
        db.parameters, LOCAL_DIR / "parameters.json", "parameters",
        guard=lambda d: d["skill_id"].startswith(LOCAL_NAMESPACE_PREFIX),
        guard_msg=f"skill_id must start with '{LOCAL_NAMESPACE_PREFIX}'",
    )
    if param_count:
        print(f"  parameters upserted:  {param_count}")

    pref_count = _overlay_load(
        db.preferences, LOCAL_DIR / "preferences.json", "preferences",
        guard=lambda d: (
            d.get("scope") != "skill"
            or d.get("applies_to_skill_id", "").startswith(LOCAL_NAMESPACE_PREFIX)
        ),
        guard_msg=f"applies_to_skill_id must start with '{LOCAL_NAMESPACE_PREFIX}' when scope=skill",
    )
    if pref_count:
        print(f"  preferences upserted: {pref_count}")


def _overlay_load(collection, path: Path, key: str, guard, guard_msg: str,
                  compat=None) -> int:
    """Read a local-overlay JSON file and upsert each doc under `key`.

    `guard(doc)` must return True; otherwise the doc is skipped with a
    clear message. `compat`, if provided, runs over each doc before
    insert (used for v1 input_type/output_type derivation on skills)."""
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"  WARNING: {path.name} is not valid JSON ({e}); skipping overlay")
        return 0
    docs = data.get(key) or []
    upserted = 0
    for doc in docs:
        if not guard(doc):
            print(f"  REJECTED {key}/{doc.get('_id', '<no _id>')}: {guard_msg}")
            continue
        if compat is not None:
            doc = compat(doc)
        try:
            collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            upserted += 1
        except WriteError as e:
            print(f"  REJECTED {key}/{doc.get('_id', '<no _id>')}: {e.details.get('errmsg', e)}")
    return upserted


if __name__ == "__main__":
    seed()
