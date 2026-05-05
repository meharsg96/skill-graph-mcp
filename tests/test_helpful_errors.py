"""Helpful error messages for retrieval tools — closes a UX gap discovered
during a real build session against v2.2.0.

When an agent calls get_tokens / get_components on a skill whose data
lives in `parameters` (e.g. skill:leafygreen-ui), the response must
tell the agent *which tenant to retry with*. Otherwise the dead-end
error costs extra tool calls (or worse, a fall-back to file reads)."""


def test_get_tokens_no_tenant_suggests_available_tenant(seeded, call):
    """LeafyGreen lives in parameters[default]. Calling without tenant
    should return an actionable error pointing at 'default'."""
    r = call(seeded.get_tokens, skill_id="skill:leafygreen-ui", theme="dark")
    assert "error" in r
    assert "tenant='default'" in r["error"]
    assert r["available_tenants"] == ["default"]


def test_get_tokens_wrong_tenant_lists_available(seeded, call):
    """Wrong tenant name is the other common agent confusion."""
    r = call(seeded.get_tokens, skill_id="skill:leafygreen-ui",
             tenant="leafygreen-ui", theme="dark")
    assert "error" in r
    assert "tenant 'leafygreen-ui'" in r["error"]
    assert "['default']" in r["error"]
    assert r["available_tenants"] == ["default"]


def test_get_components_no_tenant_suggests_available_tenant(seeded, call):
    r = call(seeded.get_components, skill_id="skill:leafygreen-ui")
    assert "error" in r
    assert "tenant='default'" in r["error"]


def test_get_tokens_skill_with_no_params_says_so_clearly(seeded, call):
    """skill:query-analysis has no design tokens AND no parameter docs.
    Error should say so, not lie about a tenant retry path."""
    r = call(seeded.get_tokens, skill_id="skill:query-analysis", tenant="anything")
    assert "error" in r
    assert "no parameter docs exist" in r["error"]
    assert r["available_tenants"] == []


def test_correct_calls_still_work_unchanged(seeded, call):
    """Sanity: the helpful-error refactor didn't break the happy path."""
    r = call(seeded.get_tokens, skill_id="skill:leafygreen-ui",
             tenant="default", theme="dark")
    assert "error" not in r
    assert r["source"] == "parameters[default]"
    assert r["tokens"]["text"]["primary"] == "#FFFFFF"


def test_ui_builder_retains_old_behavior(seeded, call):
    """ui-builder has tokens in domain_fields (not parameters).
    Calling without tenant should NOT redirect to a tenant — there's
    no parameter doc named 'default' for ui-builder."""
    r = call(seeded.get_tokens, skill_id="skill:ui-builder", theme="dark")
    # Happy path; no tenant needed
    assert "error" not in r
    assert r["source"] == "skill_default"
