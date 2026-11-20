#!/usr/bin/env python3
"""CLI entry point for the ablation analysis.

Aggregate the trial outputs of an ablation run into the per-arm CSV, Markdown and
LaTeX reports. The paper's run is reported by ``cli/analyze_results.py``, which uses
its own, independent analysis.

The reports are written to ``<out_dir>-ablation`` so that a grid report never
overwrites the paper's.

Usage:
    python -m cli.analyze_results_ablation [data_dir] [out_dir]
    python cli/analyze_results_ablation.py [data_dir] [out_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as ``python cli/analyze_results_ablation.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.ablation_analysis import run_analysis
from cli.analyze_results import print_outputs, resolve_dirs, resolve_inconsistent_dir
from cli.run_trials_parallel_ablation import RESULTS_SUFFIX


def main() -> None:
    data_dir, out_dir = resolve_dirs(sys.argv[1:])
    out_dir = out_dir.parent / f"{out_dir.name}{RESULTS_SUFFIX}"
    outputs = run_analysis(data_dir, out_dir, inconsistent_dir=resolve_inconsistent_dir())
    print_outputs(outputs)


if __name__ == "__main__":
    main()
