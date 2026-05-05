"""list_skills: declarative enumeration. Added in v2.1 to replace the
search_skills "list-everything" hack discovered during R1 (notes/r1-results.md F1).

Background: search_skills is text-index based — fine for ranked relevance,
useless for "give me every active skill". The R1 agent gave up and read
schema/skills.json directly, the exact bypass the architecture is supposed
to prevent. list_skills is the declarative replacement.
"""


def test_list_skills_default_returns_active_only(seeded, call):
    r = call(seeded.list_skills)
    assert r["filter"] == {"lifecycle": "active"}
    ids = {s["_id"] for s in r["results"]}
    assert ids == {
        "skill:query-analysis",
        "skill:schema-review",
        "skill:code-gen",
        "skill:ui-builder",
        "skill:test-writer",
        "skill:leafygreen-ui",
        "skill:react-test-writer",
    }
    assert r["count"] == 7
    # Inactive skill is excluded by default
    assert "skill:schema-review-v1" not in ids


def test_list_skills_lifecycle_inactive(seeded, call):
    r = call(seeded.list_skills, lifecycle="inactive")
    assert r["count"] == 1
    assert r["results"][0]["_id"] == "skill:schema-review-v1"


def test_list_skills_lifecycle_any_includes_inactive(seeded, call):
    r = call(seeded.list_skills, lifecycle="any")
    assert r["count"] == 8   # 7 active + 1 inactive (schema-review-v1)
    ids = {s["_id"] for s in r["results"]}
    assert "skill:schema-review-v1" in ids
    # `lifecycle: any` strips the filter entirely
    assert "lifecycle" not in r["filter"]


def test_list_skills_output_type_filter(seeded, call):
    r = call(seeded.list_skills, output_type="ui_components")
    assert r["count"] == 1
    assert r["results"][0]["_id"] == "skill:ui-builder"


def test_list_skills_input_type_filter_returns_multiple(seeded, call):
    """Two skills consume application_code: ui-builder and test-writer."""
    r = call(seeded.list_skills, input_type="application_code")
    assert {s["_id"] for s in r["results"]} == {"skill:ui-builder", "skill:test-writer"}


def test_list_skills_combined_filters(seeded, call):
    r = call(seeded.list_skills, lifecycle="active", output_type="test_suite")
    assert r["count"] == 1
    assert r["results"][0]["_id"] == "skill:test-writer"


def test_list_skills_unknown_filter_returns_empty_cleanly(seeded, call):
    r = call(seeded.list_skills, output_type="unicorn_juice")
    assert r["count"] == 0
    assert r["results"] == []


def test_list_skills_results_contain_expected_fields(seeded, call):
    r = call(seeded.list_skills)
    s = r["results"][0]
    for k in ("name", "version", "lifecycle", "input_type", "output_type", "dependencies"):
        assert k in s, f"missing field: {k}"


def test_list_skills_returns_results_sorted_by_id(seeded, call):
    r = call(seeded.list_skills, lifecycle="any")
    ids = [s["_id"] for s in r["results"]]
    assert ids == sorted(ids)
