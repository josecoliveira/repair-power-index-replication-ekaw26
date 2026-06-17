#!/usr/bin/env python3
"""CLI entry point for the classifier module.

Classify all ontologies across original/cleanup/inconsistent folders
and produce a markdown table.

Usage:
    python -m cli.classify_ontologies [options]
    python cli/classify_ontologies.py [options]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running as ``python cli/classify_ontologies.py`` (without ``-m``)
# and via VS Code debugger (which may set __package__ to non-None).
if __name__ == "__main__":
    _proj_root = str(Path(__file__).resolve().parent.parent)
    if _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)

from analysis.classifier import (
    run_classification,
    DEFAULT_JAVA_MEM,
    DEFAULT_TIMEOUT,
    DEFAULT_WORKERS,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify all ontologies across original/cleanup/inconsistent "
            "folders and produce a markdown table."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s -o my_table.md\n"
            "  %(prog)s --verbose --java-mem '-Xms2g -Xmx8g'\n"
            "  %(prog)s --workers 8\n"
            "  %(prog)s --sort-by-axioms\n"
        ),
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "ontology_classification.md",
        help="Output markdown file path (default: %(default)s)",
    )
    parser.add_argument(
        "--java-mem",
        type=str,
        default=DEFAULT_JAVA_MEM,
        help=(
            "JVM memory and stack options "
            "(default: '%(default)s')"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            "Timeout per ontology in seconds "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed progress and Java subprocess output.",
    )
    parser.add_argument(
        "-j", "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Number of parallel workers "
            "(default: %(default)s, i.e. CPUs)."
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help=(
            "Disable the live-progress display; "
            "fall back to simple log output."
        ),
    )
    parser.add_argument(
        "--sort-by-axioms",
        action="store_true",
        help=(
            "Sort ontologies by axiom count (smallest first) "
            "instead of alphabetically."
        ),
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run_classification(args)


if __name__ == "__main__":
    main()
