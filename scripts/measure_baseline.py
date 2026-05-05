#!/usr/bin/env python3
"""Compute the file-read baseline for Blog 1 / R1.5.

When the MCP server is not registered, the agent has no choice but to
read repo files directly to answer the same questions. This script models
that cost: for each R1 prompt, declare the files the agent would need to
load if it were grepping/reading instead of querying the typed graph.

The result is a defensible upper bound for the unparameterized path —
"if the agent reads any of these files at all, the cost is at least X
tokens." Real Read tool calls may chunk; agents often re-read the same
file across prompts, which approximates whole-file cost in practice.

Two modes:

    # 1. Modeled baseline using the built-in manifest:
    python scripts/measure_baseline.py

    # 2. Validate against actual R1.5 transcript file paths:
    python scripts/measure_baseline.py --input r1.5-paths.json
    # Where r1.5-paths.json looks like:
    #   {"1": ["schema/skills.json"],
    #    "2": ["schema/skills.json"],
    #    ...,
    #    "6": []}

Output mirrors `scripts/analyze.py`. Pair with:

    python scripts/measure_baseline.py
    python scripts/analyze.py --session $SESSION_ID --all

…to produce the side-by-side comparison Blog 1 needs.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Modeled baseline — what files the agent would load via Read/grep to
# answer each R1 prompt without the MCP server. Conservative: assumes
# the canonical seed file (schema/skills.json) gets loaded once per
# prompt that touches skill metadata, and that prompts about tenant
# overrides additionally load schema/parameters.json. Prompt 6 mirrors
# R1's observed behavior (cached context, zero new reads).
MODELED_MANIFEST: dict[str, list[str]] = {
    "1. tested UI from raw user requirements": ["schema/skills.json"],
    "2. list every active skill":              ["schema/skills.json"],
    "3. impact of changing schema-review":     ["schema/skills.json"],
    "4. dark login for client-a":              ["schema/skills.json", "schema/parameters.json"],
    "5. same form for client-b":               ["schema/skills.json", "schema/parameters.json"],
    "6. compare client-a vs client-b tokens":  [],
    "7. what broke schema-review-v1":          ["schema/skills.json"],
    "8. plan chain to test_suite, validate":   ["schema/skills.json"],
}


def _bytes_of(rel: str) -> int:
    p = REPO_ROOT / rel
    if not p.is_file():
        raise FileNotFoundError(rel)
    return p.stat().st_size


def _tokens(b: int) -> int:
    """chars // 4 — same approximation server.py uses for db.runs.tokens_returned."""
    return b // 4


def _print_table(rows: list[list], headers: list[str]) -> None:
    widths = [max(len(h), *(len(str(r[i])) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    sep = " | "
    print(sep.join(h.ljust(w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))
    for r in rows:
        print(sep.join(str(c).ljust(w) for c, w in zip(r, widths)))


def render(manifest: dict[str, list[str]], r1_tokens: int | None) -> None:
    print("\n## Baseline — file-read cost per R1 prompt (no MCP server)\n")
    rows = []
    total_bytes = 0
    for prompt, paths in manifest.items():
        b = sum(_bytes_of(p) for p in paths)
        total_bytes += b
        rows.append([prompt, len(paths), b, _tokens(b),
                     ", ".join(paths) if paths else "(no reads)"])
    _print_table(rows, ["prompt", "files_read", "bytes", "tokens (~b/4)", "files"])

    total_tokens = _tokens(total_bytes)
    print(f"\n  total files read     : {sum(len(p) for p in manifest.values())}")
    print(f"  total bytes          : {total_bytes:,}")
    print(f"  total tokens (~b/4)  : {total_tokens:,}")

    if r1_tokens:
        delta = total_tokens - r1_tokens
        ratio = total_tokens / r1_tokens if r1_tokens else 0
        print(f"\n  R1 graph-path tokens : {r1_tokens:,}")
        print(f"  baseline / R1        : {ratio:.2f}x")
        print(f"  absolute delta       : {delta:+,} tokens")
        if ratio >= 1.5:
            print("  finding              : graph path delivers a meaningful efficiency win")
        elif ratio >= 1.05:
            print("  finding              : graph path is modestly tighter; relevance win remains")
        else:
            print("  finding              : graph path is not bytes-cheaper; the win must be in relevance")


def load_manifest(path: str | None) -> dict[str, list[str]]:
    if not path:
        return MODELED_MANIFEST
    raw = json.loads(Path(path).read_text())
    # Accept either {"1": [...]} or {"1. label": [...]}
    return {k: v for k, v in raw.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", help="JSON map of prompt -> [file paths]; "
                                    "overrides the built-in MODELED_MANIFEST")
    ap.add_argument("--r1-tokens", type=int, default=3675,
                    help="Total graph-path tokens from analyze.py blog2 row "
                         "to compare against (default: R1 measured 3675)")
    args = ap.parse_args()

    try:
        manifest = load_manifest(args.input)
    except FileNotFoundError as e:
        print(f"input file not found: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        render(manifest, args.r1_tokens)
    except FileNotFoundError as e:
        print(f"manifest references missing file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
