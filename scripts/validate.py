#!/usr/bin/env python3
"""Plan validation using $graphLookup.

Environment:
    MONGODB_URI    Mongo connection string (default: mongodb://localhost:27017)
"""

import os

from pymongo import MongoClient

DB_NAME = os.environ.get("SKILL_GRAPH_DB", "skill_graph")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")


def get_downstream_chain(db, start_skill_id):
    """$graphLookup: compute dependency closure, active skills only."""
    pipeline = [
        {"$match": {"_id": start_skill_id}},
        {"$graphLookup": {
            "from": "skills",
            "startWith": "$_id",
            "connectFromField": "_id",
            "connectToField": "dependencies",
            "as": "downstream",
            "maxDepth": 10,
            "depthField": "depth",
            "restrictSearchWithMatch": {"lifecycle": "active"}
        }},
        {"$project": {
            "entry": "$name",
            "chain": {
                "$sortArray": {
                    "input": "$downstream",
                    "sortBy": {"depth": 1}
                }
            }
        }}
    ]
    result = list(db.skills.aggregate(pipeline))
    return result[0] if result else None


def validate_composition(db, intended_skill_ids):
    """Validate a proposed skill chain."""
    errors = []
    skills = {s["_id"]: s for s in db.skills.find({"_id": {"$in": intended_skill_ids}})}

    for skill_id in intended_skill_ids:
        if skill_id not in skills:
            errors.append({"type": "not_found", "skill": skill_id})
        elif skills[skill_id]["lifecycle"] != "active":
            errors.append({
                "type": "not_active",
                "skill": skills[skill_id]["name"],
                "state": skills[skill_id]["lifecycle"]
            })

    for i in range(len(intended_skill_ids) - 1):
        pid, cid = intended_skill_ids[i], intended_skill_ids[i + 1]
        if pid in skills and cid in skills:
            if skills[pid]["output_type"] != skills[cid]["input_type"]:
                errors.append({
                    "type": "type_mismatch",
                    "from": skills[pid]["name"],
                    "to": skills[cid]["name"],
                    "produced": skills[pid]["output_type"],
                    "expected": skills[cid]["input_type"]
                })

    return {"valid": len(errors) == 0, "errors": errors}


def main():
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]

    print("=" * 55)
    print("PLAN VALIDATION EXAMPLES")
    print("=" * 55)

    # 1: Valid chain
    print("\n--- Valid chain ---")
    print("query-analysis -> schema-review -> code-gen -> ui-builder")
    r = validate_composition(db, [
        "skill:query-analysis", "skill:schema-review",
        "skill:code-gen", "skill:ui-builder"
    ])
    print(f"Valid: {r['valid']}")

    # 2: Inactive skill in chain
    print("\n--- Inactive skill in chain ---")
    print("schema-review-v1 -> code-gen")
    r = validate_composition(db, ["skill:schema-review-v1", "skill:code-gen"])
    print(f"Valid: {r['valid']}")
    for e in r["errors"]:
        print(f"  {e['type']}: {e.get('skill', '')} {e.get('produced', '')}")

    # 3: Type mismatch (skip schema-review)
    print("\n--- Type mismatch (skip one step) ---")
    print("query-analysis -> code-gen (skipping schema-review)")
    r = validate_composition(db, ["skill:query-analysis", "skill:code-gen"])
    print(f"Valid: {r['valid']}")
    for e in r["errors"]:
        if e["type"] == "type_mismatch":
            print(f"  {e['from']} produces '{e['produced']}', {e['to']} expects '{e['expected']}'")

    # 4: Skip straight to testing (blog example)
    print("\n--- Skip straight to testing ---")
    print("query-analysis -> test-writer (can't test what hasn't been built)")
    r = validate_composition(db, ["skill:query-analysis", "skill:test-writer"])
    print(f"Valid: {r['valid']}")
    for e in r["errors"]:
        if e["type"] == "type_mismatch":
            print(f"  {e['from']} produces '{e['produced']}', {e['to']} expects '{e['expected']}'")
        else:
            print(f"  {e['type']}: {e.get('skill', '')} requires {e.get('requires', '')}")

    # 5: $graphLookup traversal
    print("\n--- $graphLookup dependency closure ---")
    result = get_downstream_chain(db, "skill:query-analysis")
    if result:
        print(f"Entry: {result['entry']}")
        for s in result["chain"]:
            print(f"  depth {s['depth']}: {s['name']} ({s['input_type']} -> {s['output_type']})")

    print("\n" + "=" * 55)

if __name__ == "__main__":
    main()
