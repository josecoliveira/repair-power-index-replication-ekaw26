#!/usr/bin/env python3
"""CLI entry point of the paper's experiment.

Runs the trials that produce the paper's numbers: for each ontology in the corpus, up to
``-K`` ``SingleTrialExperiment`` trials in flight, each with ``-t`` sampling threads, until
``-n`` trials succeed. The results are recorded for the three baselines and the Shapley
repair, and the paper's report is refreshed after every ``--analysis-interval`` ontologies
and once at the end.

``-K 1 -t 8`` is the safe choice on a single desktop and the setting the README uses.

Usage:
    python -m cli.run_trials [options]
    python cli/run_trials.py [options]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running as ``python cli/run_trials.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.ontologyutils_service import LIB_DIR
from analysis.runner import EXPERIMENT_CLASS, INCONSISTENT_DIR, run_experiment

# ── Default configuration (CONFIGURE HERE) ─────────────────────────────
N_TRIALS_PER_ONTOLOGY = 100
BASE_SEED = 13
STEP = 100
ANALYSIS_INTERVAL_ROUNDS = 1
REMOVAL_TIMEOUT_SECONDS = 300
WEAKENING_TIMEOUT_SECONDS = 300
POWER_INDEX_TIMEOUT_SECONDS = 300
MAKE_INCONSISTENT_TIMEOUT_SECONDS = 300
CONSECUTIVE_FAILURE_LIMIT = 10
DEFAULT_JAVA_MEM = "-Xms1g -Xmx64g -Xss8m"
PARALLEL_TRIALS = 1
THREADS_PER_TRIAL = 8


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of the paper's runner."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the paper's experiment: Java trials for every ontology of the "
            "corpus, up to -K in flight for the same ontology, and collect the "
            "results against the three baselines. Ontologies are processed one at "
            "a time in order of increasing axiom count."
        ),
    )

    # --- Parallelism ---
    parser.add_argument(
        "-K", "--parallel-trials",
        type=int, default=PARALLEL_TRIALS,
        help=(
            "Number of trials to keep in flight for one ontology (default: "
            "%(default)s). 1 runs them one at a time."
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
    args = build_parser().parse_args()
    # Pin the Java app: the paper's runner always drives the paper's experiment, whatever
    # the surrounding environment says.
    os.environ["ONTOLOGYUTILS_EXPERIMENT_CLASS"] = EXPERIMENT_CLASS
    run_experiment(args)


if __name__ == "__main__":
    main()
