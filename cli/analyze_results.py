#!/usr/bin/env python3
"""CLI entry point for the analysis module.

Aggregate trial outputs into CSV, Markdown, and LaTeX reports.

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


def main() -> None:
    if len(sys.argv) >= 2:
        data_dir = Path(sys.argv[1]).resolve()
    else:
        data_candidates = sorted(Path(__file__).resolve().parent.parent.glob("data/data-*"))
        if not data_candidates:
            # Also check for legacy data-* in the repo root
            data_candidates = sorted(Path(__file__).resolve().parent.parent.glob("data-*"))
        if not data_candidates:
            raise SystemExit("No data-* directory found. Pass a data directory explicitly.")
        data_dir = data_candidates[-1]

    if len(sys.argv) >= 3:
        out_dir = Path(sys.argv[2]).resolve()
    else:
        stamp = data_dir.name.replace("data-", "")
        out_dir = (Path(__file__).resolve().parent.parent / "data" / f"results-{stamp}").resolve()

    outputs = run_analysis(data_dir, out_dir)
    print("Wrote:")
    for key, value in outputs.items():
        print(f"  {key}={value}")


if __name__ == "__main__":
    main()
