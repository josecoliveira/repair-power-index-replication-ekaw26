#!/usr/bin/env python3
"""CLI entry point for the paper's analysis.

Aggregate the trial outputs of a paper run into the CSV, Markdown and LaTeX
reports. An ablation run is reported by ``cli/analyze_results_ablation.py``, which
uses its own, independent analysis.

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

from analysis.analysis import run_analysis

# ── Path constants ─────────────────────────────────────────────────────────
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
INCONSISTENT_DIR = PACKAGE_ROOT / "ontologies" / "inconsistent"


def default_data_dir() -> Path:
    """The newest ``data/data-*`` directory, or the newest legacy ``data-*``."""
    data_candidates = sorted(PACKAGE_ROOT.glob("data/data-*"))
    if not data_candidates:
        data_candidates = sorted(PACKAGE_ROOT.glob("data-*"))
    if not data_candidates:
        raise SystemExit("No data-* directory found. Pass a data directory explicitly.")
    return data_candidates[-1]


def resolve_dirs(argv: list[str]) -> tuple[Path, Path]:
    """Resolve the ``[data_dir] [out_dir]`` arguments, defaulting to the newest run."""
    data_dir = Path(argv[0]).resolve() if len(argv) >= 1 else default_data_dir()
    if len(argv) >= 2:
        out_dir = Path(argv[1]).resolve()
    else:
        stamp = data_dir.name.replace("data-", "")
        out_dir = (PACKAGE_ROOT / "data" / f"results-{stamp}").resolve()
    return data_dir, out_dir


def resolve_inconsistent_dir() -> Path | None:
    """The corpus directory, used for axiom counts in the complexity estimation."""
    if INCONSISTENT_DIR.is_dir():
        return INCONSISTENT_DIR
    print(
        f"Warning: inconsistent directory {INCONSISTENT_DIR} does not exist; "
        f"complexity estimation may be unavailable.",
        file=sys.stderr,
    )
    return None


def print_outputs(outputs: dict) -> None:
    """Print the files an analysis wrote, plus a summary of its estimation."""
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


def main() -> None:
    data_dir, out_dir = resolve_dirs(sys.argv[1:])
    outputs = run_analysis(data_dir, out_dir, inconsistent_dir=resolve_inconsistent_dir())
    print_outputs(outputs)


if __name__ == "__main__":
    main()
