#!/usr/bin/env python3
"""
MCP server for typed skill graph (v2).

v1 tools (parameter retrieval, validation, traversal) are preserved unchanged
in shape. v2 adds:

  - route_task(target_output_type)     — backward $graphLookup to a chain
  - search_skills(query)               — Mongo text-index search
  - get_skill_instructions(skill_id)   — read the skill's markdown body
  - impact_analysis(skill_id)          — direct + transitive consumers + incompatible edges

Tenant-aware retrieval: get_tokens / get_components / get_layouts now accept
an optional `tenant=` argument. When given, the parameters collection is
consulted first; the skill's own `domain_fields` is the fallback.

Usage:
    pip install -r requirements.txt
    python server.py

Environment:
    MONGODB_URI    Mongo connection string (default: mongodb://localhost:27017)
    SESSION_ID     Tag attached to every tool-call log entry
                   (auto fallback: session:auto-<pid>-<epoch>)
"""

import json
import os
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from fastmcp import FastMCP
from pymongo import MongoClient

DB_NAME = "skill_graph"
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
REPO_ROOT = Path(__file__).resolve().parent

# Trusted roots for resolving skill_path. Canonical skills live under
# REPO_ROOT; local-overlay skills (v2.5+) live under LOCAL_DIR if the
# user has populated it. Any path that doesn't resolve under one of
# these is rejected as a traversal attempt.
LOCAL_DIR = Path(
    os.environ.get("SKILL_GRAPH_LOCAL_DIR", str(Path.home() / ".skill-graph-local"))
).expanduser()
TRUSTED_ROOTS = [REPO_ROOT]
if LOCAL_DIR.is_dir():
    TRUSTED_ROOTS.append(LOCAL_DIR.resolve())

# MCP's stdio transport spawns servers with an empty env by default — the host
# must opt in to forwarding any variable. To keep session-scoped aggregations
# meaningful even when the host doesn't forward SESSION_ID, fall back to a
# server-lifetime id. Calls within one server process then group naturally;
# explicit SESSION_ID still wins when provided.
SESSION_ID = os.environ.get("SESSION_ID") or f"session:auto-{os.getpid()}-{int(time.time())}"

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
runs = db["runs"]

mcp = FastMCP("skill-graph")


# Per-tool argument redaction for the runs log. Replaces matching
# kwargs with "[REDACTED]" before insertion into db.runs. Empty by
# default — populate when adding a tool that accepts a secret value
# the caller does not want surfaced in the audit log.
_REDACTED_PARAMS: dict[str, set[str]] = {}


def log_tool_call(func):
    """Append a record of the tool call to db.runs.

    Captures: tool name, kwargs (with per-tool redactions per
    _REDACTED_PARAMS), approximate tokens returned (chars // 4),
    duration, session id, error class. Failures in logging never
    propagate to the caller — instrumentation must not break the
    tool surface.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = datetime.now(timezone.utc)
        error = None
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            error = type(exc).__name__
            result = {"error": f"{error}: {exc}"}
            raise
        finally:
            try:
                payload_chars = len(json.dumps(result, default=str))
            except Exception:
                payload_chars = 0
            try:
                redact = _REDACTED_PARAMS.get(func.__name__, set())
                params_for_log = {
                    k: ("[REDACTED]" if k in redact else v)
                    for k, v in kwargs.items()
                }
                runs.insert_one({
                    "tool": func.__name__,
                    "params": params_for_log,
                    "tokens_returned": payload_chars // 4,
                    "duration_ms": (datetime.now(timezone.utc) - start).total_seconds() * 1000,
                    "session_id": SESSION_ID,
                    "error": error,
                    "timestamp": start,
                })
            except Exception:
                pass
        return result
    return wrapper


# ---------- internal helpers ----------

def _is_subpath(child: Path, parent: Path) -> bool:
    """True iff `child` is `parent` or a descendant of it. Both must be
    resolved absolute paths. Used to enforce the trusted-root boundary
    on skill_path resolution (canonical REPO_ROOT and v2.5 LOCAL_DIR)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _active_skill(skill_id: str, projection: dict | None = None):
    """Find an active skill by id, or None."""
    return db.skills.find_one({"_id": skill_id, "lifecycle": "active"}, projection)


def _tenant_params(skill_id: str, tenant: str) -> dict | None:
    """Look up tenant overrides for a skill, or None."""
    return db.parameters.find_one({"skill_id": skill_id, "tenant": tenant})


def _available_tenants(skill_id: str) -> list[str]:
    """List tenant ids that have parameter docs for this skill.
    Used to make 'no design tokens' / 'no components' errors actionable
    instead of dead-ends — the agent gets back the next call to try."""
    return sorted(db.parameters.distinct("tenant", {"skill_id": skill_id}))


def _no_data_error(skill_id: str, kind: str, tenant: str | None) -> dict:
    """Build an error response for retrieval tools that hint at the right
    next call. `kind` is the data kind (e.g. 'design tokens', 'components',
    'layouts') used in the error message."""
    avail = _available_tenants(skill_id)
    if tenant is not None and tenant not in avail:
        if avail:
            msg = (f"Skill '{skill_id}' has no {kind} for tenant '{tenant}'. "
                   f"Available tenants: {avail}. Retry with one of those.")
        else:
            msg = (f"Skill '{skill_id}' has no {kind} (and no parameter docs "
                   f"exist for this skill at all).")
    elif tenant is None and avail:
        msg = (f"Skill '{skill_id}' has no {kind} in its own domain_fields, "
               f"but tenant-scoped parameter docs exist. "
               f"Retry with tenant='{avail[0]}' "
               f"(available tenants: {avail}).")
    else:
        msg = f"Skill '{skill_id}' has no {kind}"
    return {"error": msg, "available_tenants": avail}


# ---------- v1 tools (preserved; tenant arg added to retrieval tools) ----------

@mcp.tool()
@log_tool_call
def get_skill_contract(skill_id: str) -> dict:
    """Get a skill's base contract: input/output types, dependencies, version."""
    skill = _active_skill(
        skill_id,
        {"name": 1, "description": 1, "input_type": 1, "output_type": 1,
         "input": 1, "output": 1, "dependencies": 1, "dependency_constraints": 1,
         "version": 1, "parameter_sources": 1}
    )
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    return skill


@mcp.tool()
@log_tool_call
def get_tokens(skill_id: str, theme: str = None, tenant: str = None) -> dict:
    """Get design tokens for a skill, optionally filtered to a single theme.

    Precedence when `tenant` is provided: the matching `parameters` document
    wins. If no parameter doc exists for that tenant/skill, the skill's own
    `domain_fields.design_tokens` is the fallback. Returns include a
    `source` field indicating which path was used.
    """
    if tenant is not None:
        p = _tenant_params(skill_id, tenant)
        tokens = (p or {}).get("design_tokens") if p else None
        source = f"parameters[{tenant}]" if tokens else "skill_default"
    else:
        tokens = None
        source = "skill_default"

    if tokens is None:
        skill = _active_skill(skill_id, {"name": 1, "domain_fields.design_tokens": 1})
        if not skill:
            return {"error": f"Skill '{skill_id}' not found or not active"}
        tokens = skill.get("domain_fields", {}).get("design_tokens")
        skill_name = skill["name"]
    else:
        # Always resolve display name from the skill itself
        skill = _active_skill(skill_id, {"name": 1})
        skill_name = (skill or {}).get("name", skill_id)

    if not tokens:
        return _no_data_error(skill_id, "design tokens", tenant)

    if theme is not None:
        themes = tokens.get("themes")
        if not isinstance(themes, dict) or theme not in themes:
            available = list(themes.keys()) if isinstance(themes, dict) else themes
            return {"error": f"Theme '{theme}' not defined", "available_themes": available}
        narrowed = {k: v for k, v in tokens.items() if k != "themes"}
        narrowed["theme"] = theme
        narrowed["tokens"] = themes[theme]
        return {"skill": skill_name, "source": source, **narrowed}

    return {"skill": skill_name, "source": source, "tokens": tokens}


@mcp.tool()
@log_tool_call
def get_components(skill_id: str, category: str = None, tenant: str = None) -> dict:
    """Get component definitions for a skill, optionally filtered to one category.

    Source precedence when `tenant` is given:
      1. parameters[tenant].components — full component spec (e.g. LeafyGreen)
      2. skill.domain_fields.components — fallback

    parameters[tenant].component_overrides (small per-skill overrides like
    `button_radius`) are always attached as a separate `overrides` key.

    The shape of `categories` may differ by source: the synthetic
    ui-builder uses flat `{buttons: [variants…]}`; LeafyGreen's parameter
    doc uses a two-level `categories.{form,layout,…}.{button,toggle,…}`.
    Both are returned faithfully.
    """
    components = None
    overrides = None
    source = "skill_default"
    if tenant is not None:
        p = _tenant_params(skill_id, tenant)
        if p:
            if "components" in p:
                components = p["components"].get("categories", p["components"])
                source = f"parameters[{tenant}]"
            overrides = p.get("component_overrides")

    if components is None:
        skill = _active_skill(skill_id, {"name": 1, "domain_fields.components": 1})
        if not skill:
            return {"error": f"Skill '{skill_id}' not found or not active"}
        components = skill.get("domain_fields", {}).get("components")
        skill_name = skill["name"]
    else:
        skill = _active_skill(skill_id, {"name": 1})
        skill_name = (skill or {}).get("name", skill_id)

    if not components:
        return _no_data_error(skill_id, "components", tenant)

    if category is not None:
        if category not in components:
            return {
                "error": f"Category '{category}' not defined",
                "available_categories": sorted(components.keys()),
            }
        result = {"skill": skill_name, "source": source, "category": category, "variants": components[category]}
        if overrides is not None:
            result["overrides"] = overrides
            result["tenant"] = tenant
        return result

    result = {"skill": skill_name, "source": source, "categories": sorted(components.keys())}
    if overrides is not None:
        result["overrides"] = overrides
        result["tenant"] = tenant
    return result


@mcp.tool()
@log_tool_call
def get_layouts(skill_id: str, breakpoint: str = None, tenant: str = None) -> dict:
    """Get layout configuration for a skill.

    Tenants may override `breakpoints` and `layout_grids` via the
    `parameters` document; absent that, the skill's `domain_fields` is
    the source.
    """
    tenant_layouts = None
    if tenant is not None:
        p = _tenant_params(skill_id, tenant)
        if p:
            tenant_layouts = {k: p[k] for k in ("breakpoints", "layout_grids") if k in p}

    skill = _active_skill(skill_id, {"name": 1, "domain_fields.breakpoints": 1, "domain_fields.layout_grids": 1})
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    fields = skill.get("domain_fields", {})

    breakpoints = (tenant_layouts or {}).get("breakpoints") or fields.get("breakpoints", {})
    layout_grids = (tenant_layouts or {}).get("layout_grids") or fields.get("layout_grids")

    if breakpoint is not None:
        if breakpoint not in breakpoints:
            return {
                "error": f"Breakpoint '{breakpoint}' not defined",
                "available_breakpoints": sorted(breakpoints.keys()),
            }
        return {"skill": skill["name"], "breakpoint": breakpoint, "value": breakpoints[breakpoint]}

    result = {"skill": skill["name"]}
    if breakpoints:
        result["breakpoints"] = breakpoints
    if layout_grids:
        result["layout_grids"] = layout_grids
    return result


def _parse_version(v: str) -> tuple[int, int, int] | None:
    parts = v.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _version_range_satisfied(version: str, range_expr: str) -> bool | None:
    """Return True/False if the range can be parsed and evaluated, else None.

    Supports space-separated clauses with operators >=, <=, >, <, ==, =.
    Versions are dotted triples (semver core, no pre-release suffix).
    None signals "could not interpret" — caller should not flag a violation.
    """
    ver = _parse_version(version) if version else None
    if ver is None or not range_expr:
        return None
    ops = (">=", "<=", "==", "=", ">", "<")
    for clause in range_expr.split():
        op = next((o for o in ops if clause.startswith(o)), None)
        if op is None:
            return None
        rhs = _parse_version(clause[len(op):])
        if rhs is None:
            return None
        if op == ">=" and not (ver >= rhs):
            return False
        if op == "<=" and not (ver <= rhs):
            return False
        if op in ("==", "=") and not (ver == rhs):
            return False
        if op == ">" and not (ver > rhs):
            return False
        if op == "<" and not (ver < rhs):
            return False
    return True


@mcp.tool()
@log_tool_call
def validate_chain(skill_ids: list[str]) -> dict:
    """Validate a proposed skill chain.

    Checks (errors accumulate, none short-circuit):
      1. every skill exists and is `lifecycle: "active"`
      2. each skill's `output_type` matches the next skill's `input_type`
      3. every skill's direct dependencies appear earlier in the chain
      4. each declared `dependency_constraints[dep].version_range`
         is satisfied by the dep skill's `version`

    This validates direct dependencies only. For the full transitive
    closure of a skill's dependency tree, call `traverse_dependencies`.
    """
    errors = []
    skills = {s["_id"]: s for s in db.skills.find({"_id": {"$in": skill_ids}})}

    for sid in skill_ids:
        if sid not in skills:
            errors.append({"type": "not_found", "skill": sid})
        elif skills[sid]["lifecycle"] != "active":
            errors.append({
                "type": "not_active",
                "skill": skills[sid]["name"],
                "state": skills[sid]["lifecycle"]
            })

    for i in range(len(skill_ids) - 1):
        pid, cid = skill_ids[i], skill_ids[i + 1]
        if pid in skills and cid in skills:
            produced = skills[pid]["output_type"]
            expected = skills[cid]["input_type"]
            if produced != expected:
                errors.append({
                    "type": "type_mismatch",
                    "from": skills[pid]["name"],
                    "to": skills[cid]["name"],
                    "produced": produced,
                    "expected": expected
                })

    seen = set()
    for sid in skill_ids:
        if sid in skills:
            for dep in skills[sid].get("dependencies", []):
                if dep not in seen:
                    dep_name = skills.get(dep, {}).get("name", dep)
                    errors.append({
                        "type": "missing_dependency",
                        "skill": skills[sid]["name"],
                        "requires": dep_name
                    })
        seen.add(sid)

    # Resolve version_range against any dep we can see (in-chain or not).
    # Skills not in skill_ids are fetched here so a constraint like
    # ">=1.0.0 <2.0.0" is checked even when the dep is implicit.
    extra_dep_ids = {
        dep
        for sid in skill_ids
        if sid in skills
        for dep in (skills[sid].get("dependency_constraints") or {}).keys()
        if dep not in skills
    }
    if extra_dep_ids:
        for s in db.skills.find({"_id": {"$in": list(extra_dep_ids)}}):
            skills[s["_id"]] = s

    for sid in skill_ids:
        if sid not in skills:
            continue
        constraints = skills[sid].get("dependency_constraints") or {}
        for dep_id, spec in constraints.items():
            range_expr = (spec or {}).get("version_range")
            if not range_expr:
                continue
            dep = skills.get(dep_id)
            if not dep:
                continue
            ok = _version_range_satisfied(dep.get("version", ""), range_expr)
            if ok is False:
                errors.append({
                    "type": "version_constraint_violation",
                    "skill": skills[sid]["name"],
                    "requires": dep.get("name", dep_id),
                    "version_range": range_expr,
                    "actual_version": dep.get("version", ""),
                })

    return {
        "valid": len(errors) == 0,
        "chain": [skills[s]["name"] for s in skill_ids if s in skills],
        "errors": errors
    }


@mcp.tool()
@log_tool_call
def traverse_dependencies(skill_id: str, max_depth: int = 10) -> dict:
    """Traverse the dependency graph from a starting skill using $graphLookup.

    Returns all downstream skills reachable through active dependencies.
    Inactive skills are pruned mid-traversal via `restrictSearchWithMatch`.
    """
    pipeline = [
        {"$match": {"_id": skill_id}},
        {"$graphLookup": {
            "from": "skills",
            "startWith": "$_id",
            "connectFromField": "_id",
            "connectToField": "dependencies",
            "as": "downstream",
            "maxDepth": max_depth,
            "depthField": "depth",
            "restrictSearchWithMatch": {"lifecycle": "active"}
        }},
        {"$project": {
            "entry": "$name",
            "entry_output": "$output_type",
            "chain": {
                "$map": {
                    "input": {"$sortArray": {"input": "$downstream", "sortBy": {"depth": 1}}},
                    "as": "s",
                    "in": {
                        "name": "$$s.name",
                        "input_type": "$$s.input_type",
                        "output_type": "$$s.output_type",
                        "depth": "$$s.depth"
                    }
                }
            }
        }}
    ]
    result = list(db.skills.aggregate(pipeline))
    if not result:
        return {"error": f"Skill '{skill_id}' not found"}
    return result[0]


# ---------- v2 tools ----------

@mcp.tool()
@log_tool_call
def route_task(target_output_type: str, max_depth: int = 10) -> dict:
    """Find the skill chain that produces `target_output_type`.

    Walks dependencies backward from the target via $graphLookup, returning
    the prerequisite chain ordered root-first (deepest deps appear first,
    target last). Inactive skills are pruned mid-traversal. If multiple
    skills produce the same output type, the first match is used.
    """
    pipeline = [
        {"$match": {"output.type": target_output_type, "lifecycle": "active"}},
        {"$limit": 1},
        {"$graphLookup": {
            "from": "skills",
            "startWith": "$dependencies",
            "connectFromField": "dependencies",
            "connectToField": "_id",
            "as": "prerequisites",
            "maxDepth": max_depth,
            "depthField": "depth",
            "restrictSearchWithMatch": {"lifecycle": "active"}
        }},
        {"$project": {
            "_id": 1,
            "target": "$name",
            "target_output": "$output.type",
            "prerequisites": {
                "$map": {
                    "input": {"$sortArray": {"input": "$prerequisites", "sortBy": {"depth": -1}}},
                    "as": "s",
                    "in": {
                        "_id": "$$s._id",
                        "name": "$$s.name",
                        "input_type": "$$s.input_type",
                        "output_type": "$$s.output_type",
                        "depth": "$$s.depth"
                    }
                }
            }
        }}
    ]
    result = list(db.skills.aggregate(pipeline))
    if not result:
        return {"error": f"No active skill produces '{target_output_type}'"}
    r = result[0]
    chain = [s["_id"] for s in r["prerequisites"]] + [r["_id"]]
    chain_names = [s["name"] for s in r["prerequisites"]] + [r["target"]]
    return {
        "target": r["target"],
        "target_output": r["target_output"],
        "chain": chain,
        "chain_names": chain_names,
    }


@mcp.tool()
@log_tool_call
def search_skills(query: str, limit: int = 10) -> dict:
    """Full-text search over active skill names and domain_fields.

    Backed by Mongo's text index created in seed.py. Returns ranked
    results with relevance scores.

    For "list every skill" queries, use `list_skills` instead — text
    indexes need search terms and produce poor results for enumeration.
    """
    cursor = (
        db.skills
        .find(
            {"$text": {"$search": query}, "lifecycle": "active"},
            {"name": 1, "description": 1, "input_type": 1, "output_type": 1,
             "version": 1, "score": {"$meta": "textScore"}}
        )
        .sort([("score", {"$meta": "textScore"})])
        .limit(limit)
    )
    return {"query": query, "results": list(cursor)}


@mcp.tool()
@log_tool_call
def list_skills(
    lifecycle: str = "active",
    input_type: str = None,
    output_type: str = None,
) -> dict:
    """Enumerate skills matching declarative filters.

    Use this for "what skills exist?" or "what produces X?" — anywhere
    you'd want a list rather than a ranked relevance match. `search_skills`
    is the wrong tool for that intent (text indexes need search terms).

    Args:
        lifecycle: "active" (default), "inactive", or "any"
        input_type: filter to skills consuming this type
        output_type: filter to skills producing this type
    """
    match: dict = {}
    if lifecycle and lifecycle != "any":
        match["lifecycle"] = lifecycle
    if input_type is not None:
        match["input_type"] = input_type
    if output_type is not None:
        match["output_type"] = output_type
    cursor = db.skills.find(
        match,
        {"name": 1, "description": 1, "version": 1, "lifecycle": 1,
         "input_type": 1, "output_type": 1, "dependencies": 1}
    ).sort("_id", 1)
    results = list(cursor)
    return {"filter": match, "count": len(results), "results": results}


@mcp.tool()
@log_tool_call
def get_skill_instructions(skill_id: str) -> dict:
    """Return the skill's instruction markdown plus graph-side context.

    Strictly more useful than reading `skill_path` directly:
      - `content` — the SKILL.md body (same as `Read` would return)
      - `line_count` — total lines so callers can cite SKILL.md:N anchors
        without having to re-count
      - `accessibility_rules` — surfaced from `domain_fields` so the
        agent gets WCAG / a11y constraints in the same response
      - `dependencies` and `direct_consumers` — graph relationships
        that `Read` cannot give without a separate query
      - `source: "graph"` — provenance marker; the call is recorded in
        `db.runs` for the audit trail

    Bounded — does not traverse beyond direct consumers. Refuses any
    `skill_path` that does not resolve under a trusted root (REPO_ROOT
    for canonical skills; LOCAL_DIR for v2.5+ local-overlay skills, if
    SKILL_GRAPH_LOCAL_DIR is set and exists).
    """
    skill = _active_skill(
        skill_id,
        {"name": 1, "skill_path": 1, "domain_fields.accessibility_rules": 1,
         "dependencies": 1, "input_type": 1, "output_type": 1},
    )
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    rel = skill.get("skill_path")
    if not rel:
        return {"error": f"Skill '{skill_id}' has no skill_path"}
    # Absolute paths resolve as-is. Relative paths resolve against each
    # trusted root in order — first existing match wins so local-overlay
    # skills resolve under LOCAL_DIR while canonical skills resolve under
    # REPO_ROOT. Trusted-root check then enforces the boundary.
    candidate = Path(rel)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        path = (REPO_ROOT / candidate).resolve()
        for root in TRUSTED_ROOTS:
            attempt = (root / candidate).resolve()
            if attempt.is_file():
                path = attempt
                break
    if not any(_is_subpath(path, root) for root in TRUSTED_ROOTS):
        return {"error": f"skill_path '{rel}' escapes trusted roots"}

    accessibility_rules = (
        skill.get("domain_fields", {}).get("accessibility_rules") or []
    )

    # Direct consumers — skills whose input_type matches this skill's output_type.
    direct_consumers = []
    if skill.get("output_type"):
        for c in db.skills.find(
            {"input_type": skill["output_type"], "lifecycle": "active",
             "_id": {"$ne": skill_id}},
            {"name": 1},
        ):
            direct_consumers.append({"_id": c["_id"], "name": c["name"]})

    related = {
        "dependencies": skill.get("dependencies", []),
        "direct_consumers": direct_consumers,
    }

    if not path.is_file():
        return {
            "skill": skill["name"],
            "skill_path": rel,
            "source": "graph",
            "error": "instruction file not present in this checkout",
            "accessibility_rules": accessibility_rules,
            "related": related,
        }

    text = path.read_text()
    return {
        "skill": skill["name"],
        "skill_path": rel,
        "source": "graph",
        "content": text,
        "line_count": text.count("\n") + (0 if text.endswith("\n") else 1),
        "accessibility_rules": accessibility_rules,
        "related": related,
    }


@mcp.tool()
@log_tool_call
def impact_analysis(skill_id: str, max_depth: int = 10) -> dict:
    """Compute the blast radius if `skill_id` changes.

    Returns:
      - direct_consumers: skills that consume this skill's output_type
      - transitive_downstream: full $graphLookup downstream tree (active only)
      - incompatible_edges: edges from `db.edges` flagged compatible:false
        that involve this skill (either side)
    """
    skill = db.skills.find_one({"_id": skill_id})
    if not skill:
        return {"error": f"Skill '{skill_id}' not found"}

    output_type = skill.get("output_type")
    direct = list(db.skills.find(
        {"input_type": output_type, "lifecycle": "active", "_id": {"$ne": skill_id}},
        {"name": 1, "input_type": 1, "output_type": 1}
    ))

    pipeline = [
        {"$match": {"_id": skill_id}},
        {"$graphLookup": {
            "from": "skills",
            "startWith": "$_id",
            "connectFromField": "_id",
            "connectToField": "dependencies",
            "as": "downstream",
            "maxDepth": max_depth,
            "depthField": "depth",
            "restrictSearchWithMatch": {"lifecycle": "active"}
        }},
        {"$project": {
            "downstream": {
                "$map": {
                    "input": {"$sortArray": {"input": "$downstream", "sortBy": {"depth": 1}}},
                    "as": "s",
                    "in": {
                        "_id": "$$s._id",
                        "name": "$$s.name",
                        "depth": "$$s.depth",
                        "input_type": "$$s.input_type",
                        "output_type": "$$s.output_type"
                    }
                }
            }
        }}
    ]
    transitive = next(iter(db.skills.aggregate(pipeline)), {}).get("downstream", [])

    incompatible = list(db.edges.find(
        {"compatible": False, "$or": [{"from_skill": skill_id}, {"to_skill": skill_id}]}
    ))

    return {
        "skill": skill["name"],
        "output_type": output_type,
        "direct_consumers": direct,
        "transitive_downstream": transitive,
        "incompatible_edges": incompatible,
    }


if __name__ == "__main__":
    mcp.run()
