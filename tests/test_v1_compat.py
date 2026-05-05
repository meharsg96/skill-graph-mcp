"""v1 backward compatibility: every v1 tool still returns its v1-shape payload
even though the schema migrated to the v2 ABI form (input/output blocks)."""




def test_get_skill_contract_exposes_legacy_type_fields(seeded, call):
    r = call(seeded.get_skill_contract, skill_id="skill:schema-review")
    assert r["input_type"] == "query_patterns"
    assert r["output_type"] == "schema_recommendation"
    # New v2 fields also exposed
    assert r["input"]["type"] == "query_patterns"
    assert r["output"]["type"] == "schema_recommendation"
    assert r["dependencies"] == ["skill:query-analysis"]


def test_validate_chain_still_uses_legacy_type_fields(seeded, call):
    # The v1 type-matching pipeline is end-to-end correct against the migrated schema
    r = call(seeded.validate_chain, skill_ids=[
        "skill:query-analysis",
        "skill:schema-review",
        "skill:code-gen",
        "skill:ui-builder",
    ])
    assert r["valid"] is True


def test_traverse_dependencies_unchanged(seeded, call):
    r = call(seeded.traverse_dependencies, skill_id="skill:query-analysis")
    assert "error" not in r
    # Same chain length and type fields as v1 produced
    assert len(r["chain"]) == 4
    for s in r["chain"]:
        assert "input_type" in s and "output_type" in s


def test_get_tokens_no_tenant_matches_v1_shape(seeded, call):
    """Calling get_tokens without tenant should still produce a v1-compatible
    response (skill, theme/tokens). Adds a 'source' field — additive only."""
    r = call(seeded.get_tokens, skill_id="skill:ui-builder", theme="dark")
    assert r["skill"] == "Frontend UI Builder"
    assert r["theme"] == "dark"
    assert "tokens" in r
    # Additive only: no v1 field removed.
    assert r.get("spacing_unit") == 4
