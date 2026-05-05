"""route_task: backward $graphLookup from a target output type."""


def test_route_task_ui_components_full_chain(seeded, call):
    r = call(seeded.route_task, target_output_type="ui_components")
    assert "error" not in r
    assert r["target"] == "Frontend UI Builder"
    assert r["target_output"] == "ui_components"
    # Chain must end at the target
    assert r["chain"][-1] == "skill:ui-builder"
    # Full prerequisite chain present in dep order (root-first)
    assert r["chain"] == [
        "skill:query-analysis",
        "skill:schema-review",
        "skill:code-gen",
        "skill:ui-builder",
    ]


def test_route_task_test_suite(seeded, call):
    r = call(seeded.route_task, target_output_type="test_suite")
    assert r["chain"] == [
        "skill:query-analysis",
        "skill:schema-review",
        "skill:code-gen",
        "skill:test-writer",
    ]


def test_route_task_unknown_target(seeded, call):
    r = call(seeded.route_task, target_output_type="unicorn_juice")
    assert "error" in r
    assert "unicorn_juice" in r["error"]


def test_route_task_excludes_inactive_skills(seeded, call):
    # schema_recommendation_v1 is produced only by the inactive skill.
    # No active producer exists, so route_task should error.
    r = call(seeded.route_task, target_output_type="schema_recommendation_v1")
    assert "error" in r
