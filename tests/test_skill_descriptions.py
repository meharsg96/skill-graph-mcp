"""Skill descriptions for disambiguation — closes R2 finding F6.

R2 prompt 4 (build dark login for client-a) saw the agent first reach
for skill:leafygreen-ui — wrong skill — then self-correct via the
`source` field on the response. Two extra tool calls. Root cause: at
discovery time (list_skills, search_skills, get_skill_contract) the
two ui skills looked similar enough to confuse selection.

Fix: every skill carries a top-level `description` field. The two
ambiguous skills (ui-builder, leafygreen-ui) explicitly point at each
other, so the agent has the disambiguation in front of it before it
makes the choice.
"""


def test_every_active_skill_has_description(seeded, call):
    """Discoverability invariant: any skill the agent can find via
    list_skills must self-describe. If a future skill ships without a
    description, this test forces a deliberate decision."""
    r = call(seeded.list_skills)
    for s in r["results"]:
        assert "description" in s, f"{s['_id']} has no description"
        assert len(s["description"]) > 20, f"{s['_id']} description is too short"


def test_ui_builder_description_disambiguates_from_leafygreen(seeded, call):
    r = call(seeded.list_skills)
    ui_builder = next(s for s in r["results"] if s["_id"] == "skill:ui-builder")
    # Must reference the alternative explicitly so the agent doesn't pick the wrong one
    assert "client-a" in ui_builder["description"] or "tenant" in ui_builder["description"]
    assert "leafygreen-ui" in ui_builder["description"]


def test_leafygreen_description_disambiguates_from_ui_builder(seeded, call):
    r = call(seeded.list_skills)
    leafy = next(s for s in r["results"] if s["_id"] == "skill:leafygreen-ui")
    assert "ui-builder" in leafy["description"]
    # Must call out the no-tenant-overrides constraint that R2 stumbled on
    assert "default" in leafy["description"].lower()


def test_get_skill_contract_returns_description(seeded, call):
    r = call(seeded.get_skill_contract, skill_id="skill:leafygreen-ui")
    assert "description" in r
    assert "MongoDB" in r["description"]


def test_search_skills_returns_description(seeded, call):
    r = call(seeded.search_skills, query="react")
    assert any("description" in s for s in r["results"])


def test_test_writers_disambiguate(seeded, call):
    """skill:test-writer and skill:react-test-writer are both 'test
    writers' but differ in input. Their descriptions must point at
    each other so route_task picks the right one for the job."""
    r = call(seeded.list_skills)
    tw = next(s for s in r["results"] if s["_id"] == "skill:test-writer")
    rtw = next(s for s in r["results"] if s["_id"] == "skill:react-test-writer")
    assert "react" in tw["description"].lower()
    assert "react" in rtw["description"].lower()


def test_chain_disambiguation_directives_present(seeded, call):
    """F12: when two chains can produce semantically similar outputs
    via different output types (test_suite vs react_test_suite, or
    ui_components vs react_artifact), descriptions must contain an
    explicit CHOOSE THIS / choose THAT directive so the agent's
    route_task target selection isn't silent.

    Pin: any rewrite of these descriptions has to keep the directive
    or this test fails — ensures the F12 fix can't silently regress."""
    r = call(seeded.list_skills)
    by_id = {s["_id"]: s for s in r["results"]}
    for sid in ("skill:ui-builder", "skill:leafygreen-ui",
                "skill:test-writer", "skill:react-test-writer"):
        d = by_id[sid]["description"]
        assert "CHOOSE THIS" in d, f"{sid} missing CHOOSE THIS directive"


def test_inactive_skill_description_explains_status(seeded, call):
    """A reader running list_skills(lifecycle='inactive') deserves to
    know why the skill is dead, not just that it is."""
    r = call(seeded.list_skills, lifecycle="inactive")
    assert len(r["results"]) == 1
    desc = r["results"][0]["description"]
    assert "replaced" in desc.lower() or "legacy" in desc.lower()
