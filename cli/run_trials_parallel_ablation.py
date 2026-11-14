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
# class and the analyzer reads the arms it should report.
os.environ.setdefault(
    "ONTOLOGYUTILS_EXPERIMENT_CLASS", "www.ontologyutils.apps.SingleTrialAblationExperiment"
)
# The analysis keeps its historical arm list (B2-B9), so the ablation reports the
# same tables it always did; B1 runs but is not summarised, exactly as before.
os.environ.setdefault("ONTOLOGYUTILS_B_REPAIRS", ",".join(f"B{i}" for i in range(2, 10)))

from cli import run_trials_parallel  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_trials_parallel.main())
