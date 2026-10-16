#!/usr/bin/env python3
"""CLI entry point for the parallel experiment orchestrator.

Like ``cli/run_trials.py``, but runs up to ``--parallel-trials K`` Java trials
concurrently for the same ontology.  The sequential runner remains the
reference implementation; see ``analysis/parallel_orchestrator.py`` for the
design and the differences.

Usage:
    python -m cli.run_trials_parallel [options]
    python cli/run_trials_parallel.py [options]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python cli/run_trials_parallel.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.orchestrator import INCONSISTENT_DIR
from analysis.ontologyutils_service import LIB_DIR
from analysis.parallel_orchestrator import run_experiment_parallel
from cli.run_trials import (
    ANALYSIS_INTERVAL_ROUNDS,
    BASE_SEED,
    CONSECUTIVE_FAILURE_LIMIT,
    DEFAULT_JAVA_MEM,
    MAKE_INCONSISTENT_TIMEOUT_SECONDS,
    N_TRIALS_PER_ONTOLOGY,
    POWER_INDEX_TIMEOUT_SECONDS,
    REMOVAL_TIMEOUT_SECONDS,
    STEP,
    WEAKENING_TIMEOUT_SECONDS,
)

# ── Default configuration (CONFIGURE HERE) ─────────────────────────────
PARALLEL_TRIALS = 1
THREADS_PER_TRIAL = 16


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Java trials with several of them in flight at once for the "
            "same ontology, and collect the same A/B results as the "
            "sequential runner."
        ),
    )

    # --- Parallelism ---
    parser.add_argument(
        "-K", "--parallel-trials",
        type=int, default=PARALLEL_TRIALS,
        help=(
            "Number of trials to keep in flight for one ontology "
            "(default: %(default)s). 1 behaves exactly like the sequential runner."
        ),
    )
    parser.add_argument(
        "-t", "--threads-per-trial",
        type=int, default=THREADS_PER_TRIAL,
        help=(
            "Sampling threads per trial JVM, passed as "
            "-Dontologyutils.samplingThreads (default: %(default)s). "
            "Keep K * t within the machine's CPU budget."
        ),
    )
    parser.add_argument(
        "--cap-jvm-cpus",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Also pass -XX:ActiveProcessorCount=<t> so each JVM sizes its own "
            "thread pools (GC, ForkJoin) for its share of the budget instead of "
            "the whole machine (default: %(default)s)."
        ),
    )

    # --- Trial control ---
    parser.add_argument(
        "--ontologies",
        type=str, default=None,
        help=(
            "Comma-separated ontology names to run (e.g. --ontologies OGR,XEO). "
            "Default: every ontology in --inconsistent-dir. Restricting the set "
            "changes the seed indexing, so all configurations being compared "
            "must use the same set."
        ),
    )
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
        help=(
            "Run the interim analysis after every K completed ontologies; "
            "0 disables interim analysis, leaving only the final analysis "
            "(default: %(default)s)"
        ),
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
    parser.add_argument(
        "--consecutive-failure-limit",
        type=int, default=CONSECUTIVE_FAILURE_LIMIT,
        help="Abort ontology after this many consecutive failures (default: %(default)s)",
    )

    # --- Notifications ---
    notify_group = parser.add_mutually_exclusive_group()
    notify_group.add_argument(
        "--notify",
        dest="notify",
        action="store_true",
        default=None,
        help=(
            "Force Telegram progress notifications on. Without this flag, "
            "notifications are enabled automatically when TELEGRAM_BOT_TOKEN "
            "and TELEGRAM_CHAT_ID resolve (environment or .env file)."
        ),
    )
    notify_group.add_argument(
        "--no-notify",
        dest="notify",
        action="store_false",
        help="Disable Telegram progress notifications for this run.",
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
            "Output data directory. If --run-id is given but --data-dir is not, "
            "defaults to data/data-<run-id>."
        ),
    )
    parser.add_argument(
        "--results-dir",
        type=Path, default=None,
        help=(
            "Output results directory. If --run-id is given but --results-dir is "
            "not, defaults to data/results-<run-id>."
        ),
    )
    parser.add_argument(
        "--java-mem",
        type=str, default=DEFAULT_JAVA_MEM,
        help=(
            "JVM memory and stack options passed directly to java "
            "(default: '%(default)s'). The sampling-thread property is appended "
            "automatically."
        ),
    )

    # --- Resume / Run identity ---
    parser.add_argument(
        "--run-id",
        type=str, default=None,
        help=(
            "Run identifier. Auto-generated as timestamp if not provided. "
            "When resuming, point this to the existing run ID."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int, default=None,
        help="Explicit seed override (sets the base seed for the run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the effective configuration and exit without running any experiments.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run_experiment_parallel(args)


if __name__ == "__main__":
    main()
