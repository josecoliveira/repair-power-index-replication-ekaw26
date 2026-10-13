#!/usr/bin/env python3
"""CLI entry point for the analysis module.

Aggregate trial outputs into CSV, Markdown, and LaTeX reports,
including runtime complexity estimation.

Usage:
    python -m cli.analyze_results [data_dir] [out_dir]
    python cli/analyze_results.py [data_dir] [out_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as ``python cli/analyze_results.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.analyzer import run_analysis

# ── Path constants ──────────────────────────────────────────────────────
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
INCONSISTENT_DIR = PACKAGE_ROOT / "ontologies" / "inconsistent"


def main() -> None:
    if len(sys.argv) >= 2:
        data_dir = Path(sys.argv[1]).resolve()
    else:
        data_candidates = sorted(PACKAGE_ROOT.glob("data/data-*"))
        if not data_candidates:
            # Also check for legacy data-* in the repo root
            data_candidates = sorted(PACKAGE_ROOT.glob("data-*"))
        if not data_candidates:
            raise SystemExit("No data-* directory found. Pass a data directory explicitly.")
        data_dir = data_candidates[-1]

    if len(sys.argv) >= 3:
        out_dir = Path(sys.argv[2]).resolve()
    else:
        stamp = data_dir.name.replace("data-", "")
        out_dir = (PACKAGE_ROOT / "data" / f"results-{stamp}").resolve()

    # Resolve inconsistent directory (used for axiom counts in estimation)
    inconsistent_dir: Path | None = INCONSISTENT_DIR
    if not inconsistent_dir.is_dir():
        print(
            f"Warning: inconsistent directory {inconsistent_dir} does not exist; "
            f"complexity estimation may be unavailable.",
            file=sys.stderr,
        )
        inconsistent_dir = None

    outputs = run_analysis(data_dir, out_dir, inconsistent_dir=inconsistent_dir)
    print("Wrote:")
    for key, value in outputs.items():
        if key == "estimation":
            # Print estimation summary inline
            print(f"  estimation (see report for full details):")
            if not value:
                print("    N/A (not enough completed ontologies yet)")
                continue
            print(f"    best_fit  = {value.get('best_fit', 'N/A')}")
            for field in ("r2_poly", "r2_exp"):
                score = value.get(field)
                if isinstance(score, (int, float)):
                    print(f"    {field:<10}= {score:.4f}")
                else:
                    print(f"    {field:<10}= {score if score is not None else 'N/A'}")
            print(f"    conclusion= {value.get('conclusion', 'N/A')}")
        else:
            print(f"  {key}={value}")


if __name__ == "__main__":
    main()
