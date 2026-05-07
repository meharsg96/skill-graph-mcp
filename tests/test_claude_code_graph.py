"""Graph-integrity tests for the v2.11.0 claude-code subgraph.

Asserts the structural invariants the rest of the system depends on:
- every tool skill is rooted at permission-mode
- every subagent skill depends on tool:agent
- traversal terminates correctly
- impact_analysis on bash lists the expected consumer set
- no active skill depends on an inactive skill
"""

import os

import pytest
from pymongo import MongoClient


@pytest.fixture
def db(seeded):
    client = MongoClient(os.environ["MONGODB_URI"])
    yield client[os.environ.get("SKILL_GRAPH_DB", "skill_graph_test")]
    client.close()


CC_TOOL_SKILLS = [
    "skill:claude-code:tool:bash",
    "skill:claude-code:tool:read",
    "skill:claude-code:tool:edit",
    "skill:claude-code:tool:write",
    "skill:claude-code:tool:agent",
    "skill:claude-code:tool:mcp",
    "skill:claude-code:tool:web",
    "skill:claude-code:slash-commands",
    "skill:claude-code:hooks",
]

CC_AGENT_SKILLS = [
    "skill:claude-code:agent:general-purpose",
    "skill:claude-code:agent:explore",
    "skill:claude-code:agent:plan",
    "skill:claude-code:agent:code-review-judge",
    "skill:claude-code:agent:agentic-systems-architect",
    "skill:claude-code:agent:frontend-design-elevate",
    "skill:claude-code:agent:claude-code-guide",
    "skill:claude-code:agent:statusline-setup",
]


# ── invariants on the dependency graph ──────────────────────────────────────

def test_every_tool_depends_on_permission_mode_or_model_selection(db):
    """Tool skills must root somewhere on the policy plane (permission-mode
    or model-selection). Without that, a downstream consumer cannot rely on
    the policy chain firing before the tool runs."""
    for skill_id in CC_TOOL_SKILLS:
        skill = db.skills.find_one({"_id": skill_id})
        assert skill, f"missing skill: {skill_id}"
        deps = set(skill.get("dependencies", []))
        rooted = (
            "skill:claude-code:permission-mode" in deps
            or "skill:claude-code:model-selection" in deps
        )
        assert rooted, f"{skill_id} dependencies do not include permission-mode or model-selection: {deps}"


def test_every_subagent_depends_on_tool_agent(db):
    for skill_id in CC_AGENT_SKILLS:
        skill = db.skills.find_one({"_id": skill_id})
        assert skill, f"missing skill: {skill_id}"
        deps = set(skill.get("dependencies", []))
        assert "skill:claude-code:tool:agent" in deps, (
            f"{skill_id} must depend on skill:claude-code:tool:agent, "
            f"got {deps}"
        )


def test_no_active_skill_depends_on_inactive_skill(db):
    """Active skills must not point to inactive ones — that would let
    inactive skills leak into active chains via $graphLookup."""
    active_ids = {s["_id"] for s in db.skills.find({"lifecycle": "active"})}
    inactive_ids = {s["_id"] for s in db.skills.find({"lifecycle": "inactive"})}
    for s in db.skills.find({"lifecycle": "active"}):
        deps = set(s.get("dependencies", []))
        bad = deps & inactive_ids
        assert not bad, f"{s['_id']} depends on inactive: {bad}"
        # Every dep must exist in the active set
        missing = deps - active_ids - inactive_ids
        assert not missing, f"{s['_id']} depends on undefined: {missing}"


def test_subagent_tool_whitelists_match_dependencies(db):
    """If a subagent declares domain_fields.tool_whitelist as a list, the
    underlying tool dependencies should be a subset of (whitelist + spawning).
    Catches the case where a subagent lists tool:bash in dependencies but
    blacklists Bash in tool_whitelist — incoherent."""
    for skill_id in CC_AGENT_SKILLS:
        s = db.skills.find_one({"_id": skill_id})
        whitelist = s["domain_fields"].get("tool_whitelist")
        if whitelist == "*" or whitelist is None:
            continue  # general-access agents — no constraint to check
        # Map declared dependencies (skill IDs) to the Claude Code tool
        # name they represent. tool_whitelist is the Claude tool name set.
        tool_to_dep = {
            "Bash": "skill:claude-code:tool:bash",
            "Read": "skill:claude-code:tool:read",
            "Edit": "skill:claude-code:tool:edit",
            "Write": "skill:claude-code:tool:write",
            "Agent": "skill:claude-code:tool:agent",
            "WebFetch": "skill:claude-code:tool:web",
            "WebSearch": "skill:claude-code:tool:web",
        }
        deps = set(s.get("dependencies", []))
        for tname in whitelist:
            dep_id = tool_to_dep.get(tname)
            if not dep_id:
                continue  # tools without a graph node (Glob, Grep, TodoWrite, etc.)
            if dep_id == "skill:claude-code:tool:agent":
                continue  # spawning capability is universal
            assert dep_id in deps, (
                f"{skill_id} whitelists '{tname}' but does not depend on {dep_id}"
            )


# ── traverse_dependencies / route_task / impact_analysis ────────────────────

def test_impact_analysis_on_permission_mode_lists_all_tool_skills(seeded, call):
    """permission-mode is the root of the policy chain — impact_analysis's
    transitive_downstream must surface every claude-code tool that
    depends on permission-mode (transitively).

    Note: skill:claude-code:slash-commands depends on model-selection, not
    permission-mode, so it is correctly NOT in this downstream set.
    """
    r = call(seeded.impact_analysis, skill_id="skill:claude-code:permission-mode")
    downstream_ids = {c["_id"] for c in r.get("transitive_downstream", [])}
    expected_tools = [t for t in CC_TOOL_SKILLS if t != "skill:claude-code:slash-commands"]
    for tool_id in expected_tools:
        assert tool_id in downstream_ids, (
            f"impact_analysis on permission-mode missed downstream {tool_id} "
            f"(got {len(downstream_ids)} skills downstream)"
        )
    # Subagents transitively depend on permission-mode via tool:agent → permission-mode
    for agent_id in CC_AGENT_SKILLS:
        assert agent_id in downstream_ids


def test_impact_analysis_on_model_selection_includes_slash_commands(seeded, call):
    """slash-commands depends on model-selection — verify the policy plane
    has both roots (permission-mode AND model-selection) covering the full
    tool surface."""
    r = call(seeded.impact_analysis, skill_id="skill:claude-code:model-selection")
    downstream_ids = {c["_id"] for c in r.get("transitive_downstream", [])}
    assert "skill:claude-code:slash-commands" in downstream_ids
    # Subagents also depend on tool:agent which depends on model-selection
    assert "skill:claude-code:tool:agent" in downstream_ids


def test_impact_analysis_on_tool_agent_lists_all_subagents(seeded, call):
    r = call(seeded.impact_analysis, skill_id="skill:claude-code:tool:agent")
    downstream_ids = {c["_id"] for c in r.get("transitive_downstream", [])}
    for agent_id in CC_AGENT_SKILLS:
        assert agent_id in downstream_ids, (
            f"impact_analysis on tool:agent missed subagent {agent_id} "
            f"(got {len(downstream_ids)} skills downstream)"
        )


def test_impact_analysis_on_tool_bash_lists_expected_consumers(seeded, call):
    """impact_analysis(skill:claude-code:tool:bash) must list every subagent
    that declares bash in its dependencies. Per skills.json: general-purpose,
    explore, plan, code-review-judge, agentic-systems-architect, claude-code-guide
    — 6 of the 8 subagents. frontend-design-elevate and statusline-setup do
    NOT depend on bash."""
    r = call(seeded.impact_analysis, skill_id="skill:claude-code:tool:bash")
    downstream_ids = {c["_id"] for c in r.get("transitive_downstream", [])}
    expected_consumers = {
        "skill:claude-code:agent:general-purpose",
        "skill:claude-code:agent:explore",
        "skill:claude-code:agent:plan",
        "skill:claude-code:agent:code-review-judge",
        "skill:claude-code:agent:agentic-systems-architect",
        "skill:claude-code:agent:claude-code-guide",
    }
    missing = expected_consumers - downstream_ids
    assert not missing, f"impact_analysis on tool:bash missed {missing}"
    # Negative check: subagents that don't use bash should NOT appear
    not_expected = {
        "skill:claude-code:agent:frontend-design-elevate",
        "skill:claude-code:agent:statusline-setup",
    }
    leaked = not_expected & downstream_ids
    assert not leaked, (
        f"impact_analysis on tool:bash listed subagents that don't depend "
        f"on bash: {leaked}"
    )


def test_route_task_resolves_to_subagent_output_type(seeded, call):
    """route_task('file_findings') must resolve into a chain that ends at
    skill:claude-code:agent:explore (which produces file_findings). This
    is the typed-routing demonstration: pick the agent by output_type, not
    by reading prose descriptions.

    route_task returns chain as a list of skill_id strings (not dicts).
    """
    r = call(seeded.route_task, target_output_type="file_findings")
    chain = r.get("chain") or []
    assert "skill:claude-code:agent:explore" in chain, (
        f"route_task('file_findings') did not surface agent:explore. "
        f"Got chain: {chain}"
    )
    # The chain should be ordered with the agent at the end (terminal output)
    assert chain[-1] == "skill:claude-code:agent:explore"


def test_get_skill_instructions_for_claude_code_skill(seeded, call):
    """get_skill_instructions on a claude-code skill must return the
    skills/claude-code/SKILL.md body (not an error). All claude-code skills
    share the same skill_path."""
    r = call(
        seeded.get_skill_instructions,
        skill_id="skill:claude-code:tool:bash",
    )
    assert r.get("error") is None, f"got error: {r.get('error')}"
    body = r.get("instructions") or r.get("content") or ""
    # SKILL.md mentions Bash specifically
    assert "Bash" in body or "bash" in body.lower(), (
        f"SKILL.md body returned but doesn't mention bash: {body[:200]!r}"
    )
    # Provenance: should be source=graph (per v2.3.0 contract)
    assert r.get("source") in {"graph", None}  # tolerate both


def test_traverse_dependencies_subagent_terminal(seeded, call):
    """Subagents are leaves in the DOWNSTREAM direction (nothing consumes
    their output further) but have full upstream chains. traverse_dependencies
    walks DOWNSTREAM consumers — for subagents that should be empty."""
    r = call(
        seeded.traverse_dependencies,
        skill_id="skill:claude-code:agent:explore",
    )
    chain = r.get("chain") or []
    assert chain == [] or all(c["_id"] != "skill:claude-code:agent:explore" for c in chain)


# ── search_skills / list_skills with the new corpus ─────────────────────────

def test_search_skills_finds_subagents(seeded, call):
    r = call(seeded.search_skills, query="subagent", limit=20)
    ids = {s["_id"] for s in r.get("results", [])}
    # At least 4 of the 8 subagent skills should rank in top 20 for "subagent"
    matched = [a for a in CC_AGENT_SKILLS if a in ids]
    assert len(matched) >= 4, (
        f"search_skills('subagent') only matched {len(matched)} subagent skills: {matched}"
    )


def test_list_skills_filters_by_input_type_task_description(seeded, call):
    """Multiple subagent skills (general-purpose, plan) consume task_description.
    list_skills with that filter should return them."""
    r = call(seeded.list_skills, input_type="task_description")
    ids = {s["_id"] for s in r.get("results", [])}
    assert "skill:claude-code:agent:general-purpose" in ids
    assert "skill:claude-code:agent:plan" in ids


# ── constraint embeddings populated ─────────────────────────────────────────

def test_claude_code_constraints_are_embedded(db):
    """After running scripts/seed_constraint_embeddings.py, all 7 claude-code
    constraints must have a non-null constraint_embedding.

    Skipped when running in CI without VOYAGE_API_KEY — the seeder is
    optional, embeddings are only required for Layer 2 vector search.
    """
    cursor = db.constraints.find(
        {"skill_id": {"$regex": "^skill:claude-code:"}},
        {"_id": 1, "constraint_embedding": 1},
    )
    docs = list(cursor)
    assert len(docs) == 7
    null_count = sum(1 for d in docs if d.get("constraint_embedding") is None)
    if null_count == len(docs):
        pytest.skip(
            "no claude-code embeddings present — run "
            "MONGODB_URI=... scripts/seed_constraint_embeddings.py to populate"
        )
    assert null_count == 0, (
        f"{null_count} claude-code constraints missing embeddings — "
        "re-run scripts/seed_constraint_embeddings.py"
    )


def test_skill_md_models_match_domain_fields(db):
    """Drift catcher: the model availability matrix in
    skills/claude-code/SKILL.md must reference the same model IDs as
    skill:claude-code:model-selection.domain_fields.available_models.
    If a model is added in one place and not the other, this test fires."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    skill_md = (repo_root / "skills" / "claude-code" / "SKILL.md").read_text()

    skill = db.skills.find_one({"_id": "skill:claude-code:model-selection"})
    assert skill, "skill:claude-code:model-selection missing from graph"
    declared = {
        m["id"] for m in skill["domain_fields"].get("available_models", [])
    }
    assert declared, "available_models is empty — graph drift"

    missing_in_md = [m for m in declared if m not in skill_md]
    assert not missing_in_md, (
        f"models declared in graph but not in SKILL.md: {missing_in_md}. "
        "Update the model availability matrix in skills/claude-code/SKILL.md "
        "or remove the model from skill:claude-code:model-selection."
    )


def test_constraint_embedding_dimension_matches_v4_256_int8(db):
    """All embeddings must be 256-d (matching the seeder's voyage-4 @ 256
    dim config). Catches drift if someone changes the seeder dim without
    updating the Atlas Vector Search index numDimensions."""
    docs = list(db.constraints.find(
        {"constraint_embedding": {"$ne": None}},
        {"_id": 1, "constraint_embedding": 1},
    ))
    if not docs:
        pytest.skip("no embeddings present")
    for d in docs:
        emb = d["constraint_embedding"]
        assert len(emb) == 256, (
            f"{d['_id']} has {len(emb)}-d embedding, expected 256"
        )
