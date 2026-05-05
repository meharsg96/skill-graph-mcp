"""measure_baseline.py — file-read baseline for Blog 1 / R1.5.

The script models the bytes/tokens cost of answering R1's 8 prompts via
file reads instead of MCP tool calls. Tests verify the manifest matches
the actual files on disk and that bytes->tokens math is correct.
"""

import json
from pathlib import Path

import measure_baseline


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_modeled_manifest_only_references_present_files():
    for prompt, paths in measure_baseline.MODELED_MANIFEST.items():
        for p in paths:
            assert (REPO_ROOT / p).is_file(), f"{prompt}: {p} missing"


def test_bytes_of_returns_filesystem_size():
    skills = REPO_ROOT / "schema" / "skills.json"
    assert measure_baseline._bytes_of("schema/skills.json") == skills.stat().st_size


def test_bytes_of_unknown_path_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        measure_baseline._bytes_of("schema/does-not-exist.json")


def test_tokens_is_chars_div_four():
    # Matches server.py:log_tool_call's tokens_returned approximation
    assert measure_baseline._tokens(0) == 0
    assert measure_baseline._tokens(7) == 1
    assert measure_baseline._tokens(8) == 2
    assert measure_baseline._tokens(8172) == 2043


def test_load_manifest_default_returns_modeled():
    assert measure_baseline.load_manifest(None) is measure_baseline.MODELED_MANIFEST


def test_load_manifest_from_json_file(tmp_path):
    f = tmp_path / "paths.json"
    f.write_text(json.dumps({
        "p1": ["schema/skills.json"],
        "p2": [],
    }))
    m = measure_baseline.load_manifest(str(f))
    assert m == {"p1": ["schema/skills.json"], "p2": []}


def test_render_total_matches_sum_of_per_prompt(capsys):
    """Total tokens reported equals sum across the manifest. Catches
    accidental off-by-one or double-counting."""
    measure_baseline.render(measure_baseline.MODELED_MANIFEST, r1_tokens=None)
    out = capsys.readouterr().out
    assert "total bytes" in out
    assert "total tokens" in out
    # Sanity: non-empty manifest produces non-zero totals
    expected_bytes = sum(
        (REPO_ROOT / p).stat().st_size
        for paths in measure_baseline.MODELED_MANIFEST.values()
        for p in paths
    )
    assert f"{expected_bytes:,}" in out


def test_render_with_r1_comparison_emits_ratio(capsys):
    measure_baseline.render(measure_baseline.MODELED_MANIFEST, r1_tokens=3675)
    out = capsys.readouterr().out
    assert "baseline / R1" in out
    assert "absolute delta" in out
    assert "finding" in out


def test_render_without_r1_comparison_skips_delta(capsys):
    measure_baseline.render(measure_baseline.MODELED_MANIFEST, r1_tokens=None)
    out = capsys.readouterr().out
    assert "baseline / R1" not in out


def test_modeled_baseline_currently_exceeds_r1_measurement():
    """The whole point of the comparison: file-read path costs more
    tokens than the graph path. If this ever fails, either the agent's
    real-usage cost ballooned (bad) or the schema files shrank below
    the per-call MCP response size (very surprising)."""
    total_b = sum(
        (REPO_ROOT / p).stat().st_size
        for paths in measure_baseline.MODELED_MANIFEST.values()
        for p in paths
    )
    assert measure_baseline._tokens(total_b) > 3675
