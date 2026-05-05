"""skill:harness — the self-documenting meta-skill (v2.3.0).

Closes the user's question: "is this workflow part of the skill itself?
if not shouldnt it be?" — the harness now ships its own operating
manual as a queryable skill via the same MCP surface as everything
else. Doubles as the F9 fix's first compelling user (the agent has a
real reason to call get_skill_instructions because skill:harness IS
pure instructions)."""


def test_harness_skill_present_with_correct_contract(seeded, call):
    r = call(seeded.get_skill_contract, skill_id="skill:harness")
    assert "error" not in r
    assert r["name"] == "Skill Graph Harness"
    assert r["version"] == "1.0.0"
    assert r["input"]["type"] == "meta_query"
    assert r["output"]["type"] == "system_documentation"
    assert r["dependencies"] == []


def test_harness_self_documents_via_skill_instructions(seeded, call):
    """The whole point: get_skill_instructions(skill:harness) returns
    the operating manual. If this fails, the system has lost the ability
    to document itself through its own tooling."""
    r = call(seeded.get_skill_instructions, skill_id="skill:harness")
    assert "error" not in r
    assert "content" in r
    body = r["content"]
    # Spot-check that the file actually has operational content
    for topic in ["SESSION_ID", "TTL", "analyze.py", "mongodump",
                  "registering the server", "common pitfalls"]:
        assert topic.lower() in body.lower(), f"missing topic: {topic}"


def test_harness_discoverable_via_search_skills(seeded, call):
    """An agent asking 'how do I operate this MCP server' should find
    the harness skill via description-based text search.

    Query intent matters more than exact keyword: Mongo's text index
    tokenizes 'SESSION_ID' as one token (underscore-joined), so
    'session' alone won't match. Use a phrase that's actually in the
    description prose."""
    r = call(seeded.search_skills, query="operate skill-graph harness")
    ids = [s["_id"] for s in r["results"]]
    assert "skill:harness" in ids


def test_harness_listed_in_list_skills(seeded, call):
    r = call(seeded.list_skills)
    ids = {s["_id"] for s in r["results"]}
    assert "skill:harness" in ids
    harness = next(s for s in r["results"] if s["_id"] == "skill:harness")
    assert "operate" in harness["description"].lower() or "self-documenting" in harness["description"].lower()


def test_harness_has_no_chain_dependencies(seeded, call):
    """Meta-skill must be a graph leaf: no deps, no consumers. It exists
    to be queried, not chained into production work."""
    r = call(seeded.impact_analysis, skill_id="skill:harness")
    assert r["direct_consumers"] == []
    # transitive_downstream lists the skill itself at depth 0 by
    # construction of $graphLookup; that's expected
    assert all(c["_id"] == "skill:harness" for c in r["transitive_downstream"])


def test_route_task_meta_query_routes_to_harness(seeded, call):
    """system_documentation is a distinct output type so the meta-skill
    is reachable via route_task."""
    r = call(seeded.route_task, target_output_type="system_documentation")
    assert "error" not in r
    assert r["chain"] == ["skill:harness"]
