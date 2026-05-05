#!/usr/bin/env python3
"""
MCP server for typed skill graph.

Exposes semantic graph operations backed by MongoDB,
not raw database access. This is the pattern from the blog:
the agent queries skills through these tools, not by reading files.

Usage:
    pip install fastmcp pymongo
    python server.py
"""

from fastmcp import FastMCP
from pymongo import MongoClient

DB_NAME = "skill_graph"
client = MongoClient("mongodb://localhost:27017")
db = client[DB_NAME]

mcp = FastMCP("skill-graph")


@mcp.tool()
def get_skill_contract(skill_id: str) -> dict:
    """Get a skill's base contract: input type, output type, dependencies."""
    skill = db.skills.find_one(
        {"_id": skill_id, "lifecycle": "active"},
        {"name": 1, "input_type": 1, "output_type": 1, "dependencies": 1, "version": 1}
    )
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    return skill


@mcp.tool()
def get_tokens(skill_id: str, theme: str = None) -> dict:
    """Get design tokens for a skill. Optionally filter by theme."""
    skill = db.skills.find_one(
        {"_id": skill_id, "lifecycle": "active"},
        {"name": 1, "domain_fields.design_tokens": 1}
    )
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    tokens = skill.get("domain_fields", {}).get("design_tokens")
    if not tokens:
        return {"error": f"Skill '{skill_id}' has no design tokens"}
    if theme and isinstance(tokens, dict) and "themes" in tokens:
        return {"skill": skill["name"], "theme": theme, "tokens": tokens}
    return {"skill": skill["name"], "tokens": tokens}


@mcp.tool()
def get_components(skill_id: str, category: str = None) -> dict:
    """Get component definitions for a skill. Optionally filter by category."""
    skill = db.skills.find_one(
        {"_id": skill_id, "lifecycle": "active"},
        {"name": 1, "domain_fields": 1}
    )
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    return {"skill": skill["name"], "domain_fields": skill.get("domain_fields", {})}


@mcp.tool()
def get_layouts(skill_id: str, breakpoint: str = None) -> dict:
    """Get layout configuration for a skill. Optionally filter by breakpoint."""
    skill = db.skills.find_one(
        {"_id": skill_id, "lifecycle": "active"},
        {"name": 1, "domain_fields.breakpoints": 1, "domain_fields.layout_grids": 1}
    )
    if not skill:
        return {"error": f"Skill '{skill_id}' not found or not active"}
    fields = skill.get("domain_fields", {})
    result = {"skill": skill["name"]}
    if "breakpoints" in fields:
        if breakpoint and breakpoint in fields["breakpoints"]:
            result["breakpoint"] = {breakpoint: fields["breakpoints"][breakpoint]}
        else:
            result["breakpoints"] = fields["breakpoints"]
    if "layout_grids" in fields:
        result["layout_grids"] = fields["layout_grids"]
    return result


@mcp.tool()
def validate_chain(skill_ids: list[str]) -> dict:
    """
    Validate a proposed skill chain.

    Checks that every skill is active, every skill's output type
    matches the next skill's input type, and all dependencies
    are satisfied.
    """
    errors = []
    skills = {s["_id"]: s for s in db.skills.find({"_id": {"$in": skill_ids}})}

    # Check all skills exist and are active
    for sid in skill_ids:
        if sid not in skills:
            errors.append({"type": "not_found", "skill": sid})
        elif skills[sid]["lifecycle"] != "active":
            errors.append({
                "type": "not_active",
                "skill": skills[sid]["name"],
                "state": skills[sid]["lifecycle"]
            })

    # Check type compatibility across the chain
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

    # Check dependency satisfaction
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
def traverse_dependencies(skill_id: str, max_depth: int = 10) -> dict:
    """
    Traverse the dependency graph from a starting skill using $graphLookup.

    Returns all downstream skills reachable through active dependencies.
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


if __name__ == "__main__":
    mcp.run()
