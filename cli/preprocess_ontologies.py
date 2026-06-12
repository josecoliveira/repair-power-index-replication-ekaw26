#!/usr/bin/env python3
"""CLI entry point for the preprocessor module.

Pre-process ontologies from the ekaw26 test resources in parallel.

3-stage pipeline:
  1. CleanupOntology:      original/{name}.owl  ->  cleanup/{name}.owl
  2. Classify & Filter:    cleanup/{name}.owl  ->  alc/{name}.owl (ALC only)
  3. MakeInconsistent:     alc/{name}.owl      ->  inconsistent/{name}.owl

Usage:
    python -m cli.preprocess_ontologies [options] [ontology_name ...]
    python cli/preprocess_ontologies.py [options] [ontology_name ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python cli/preprocess_ontologies.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.preprocessor import (
    run_preprocessing,
    DEFAULT_JAVA_MEM,
    DEFAULT_WORKERS,
    list_original_ontologies,
    ALC_DIR,
    CLEANUP_DIR,
    INCONSISTENT_DIR,
    ORIGINAL_DIR,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-process ontologies: cleanup, ALC-filter, then make inconsistent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s bctt elig\n"
            "  %(prog)s --verbose co-wheat\n"
            "  %(prog)s --dry-run\n"
            "  %(prog)s --force\n"
            "  %(prog)s --workers 8\n"
        ),
    )
    parser.add_argument(
        "ontologies",
        nargs="*",
        metavar="NAME",
        help="Ontology name(s) without .owl extension (e.g. bctt co-wheat). "
        "If omitted, all ontologies from original/ are processed.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed progress and Java subprocess output.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the operations that would be performed without executing them.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process ontologies even if the output files already exist.",
    )
    parser.add_argument(
        "--java-mem",
        default=DEFAULT_JAVA_MEM,
        help=f"JVM memory options (default: '{DEFAULT_JAVA_MEM}').",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of parallel workers (default: {DEFAULT_WORKERS}, i.e. CPUs).",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the live-progress display; fall back to simple log output.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run_preprocessing(args)


if __name__ == "__main__":
    main()
