#!/usr/bin/env python3
"""Facade over the analysis pipeline used by the replication package.

The reports are produced by :mod:`analysis.paper_analysis`, which reproduces the
output of the published analysis: an IIC table per baseline, a runtime table per
repair method and an outcome table, written as CSV, Markdown and LaTeX.

This module only keeps the entry points the rest of the package imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from analysis import paper_analysis

#: The arm the paper compares, reported under the canonical name ``power``.
PAPER_ARM = "B5"


def discover_csvs(data_dir: Path, kind: str) -> list[Path]:
    """Return the ``kind`` CSVs (``iic`` or ``runtime``) found in ``data_dir``."""
    return paper_analysis.discover_csvs(kind, Path(data_dir))


def load_runtime_rows(paths: Iterable[Path]) -> pd.DataFrame:
    """Load the runtime CSVs of several ontologies into one frame."""
    return paper_analysis.load_runtime_frames(paths)


def run_analysis(
    data_dir: Path,
    out_dir: Path,
    inconsistent_dir: Path | None = None,
    arm: str = PAPER_ARM,
) -> dict:
    """Aggregate the trials of one run into the report files of ``out_dir``.

    :param data_dir: Directory holding the ``iic-*.csv`` and ``runtime-*.csv`` files of one run.
    :param out_dir: Directory the summaries and reports are written to.
    :param inconsistent_dir: Accepted for backwards compatibility; unused.
    :param arm: The arm of the grid to report as the power-index repair.
    :returns: The paths of the files written, or an empty mapping if the run has no trials yet.
    """
    return paper_analysis.run_analysis(data_dir, out_dir, inconsistent_dir=inconsistent_dir, arm=arm)
