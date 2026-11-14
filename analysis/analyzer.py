#!/usr/bin/env python3
"""Facade over the analysis pipeline used by the replication package.

Two reports exist, one per experiment:

- :mod:`analysis.paper_analysis` reproduces the published analysis, with one
  power-index column compared against each baseline. It is used by the runners
  that execute the paper's arm alone.
- :mod:`analysis.ablation_analysis` reports the whole nine-arm grid. It is used
  by the ablation runner, whose reports land in ``<results-dir>-ablation`` so
  that they never overwrite the paper's output.

This module only keeps the entry points the rest of the package imports and
picks the report that matches the experiment the run is driving.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pandas as pd

from analysis import ablation_analysis, paper_analysis

#: The arm the paper compares, reported under the canonical name ``power``.
PAPER_ARM = "B5"

#: The experiment class that drives the nine-arm grid.
ABLATION_EXPERIMENT = "SingleTrialAblationExperiment"

#: Suffix appended to the results directory of an ablation run.
ABLATION_SUFFIX = "-ablation"


def discover_csvs(data_dir: Path, kind: str) -> list[Path]:
    """Return the ``kind`` CSVs (``iic`` or ``runtime``) found in ``data_dir``."""
    return paper_analysis.discover_csvs(kind, Path(data_dir))


def load_runtime_rows(paths: Iterable[Path]) -> pd.DataFrame:
    """Load the runtime CSVs of several ontologies into one frame."""
    return paper_analysis.load_runtime_frames(paths)


def is_ablation_run() -> bool:
    """Whether the current run drives the nine-arm ablation experiment."""
    return ABLATION_EXPERIMENT in os.environ.get("ONTOLOGYUTILS_EXPERIMENT_CLASS", "")


def ablation_out_dir(out_dir: Path) -> Path:
    """Where the reports of an ablation run go, next to those of the paper."""
    out_dir = Path(out_dir)
    return out_dir.parent / f"{out_dir.name}{ABLATION_SUFFIX}"


def run_analysis(
    data_dir: Path,
    out_dir: Path,
    inconsistent_dir: Path | None = None,
    arm: str = PAPER_ARM,
) -> dict:
    """Aggregate the trials of one run into the report files of ``out_dir``.

    :param data_dir: Directory holding the ``iic-*.csv`` and ``runtime-*.csv`` files of one run.
    :param out_dir: Directory the summaries and reports are written to. For an ablation run the
        reports are written to ``<out_dir>-ablation`` instead.
    :param inconsistent_dir: Directory of the corpus, used by the ablation complexity estimate.
    :param arm: The arm of the grid to report as the power-index repair; paper runs only.
    :returns: The paths of the files written, or an empty mapping if the run has no trials yet.
    """
    if is_ablation_run():
        return ablation_analysis.run_analysis(
            data_dir, ablation_out_dir(out_dir), inconsistent_dir=inconsistent_dir
        )
    return paper_analysis.run_analysis(data_dir, out_dir, inconsistent_dir=inconsistent_dir, arm=arm)
