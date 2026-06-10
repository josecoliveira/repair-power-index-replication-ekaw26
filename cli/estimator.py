#!/usr/bin/env python3
"""CLI entry point for the estimator module.

Estimate the complexity of the repair algorithm.

Usage:
    python -m cli.estimator
    python cli/estimator.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as ``python cli/estimator.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.estimator import run_estimation


def main() -> None:
    run_estimation()


if __name__ == "__main__":
    main()
