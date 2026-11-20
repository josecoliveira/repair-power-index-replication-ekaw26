#!/usr/bin/env python3
"""CLI entry point of the nine-arm ablation grid.

WORK IN PROGRESS. The ablation is not part of the paper's replication, and neither its
numbers nor the shape of its output should be presented as the paper's. See
``DOCUMENTATION.md`` for what the grid is.

Drives ``SingleTrialAblationExperiment`` (A1, A2, A3 and the nine indexed arms B1-B9) and
reports with :mod:`analysis.ablation_analysis`, into ``data/results-<run-id>-ablation/``.

Usage:
    python -m cli.run_trials_ablation [options]
    python cli/run_trials_ablation.py [options]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running as ``python cli/run_trials_ablation.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.ablation_runner import EXPERIMENT_CLASS, run_experiment
from cli.run_trials import build_parser

#: Trials per ontology by default: the grid is an exploration, not a publication run.
N_TRIALS_PER_ONTOLOGY = 10


def main() -> None:
    print(
        "[ablation] Work in progress - these numbers are not the paper's replication.",
        file=sys.stderr,
    )
    parser = build_parser()
    parser.description = (
        "Run the nine-arm ablation grid (A1-A3 plus the B1-B9 arms) on the corpus. "
        "Work in progress: not the paper's replication."
    )
    parser.set_defaults(n_trials=N_TRIALS_PER_ONTOLOGY)
    args = parser.parse_args()
    # Pin the Java app: a grid run always drives the grid experiment.
    os.environ["ONTOLOGYUTILS_EXPERIMENT_CLASS"] = EXPERIMENT_CLASS
    run_experiment(args)


if __name__ == "__main__":
    main()
