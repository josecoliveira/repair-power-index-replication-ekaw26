#!/usr/bin/env python3
"""CLI entry point for the parallel orchestrator over the full arm grid.

Same orchestrator as ``cli/run_trials_parallel.py``, but it drives
``SingleTrialAblationExperiment`` (A1, A2, A3 and the nine indexed arms B1-B9)
instead of the four-arm ``SingleTrialExperiment`` used by the paper's comparison.

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

# Must be set before ``analysis`` is imported: the service reads the experiment
# class, and the analysis uses it to pick the report of the ablation grid.
os.environ.setdefault(
    "ONTOLOGYUTILS_EXPERIMENT_CLASS", "www.ontologyutils.apps.SingleTrialAblationExperiment"
)
# The reports of an ablation run are written to ``<results-dir>-ablation`` and
# cover the whole B1-B9 grid, so they never touch the paper's output.

from cli import run_trials_parallel  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_trials_parallel.main())
