#!/usr/bin/env python3
"""CLI entry point for the estimator module.

Estimate the complexity of the repair algorithm from experimental data.

Usage (standalone):
    python -m cli.estimator
    python cli/estimator.py
    python cli/estimator.py --run-id 20260530053655536655
    python cli/estimator.py --data-dir data/data-20260530053655536655

When called without arguments, the latest data-* directory is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python cli/estimator.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.analyzer import discover_csvs, load_runtime_rows
from analysis.estimator import (
    estimate_complexity,
    load_axiom_counts,
    run_estimation,
)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = PACKAGE_ROOT / "data"
INCONSISTENT_DIR = PACKAGE_ROOT / "ontologies" / "inconsistent"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate repair algorithm complexity from experimental "
            "runtime data. If no arguments are given, the latest "
            "data-* directory is used."
        ),
    )

    # Data source (mutually exclusive-ish — data-dir takes precedence)
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run identifier (e.g. 20260530053655536655). "
             "Resolves to data/data-<run-id>.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Path to a data-<timestamp> directory containing runtime CSVs.",
    )
    parser.add_argument(
        "--inconsistent-dir",
        type=Path,
        default=INCONSISTENT_DIR,
        help=(
            "Path to the inconsistent/ ontology directory, used for "
            "axiom count resolution (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use hardcoded legacy data points instead of experimental data.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ── Legacy mode ─────────────────────────────────────────────────
    if args.legacy:
        run_estimation()
        return

    # ── Resolve data directory ──────────────────────────────────────
    data_dir: Path | None = args.data_dir

    if data_dir is None and args.run_id is not None:
        candidate = ANALYSIS_DIR / f"data-{args.run_id}"
        if candidate.is_dir():
            data_dir = candidate
        else:
            print(
                f"Error: data directory {candidate} not found.",
                file=sys.stderr,
            )
            sys.exit(1)

    if data_dir is None:
        # Use the latest data-* directory
        candidates = sorted(ANALYSIS_DIR.glob("data-*"))
        if not candidates:
            print(
                "Error: no data-* directories found. "
                "Pass --data-dir or --run-id explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)
        data_dir = candidates[-1]
        print(f"Using latest data directory: {data_dir}")

    # ── Load runtime CSVs ───────────────────────────────────────────
    runtime_paths = discover_csvs(data_dir, "runtime")
    if not runtime_paths:
        print(
            f"Error: no runtime CSVs found in {data_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    runtime_df = load_runtime_rows(runtime_paths)
    print(f"Loaded {len(runtime_paths)} runtime CSV(s) from {data_dir}")
    print(f"  Ontologies: {sorted(runtime_df['ontology'].unique())}")
    print(f"  Total rows: {len(runtime_df)}")

    # ── Resolve axiom counts ────────────────────────────────────────
    inconsistent_dir: Path | None = args.inconsistent_dir
    if inconsistent_dir is not None and not inconsistent_dir.is_dir():
        print(
            f"Warning: inconsistent directory {inconsistent_dir} "
            f"does not exist; axiom counts may be unavailable.",
            file=sys.stderr,
        )
        inconsistent_dir = None

    axiom_counts = load_axiom_counts(
        names=runtime_df["ontology"].unique().tolist(),
        inconsistent_dir=inconsistent_dir,
    )

    # ── Estimate complexity ─────────────────────────────────────────
    result = estimate_complexity(runtime_df, axiom_counts=axiom_counts)

    # ── Print results ───────────────────────────────────────────────
    print()
    print("=" * 60)
    print("COMPLEXITY ESTIMATION RESULTS")
    print("=" * 60)
    print()

    data_points = result.get("data_points", [])
    if data_points:
        print(f"{'Ontology':<12} {'Axioms':>8} {'Mean Runtime (s)':>18}")
        print("-" * 40)
        for dp in data_points:
            print(
                f"{dp['ontology']:<12} {dp['axioms']:>8} "
                f"{dp['mean_runtime_s']:>14.2f}"
            )
        print()
    else:
        print("No data points available.")
        print()

    print(f"Polynomial Fit (log-log):")
    print(f"  R² = {result['r2_poly']:.4f}")
    print(f"  Estimated exponent k = {result['k']:.2f}")
    print(f"  → {result['poly_equation']}")
    print()
    print(f"Exponential Fit (semi-log):")
    print(f"  R² = {result['r2_exp']:.4f}")
    print(f"  Estimated base b = {result['b']:.2f}")
    print(f"  → {result['exp_equation']}")
    print()
    print(f"Conclusion: {result['conclusion']}")


if __name__ == "__main__":
    main()
