"""search_skills: text-index search returns ranked active skills."""




def test_search_skills_finds_ui_builder_by_keyword(seeded, call):
    r = call(seeded.search_skills, query="react ui")
    assert "results" in r
    names = [s["_id"] for s in r["results"]]
    assert "skill:ui-builder" in names
    # Inactive skills must not appear
    assert "skill:schema-review-v1" not in names


def test_search_skills_results_have_score_and_metadata(seeded, call):
    r = call(seeded.search_skills, query="schema")
    assert len(r["results"]) >= 1
    top = r["results"][0]
    for k in ("name", "input_type", "output_type", "version", "score"):
        assert k in top, f"missing field: {k}"


def test_search_skills_limit_respected(seeded, call):
    r = call(seeded.search_skills, query="active", limit=2)
    assert len(r["results"]) <= 2


def test_search_skills_no_results_returns_empty(seeded, call):
    r = call(seeded.search_skills, query="quantumcryptobanana")
    assert r["results"] == []


def test_get_skill_instructions_missing_file_returns_clean_error(seeded, call):
    """The seed JSON references skill_path values whose markdown files
    aren't shipped in this template repo. Tool should report the gap."""
    r = call(seeded.get_skill_instructions, skill_id="skill:ui-builder")
    assert "error" in r
    assert r["skill_path"] == "skills/ui-builder/SKILL.md"


def test_get_skill_instructions_unknown_skill(seeded, call):
    r = call(seeded.get_skill_instructions, skill_id="skill:does-not-exist")
    assert "error" in r
