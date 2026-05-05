"""skill:react-test-writer — closes R2 finding F5 (notes/r2-leafygreen.md).

R2 surfaced that skill:leafygreen-ui's react_artifact output had no
downstream test path. The agent flagged it twice unprompted in prompts
9 and 13. v2.2.0 adds skill:react-test-writer as a sibling to
test-writer, with input=react_artifact and output=react_test_suite
(distinct from test_suite so route_task disambiguates cleanly).
"""


def test_react_test_writer_present_with_correct_contract(seeded, call):
    r = call(seeded.get_skill_contract, skill_id="skill:react-test-writer")
    assert "error" not in r
    assert r["name"] == "React Test Suite Generator"
    assert r["version"] == "1.0.0"
    assert r["input"]["type"] == "react_artifact"
    assert r["output"]["type"] == "react_test_suite"
    assert r["dependencies"] == ["skill:leafygreen-ui"]


def test_route_task_react_test_suite_returns_full_chain(seeded, call):
    r = call(seeded.route_task, target_output_type="react_test_suite")
    assert "error" not in r
    assert r["chain"] == ["skill:leafygreen-ui", "skill:react-test-writer"]
    assert r["target"] == "React Test Suite Generator"


def test_validate_chain_leafygreen_to_react_test_writer(seeded, call):
    r = call(seeded.validate_chain, skill_ids=[
        "skill:leafygreen-ui",
        "skill:react-test-writer",
    ])
    assert r["valid"] is True
    assert r["errors"] == []


def test_impact_analysis_leafygreen_now_has_consumer(seeded, call):
    """Pre-v2.2.0, impact_analysis on leafygreen-ui returned zero
    direct consumers — the chain dead-ended. Now it surfaces
    react-test-writer as a direct consumer."""
    r = call(seeded.impact_analysis, skill_id="skill:leafygreen-ui")
    direct_ids = {c["_id"] for c in r["direct_consumers"]}
    assert "skill:react-test-writer" in direct_ids


def test_traverse_dependencies_includes_react_test_writer(seeded, call):
    r = call(seeded.traverse_dependencies, skill_id="skill:leafygreen-ui")
    chain_names = {s["name"] for s in r["chain"]}
    assert "React Test Suite Generator" in chain_names


def test_existing_test_writer_chain_unchanged(seeded, call):
    """The original application_code -> test_suite chain must not be
    disturbed by adding the parallel react_test_suite chain."""
    r = call(seeded.route_task, target_output_type="test_suite")
    assert r["chain"] == [
        "skill:query-analysis",
        "skill:schema-review",
        "skill:code-gen",
        "skill:test-writer",
    ]


def test_distinct_output_types_route_independently(seeded, call):
    """test_suite and react_test_suite must produce different chains —
    that's the whole reason for keeping them as separate output types."""
    test_suite = call(seeded.route_task, target_output_type="test_suite")
    react_test_suite = call(seeded.route_task, target_output_type="react_test_suite")
    assert test_suite["chain"] != react_test_suite["chain"]


def test_list_skills_input_type_react_artifact(seeded, call):
    """Until v2.2.0, no skill consumed react_artifact — it was a leaf."""
    r = call(seeded.list_skills, input_type="react_artifact")
    assert r["count"] == 1
    assert r["results"][0]["_id"] == "skill:react-test-writer"


def test_react_test_writer_dependency_constraint_present(seeded):
    """Dependency on leafygreen-ui carries a real version range,
    same shape as the rest of the v2 ABI."""
    import os
    from pymongo import MongoClient
    db = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]
    s = db.skills.find_one({"_id": "skill:react-test-writer"})
    constraint = s["dependency_constraints"]["skill:leafygreen-ui"]
    assert constraint["version_range"] == ">=1.0.0 <2.0.0"


def test_edge_to_react_test_writer_seeded_compatible(seeded):
    """Explicit edge in db.edges so impact_analysis surfaces it as a
    discoverable connection rather than only via $graphLookup."""
    import os
    from pymongo import MongoClient
    db = MongoClient(os.environ["MONGODB_URI"])["skill_graph"]
    edge = db.edges.find_one({"_id": "edge:leafygreen-ui->react-test-writer"})
    assert edge is not None
    assert edge["compatible"] is True
    assert edge["produced_type"] == "react_artifact"
    assert edge["consumed_type"] == "react_artifact"
