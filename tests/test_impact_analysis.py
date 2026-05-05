"""impact_analysis: direct + transitive consumers + incompatible edges."""




def test_impact_schema_review_direct_and_transitive(seeded, call):
    r = call(seeded.impact_analysis, skill_id="skill:schema-review")
    assert "error" not in r
    assert r["output_type"] == "schema_recommendation"

    direct_names = {c["name"] for c in r["direct_consumers"]}
    assert direct_names == {"Application Code Generator"}

    transitive_names = {c["name"] for c in r["transitive_downstream"]}
    # Transitive includes the direct consumer (depth 0) plus everything below
    assert transitive_names == {
        "Application Code Generator",
        "Frontend UI Builder",
        "Test Suite Generator",
    }
    # No incompatible edges involve schema-review (active path)
    assert r["incompatible_edges"] == []


def test_impact_inactive_skill_surfaces_incompatible_edge(seeded, call):
    r = call(seeded.impact_analysis, skill_id="skill:schema-review-v1")
    assert "error" not in r
    edges = r["incompatible_edges"]
    assert len(edges) == 1
    assert edges[0]["from_skill"] == "skill:schema-review-v1"
    assert edges[0]["to_skill"] == "skill:code-gen"
    assert edges[0]["compatible"] is False
    assert "note" in edges[0]


def test_impact_unknown_skill(seeded, call):
    r = call(seeded.impact_analysis, skill_id="skill:does-not-exist")
    assert "error" in r
