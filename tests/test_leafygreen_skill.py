"""LeafyGreen UI skill — real-world MongoDB design system as a skill graph node.

Validates that the v2.1.0 add-on works end-to-end through every relevant
tool, and that the parameter doc carries the full design system without
breaking the existing tenant-precedence tests for the synthetic ui-builder.
"""


def test_leafygreen_skill_present_with_correct_contract(seeded, call):
    r = call(seeded.get_skill_contract, skill_id="skill:leafygreen-ui")
    assert "error" not in r
    assert r["name"] == "LeafyGreen UI Builder"
    assert r["version"] == "1.0.0"
    assert r["input"]["type"] == "ui_requirements"
    assert r["output"]["type"] == "react_artifact"
    assert r["dependencies"] == []
    assert "params:leafygreen-ui:default" in r["parameter_sources"]


def test_leafygreen_tokens_dark_theme(seeded, call):
    """get_tokens narrows to a single theme even when the param doc is large.
    Verifies tenant precedence works for a parameter doc that lives in
    `parameters` rather than `domain_fields`."""
    r = call(seeded.get_tokens, skill_id="skill:leafygreen-ui",
             tenant="default", theme="dark")
    assert "error" not in r
    assert r["source"] == "parameters[default]"
    assert r["theme"] == "dark"
    # Dark theme has white text on dark background
    assert r["tokens"]["text"]["primary"] == "#FFFFFF"
    # Theme-agnostic data (palette, typography) carries through
    assert "palette" in r
    assert r["palette"]["green"]["dark2"] == "#00684A"


def test_leafygreen_tokens_light_theme_distinct(seeded, call):
    light = call(seeded.get_tokens, skill_id="skill:leafygreen-ui",
                 tenant="default", theme="light")
    dark = call(seeded.get_tokens, skill_id="skill:leafygreen-ui",
                tenant="default", theme="dark")
    assert light["tokens"]["text"]["primary"] != dark["tokens"]["text"]["primary"]
    assert light["tokens"]["text"]["primary"] == "#001E2B"


def test_leafygreen_components_categories(seeded, call):
    """Component spec is two-level (categories.{form,layout,…}.{button,toggle,…}).
    Verifies get_components handles the richer LeafyGreen shape, not just the
    flat ui-builder shape."""
    r = call(seeded.get_components, skill_id="skill:leafygreen-ui",
             tenant="default")
    assert r["source"] == "parameters[default]"
    # Real LeafyGreen categories
    assert set(r["categories"]).issuperset({"form", "layout", "feedback"})


def test_leafygreen_components_form_category(seeded, call):
    r = call(seeded.get_components, skill_id="skill:leafygreen-ui",
             tenant="default", category="form")
    assert r["source"] == "parameters[default]"
    assert r["category"] == "form"
    # form contains real LeafyGreen primitives
    assert "button" in r["variants"]
    assert "textInput" in r["variants"]
    button = r["variants"]["button"]
    assert "primary" in button["variants"]
    # Real LeafyGreen accent uses green.dark2 not green.base
    assert button["variantSpecs"]["primary"]["bg"] == "green.dark2"


def test_leafygreen_skill_path_resolves(seeded, call):
    r = call(seeded.get_skill_instructions, skill_id="skill:leafygreen-ui")
    assert "error" not in r
    assert r["skill_path"] == "skills/leafygreen/SKILL.md"
    assert "content" in r
    assert "MongoDB" in r["content"]
    assert "LeafyGreen" in r["content"]


def test_route_task_react_artifact_targets_leafygreen(seeded, call):
    r = call(seeded.route_task, target_output_type="react_artifact")
    assert "error" not in r
    assert r["chain"] == ["skill:leafygreen-ui"]
    assert r["target"] == "LeafyGreen UI Builder"


def test_list_skills_can_find_leafygreen_by_output_type(seeded, call):
    r = call(seeded.list_skills, output_type="react_artifact")
    assert r["count"] == 1
    assert r["results"][0]["_id"] == "skill:leafygreen-ui"


def test_search_skills_finds_leafygreen_by_keyword(seeded, call):
    r = call(seeded.search_skills, query="leafygreen mongodb")
    ids = [s["_id"] for s in r["results"]]
    assert "skill:leafygreen-ui" in ids


def test_existing_ui_builder_unaffected_by_leafygreen(seeded, call):
    """LeafyGreen lives parallel to ui-builder; the synthetic skill keeps
    its existing tenant-precedence behavior intact."""
    r = call(seeded.get_tokens, skill_id="skill:ui-builder",
             tenant="client-a", theme="dark")
    assert r["source"] == "parameters[client-a]"
    assert r["tokens"]["primary"] == "#3B82F6"   # client-a unchanged


def test_leafygreen_accessibility_rule_present_on_skill(seeded):
    """The contract carries the WCAG rule that Blog 3's validate_artifact
    will eventually enforce. Test exists so the rule is treated as
    load-bearing data, not stylistic doc filler. Read directly from db
    because get_skill_contract's projection deliberately omits
    domain_fields (would bloat the response on a hot path)."""
    import os
    from pymongo import MongoClient
    db = MongoClient(os.environ["MONGODB_URI"])[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    skill = db.skills.find_one({"_id": "skill:leafygreen-ui"})
    rules = skill["domain_fields"]["accessibility_rules"]
    assert len(rules) >= 1
    no_green = next(rule for rule in rules if rule["id"] == "no-green-base-on-white")
    assert "#00ED64" in no_green["rule"]
    assert no_green["use_instead"].startswith("green.dark2")
