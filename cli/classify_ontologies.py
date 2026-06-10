#!/usr/bin/env python3
"""CLI entry point for the classifier module.

Classify all ontologies across original/cleanup/inconsistent folders
and produce a markdown table.

Usage:
    python -m cli.classify_ontologies [--output TABLE.md]
    python cli/classify_ontologies.py [options]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python cli/classify_ontologies.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.classifier import (
    run_classification,
    DEFAULT_JAVA_MEM,
    DEFAULT_TIMEOUT,
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
        help="Print progress to stderr.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run_classification(args)


if __name__ == "__main__":
    main()
