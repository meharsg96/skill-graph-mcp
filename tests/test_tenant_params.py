"""Tenant precedence: parameters collection wins, falls back to skill defaults."""




def test_get_tokens_no_tenant_uses_skill_default(seeded, call):
    r = call(seeded.get_tokens, skill_id="skill:ui-builder", theme="dark")
    assert r["source"] == "skill_default"
    assert r["tokens"]["primary"] == "#60A5FA"  # default dark primary


def test_get_tokens_client_a_overrides(seeded, call):
    r = call(seeded.get_tokens, skill_id="skill:ui-builder", tenant="client-a", theme="dark")
    assert r["source"] == "parameters[client-a]"
    assert r["tokens"]["primary"] == "#3B82F6"  # client-a dark primary


def test_get_tokens_client_b_distinct_from_a(seeded, call):
    a = call(seeded.get_tokens, skill_id="skill:ui-builder", tenant="client-a", theme="dark")
    b = call(seeded.get_tokens, skill_id="skill:ui-builder", tenant="client-b", theme="dark")
    assert a["source"] != b["source"]
    assert a["tokens"]["primary"] != b["tokens"]["primary"]
    # client-b uses 8px spacing; client-a uses 4px
    assert a["spacing_unit"] == 4
    assert b["spacing_unit"] == 8


def test_get_tokens_unknown_tenant_falls_back_to_skill(seeded, call):
    r = call(seeded.get_tokens, skill_id="skill:ui-builder", tenant="client-z", theme="dark")
    assert r["source"] == "skill_default"  # no parameter doc → fallback
    assert r["tokens"]["primary"] == "#60A5FA"


def test_get_components_attaches_tenant_overrides(seeded, call):
    r = call(seeded.get_components, skill_id="skill:ui-builder", tenant="client-a", category="buttons")
    assert r["overrides"]["button_radius"] == 8
    assert r["overrides"]["input_style"] == "outlined"
    assert r["tenant"] == "client-a"
    # Variants themselves still come from the skill
    assert {v["variant"] for v in r["variants"]}.issuperset({"primary", "secondary"})


def test_get_components_no_tenant_no_overrides_key(seeded, call):
    r = call(seeded.get_components, skill_id="skill:ui-builder", category="buttons")
    assert "overrides" not in r
    assert "tenant" not in r
