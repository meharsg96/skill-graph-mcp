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


def log_tool_call(func):
    """Append a record of the tool call to db.runs.

    Captures: tool name, kwargs, approximate tokens returned (chars // 4),
    duration, session id, error class. Failures in logging never propagate
    to the caller — instrumentation must not break the tool surface.
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
                runs.insert_one({
                    "tool": func.__name__,
                    "params": kwargs,
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

def _active_skill(skill_id: str, projection: dict | None = None):
    """Find an active skill by id, or None."""
    return db.skills.find_one({"_id": skill_id, "lifecycle": "active"}, projection)


def _tenant_params(skill_id: str, tenant: str) -> dict | None:
    """Look up tenant overrides for a skill, or None."""
    return db.parameters.find_one({"skill_id": skill_id, "tenant": tenant})


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
        return {"error": f"Skill '{skill_id}' has no design tokens"}

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
        return {"error": f"Skill '{skill_id}' has no components"}

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


@mcp.tool()
@log_tool_call
def validate_chain(skill_ids: list[str]) -> dict:
    """Validate a proposed skill chain.

    Checks (errors accumulate, none short-circuit):
      1. every skill exists and is `lifecycle: "active"`
      2. each skill's `output_type` matches the next skill's `input_type`
      3. every skill's direct dependencies appear earlier in the chain

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
    """Return the skill's instruction markdown (`skill_path`).

    Bounded — does not traverse. Returns the file body if present, or a
    structured error if the file is missing or the skill is unknown.
    """
    skill = _active_skill(skill_id, {"name": 1, "skill_path": 1})
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    rel = skill.get("skill_path")
    if not rel:
        return {"error": f"Skill '{skill_id}' has no skill_path"}
    path = (REPO_ROOT / rel).resolve()
    # Hard guard: refuse paths that escape the repo root
    try:
        path.relative_to(REPO_ROOT)
    except ValueError:
        return {"error": f"skill_path '{rel}' escapes the repository root"}
    if not path.is_file():
        return {
            "skill": skill["name"],
            "skill_path": rel,
            "error": "instruction file not present in this checkout",
        }
    return {
        "skill": skill["name"],
        "skill_path": rel,
        "content": path.read_text(),
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
