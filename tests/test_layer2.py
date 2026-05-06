"""Layer 2 semantic validation — threshold logic and structure tests.

Pure-logic tests (no MongoDB, no API keys needed). Atlas $vectorSearch
integration is not tested here — it requires a live Atlas cluster with
the constraint_embedding_index in READY state.
"""
# ── threshold logic ──────────────────────────────────────────────────────────

import query_layer2 as l2


def test_verdict_clean():
    assert l2._verdict(0.0) == "clean"
    assert l2._verdict(0.50) == "clean"
    assert l2._verdict(0.68) == "clean"
    assert l2._verdict(l2.LOW_THRESHOLD - 0.001) == "clean"


def test_verdict_flag():
    assert l2._verdict(l2.LOW_THRESHOLD) == "flag"
    assert l2._verdict(0.71) == "flag"
    assert l2._verdict(l2.HIGH_THRESHOLD - 0.001) == "flag"


def test_verdict_escalate():
    assert l2._verdict(l2.HIGH_THRESHOLD) == "escalate"
    assert l2._verdict(0.73) == "escalate"
    assert l2._verdict(1.0) == "escalate"


def test_thresholds_ordered():
    assert 0.0 < l2.LOW_THRESHOLD < l2.HIGH_THRESHOLD < 1.0


def test_verdict_boundary_low():
    # boundary: LOW_THRESHOLD itself → flag, not clean
    assert l2._verdict(l2.LOW_THRESHOLD) == "flag"


def test_verdict_boundary_high():
    # boundary: HIGH_THRESHOLD itself → escalate, not flag
    assert l2._verdict(l2.HIGH_THRESHOLD) == "escalate"


def test_summary_counts_match_checks():
    """summary counts must sum to total and agree with the checks array."""
    # calibrated thresholds: LOW=0.70, HIGH=0.725
    scores = [0.50, 0.71, 0.73, 0.68, 0.726]
    checks = [{"verdict": l2._verdict(s)} for s in scores]
    # 0.50 → clean, 0.71 → flag, 0.73 → escalate, 0.68 → clean, 0.726 → escalate
    summary = {
        "total": len(checks),
        "escalate": sum(1 for c in checks if c["verdict"] == "escalate"),
        "flag": sum(1 for c in checks if c["verdict"] == "flag"),
        "clean": sum(1 for c in checks if c["verdict"] == "clean"),
    }
    assert summary["total"] == 5
    assert summary["clean"] == 2
    assert summary["flag"] == 1
    assert summary["escalate"] == 2
    assert summary["clean"] + summary["flag"] + summary["escalate"] == summary["total"]


# ── script importability ─────────────────────────────────────────────────────

def test_query_layer2_importable():
    import query_layer2  # noqa: F401


def test_create_atlas_indexes_importable():
    import create_atlas_indexes  # noqa: F401


# ── graceful degradation without Voyage API key ──────────────────────────────

def test_run_layer2_fails_gracefully_without_voyage_key(tmp_path, monkeypatch):
    """Returns error dict (not exception) when VOYAGE_API_KEY is absent
    and OPENROUTER_API_KEY is absent (fact extraction fails first)."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"components": []}')

    result = l2.run_layer2("skill:leafygreen-ui", artifact)
    assert result["ok"] is False
    assert "error" in result


# ── check_constraints MCP tool surface ───────────────────────────────────────

def test_check_constraints_no_voyage_key(seeded, call, monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    result = call(seeded.check_constraints, skill_id="skill:leafygreen-ui", fact_summary="test")
    assert "error" in result
    assert "VOYAGE_API_KEY" in result["error"]
