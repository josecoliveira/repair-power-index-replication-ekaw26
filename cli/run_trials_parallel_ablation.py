#!/usr/bin/env python3
"""CLI entry point for the parallel orchestrator over the full arm grid.

Same trial engine as ``cli/run_trials_parallel.py``, but it drives
``SingleTrialAblationExperiment`` (A1, A2, A3 and the nine indexed arms B1-B9) and
reports with :mod:`analysis.ablation_analysis` instead of the paper's analysis.

The two reports are independent: this module is the only place that knows an
ablation run exists, so neither analysis has to inspect the environment to find out
which experiment produced the data.

Usage:
    python -m cli.run_trials_parallel_ablation [options]
    python cli/run_trials_parallel_ablation.py [options]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running as ``python cli/run_trials_parallel_ablation.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis import ablation_analysis
from analysis.parallel_orchestrator import run_experiment_parallel
from cli.run_trials_parallel import build_parser

#: Suffix appended to the results directory, so the grid never overwrites the paper's report.
RESULTS_SUFFIX = "-ablation"

#: The experiment the trial runner must launch.
EXPERIMENT_CLASS = "www.ontologyutils.apps.SingleTrialAblationExperiment"


def main() -> None:
    # The service reads this to pick the Java app; the analysis is passed in
    # explicitly below and never looks at the environment.
    os.environ.setdefault("ONTOLOGYUTILS_EXPERIMENT_CLASS", EXPERIMENT_CLASS)
    args = build_parser().parse_args()
    run_experiment_parallel(args, ablation_analysis.run_analysis, RESULTS_SUFFIX)


if __name__ == "__main__":
    raise SystemExit(main())
