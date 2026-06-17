#!/usr/bin/env python3
"""CLI entry point for the experiment orchestrator.

Run round-robin single-trial Java experiments for all ontologies and collect A/B results.

Usage:
    python -m cli.run_trials [--dry-run]
    python cli/run_trials.py [options]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python cli/run_trials.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.orchestrator import run_experiment, INCONSISTENT_DIR
from analysis.ontologyutils_service import LIB_DIR

# ── Default configuration (CONFIGURE HERE) ─────────────────────────────
N_TRIALS_PER_ONTOLOGY = 100
BASE_SEED = 13
STEP = 100
ANALYSIS_INTERVAL_ROUNDS = 1
REMOVAL_TIMEOUT_SECONDS = 300
WEAKENING_TIMEOUT_SECONDS = 300
POWER_INDEX_TIMEOUT_SECONDS = 300
MAKE_INCONSISTENT_TIMEOUT_SECONDS = 300
DEFAULT_JAVA_MEM = "-Xms1g -Xmx64g -Xss8m"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with local default values."""
    parser = argparse.ArgumentParser(
        description=(
            "Run round-robin single-trial Java experiments for all ontologies "
            "and collect A/B results."
        ),
    )

    # --- Trial control ---
    parser.add_argument(
        "-n", "--n-trials",
        type=int, default=N_TRIALS_PER_ONTOLOGY,
        help="Target number of successful trials per ontology (default: %(default)s)",
    )
    parser.add_argument(
        "--base-seed",
        type=int, default=BASE_SEED,
        help="Base random seed (default: %(default)s)",
    )
    parser.add_argument(
        "--step",
        type=int, default=STEP,
        help="Seed step between attempts (default: %(default)s)",
    )
    parser.add_argument(
        "--analysis-interval",
        type=int, default=ANALYSIS_INTERVAL_ROUNDS,
        help="Run analysis every K rounds; 0 to disable (default: %(default)s)",
    )

    # --- Timeouts ---
    parser.add_argument(
        "--removal-timeout",
        type=int, default=REMOVAL_TIMEOUT_SECONDS,
        help="Removal repair timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--weakening-timeout",
        type=int, default=WEAKENING_TIMEOUT_SECONDS,
        help="Weakening repair timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--power-index-timeout",
        type=int, default=POWER_INDEX_TIMEOUT_SECONDS,
        help="Power index computation timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--make-inconsistent-timeout",
        type=int, default=MAKE_INCONSISTENT_TIMEOUT_SECONDS,
        help="Make-inconsistent timeout in seconds (default: %(default)s)",
    )

    # --- Path overrides ---
    parser.add_argument(
        "--inconsistent-dir",
        type=Path, default=INCONSISTENT_DIR,
        help="Directory containing inconsistent ontologies (default: %(default)s)",
    )
    parser.add_argument(
        "--lib-dir",
        type=Path, default=LIB_DIR,
        help="Directory containing the shaded jar (default: %(default)s)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path, default=None,
        help=(
            "Output data directory. "
            "If --run-id is given but --data-dir is not, defaults to data/data-<run-id>. "
            "Otherwise defaults to data/data-<auto-run-id>."
        ),
    )
    parser.add_argument(
        "--results-dir",
        type=Path, default=None,
        help=(
            "Output results directory. "
            "If --run-id is given but --results-dir is not, defaults to data/results-<run-id>. "
            "Otherwise defaults to data/results-<auto-run-id>."
        ),
    )
    parser.add_argument(
        "--java-mem",
        type=str, default=DEFAULT_JAVA_MEM,
        help=(
            "JVM memory and stack options passed directly to java "
            "(default: '%(default)s')."
        ),
    )

    # --- Resume / Run identity ---
    parser.add_argument(
        "--run-id",
        type=str, default=None,
        help=(
            "Run identifier. Auto-generated as timestamp if not provided. "
            "When resuming an existing experiment, point this to the existing run ID "
            "and the script will continue from where it left off."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int, default=None,
        help=(
            "Explicit seed override. When used with --run-id, this sets the base seed "
            "for the resumed run. When omitted during resume, the base seed from the "
            "CONFIGURE HERE section is used."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the effective configuration and exit without running any experiments.",
    )

    # --- Positional (backward compatible) ---
    parser.add_argument(
        "n_trials_pos",
        type=int, nargs="?",
        help="[DEPRECATED] Positional argument for N_TRIALS_PER_ONTOLOGY",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run_experiment(args)


if __name__ == "__main__":
    main()
