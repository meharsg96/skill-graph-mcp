"""get_skill_instructions richer response (v2.3.0 — closes R2/R3 F9).

R3 prompts 14 + 15 confirmed the agent reaches for Read instead of
get_skill_instructions because Read gives line-anchored citations
(SKILL.md:62) that the old tool didn't provide. Plus Read is a
strict-content path; our tool only added a wrapper.

v2.3.0 fix: the response is now a strict superset of what Read gives,
plus graph-side context (accessibility_rules from domain_fields,
direct_consumers, line_count for citation math, source provenance).
"""


def test_response_includes_line_count(seeded, call):
    r = call(seeded.get_skill_instructions, skill_id="skill:leafygreen-ui")
    assert "line_count" in r
    assert r["line_count"] > 0
    # Should equal the count of newlines in content (+1 if no trailing nl)
    body = r["content"]
    expected = body.count("\n") + (0 if body.endswith("\n") else 1)
    assert r["line_count"] == expected


def test_response_includes_source_provenance(seeded, call):
    r = call(seeded.get_skill_instructions, skill_id="skill:leafygreen-ui")
    assert r["source"] == "graph"


def test_response_surfaces_accessibility_rules(seeded, call):
    """LeafyGreen carries a WCAG rule in domain_fields. The agent should
    not have to make a second call (or read a second file) to get it."""
    r = call(seeded.get_skill_instructions, skill_id="skill:leafygreen-ui")
    rules = r["accessibility_rules"]
    assert len(rules) >= 1
    no_green = next(rule for rule in rules if rule["id"] == "no-green-base-on-white")
    assert "#00ED64" in no_green["rule"]


def test_response_includes_related_skills(seeded, call):
    """direct_consumers comes from the graph; Read can't provide this."""
    r = call(seeded.get_skill_instructions, skill_id="skill:leafygreen-ui")
    related = r["related"]
    assert related["dependencies"] == []
    consumer_ids = {c["_id"] for c in related["direct_consumers"]}
    # v2.2.0 added react-test-writer as the direct consumer of react_artifact
    assert "skill:react-test-writer" in consumer_ids


def test_skill_with_no_accessibility_rules_returns_empty_list(seeded, call):
    """Other skills (e.g. test-writer) have no accessibility_rules.
    The field must still be present and empty, not missing."""
    r = call(seeded.get_skill_instructions, skill_id="skill:test-writer")
    assert "accessibility_rules" in r
    assert r["accessibility_rules"] == []


def test_missing_file_still_returns_graph_context(seeded, call):
    """skill:test-writer ships no SKILL.md file in this template repo.
    The error path must still include accessibility_rules and related —
    the graph data is independent of file presence."""
    r = call(seeded.get_skill_instructions, skill_id="skill:test-writer")
    # Either content present or error — both shapes carry the metadata
    assert "accessibility_rules" in r
    assert "related" in r
    assert r["source"] == "graph"


def test_path_traversal_guard_still_works(seeded, call):
    """Defensive: F9 fix added fields but must not weaken the security
    guard. Reuse the test_security pattern."""
    import os
    from pymongo import MongoClient
    db = MongoClient(os.environ["MONGODB_URI"])[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    db.skills.delete_one({"_id": "skill:test-malicious-v23"})
    db.skills.insert_one({
        "_id": "skill:test-malicious-v23",
        "name": "Test Malicious",
        "version": "0.0.1",
        "lifecycle": "active",
        "input":  {"type": "x"},
        "output": {"type": "y"},
        "dependencies": [],
        "input_type": "x",
        "output_type": "y",
        "skill_path": "../../etc/passwd",
    })
    r = call(seeded.get_skill_instructions, skill_id="skill:test-malicious-v23")
    assert "error" in r
    assert "escapes trusted roots" in r["error"]
    assert "content" not in r
