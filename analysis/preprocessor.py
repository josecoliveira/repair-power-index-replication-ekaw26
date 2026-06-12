"""Pre-process ontologies through a 3-stage batch pipeline.

Pipeline (all ontologies complete each stage before the next begins):

  1. Cleanup:          original/{name}.owl  ->  cleanup/{name}.owl
  2. Classify & Filter: cleanup/{name}.owl  ->  alc/{name}.owl  (ALC only)
  3. Make Inconsistent: alc/{name}.owl      ->  inconsistent/{name}.owl

Stages 1 & 2 run on ALL ontologies; Stage 3 runs only on ALC-suitable ones,
sorted by axiom count (smallest first) so the expensive step processes
small ontologies first — interrupt-safe.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from rich.console import Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from analysis.ontologyutils_service import (
    classify_ontology,
    run_cleanup_ontology,
    run_make_inconsistent,
)

# ── Paths ──────────────────────────────────────────────────────────────
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

ORIGINAL_DIR = PACKAGE_ROOT / "ontologies" / "original"
CLEANUP_DIR = PACKAGE_ROOT / "ontologies" / "cleanup"
ALC_DIR = PACKAGE_ROOT / "ontologies" / "alc"
INCONSISTENT_DIR = PACKAGE_ROOT / "ontologies" / "inconsistent"

DEFAULT_JAVA_MEM = "-Xms1g -Xmx16g -Xss8m"
DEFAULT_WORKERS = os.cpu_count() or 4

# ── Acceptable DL languages (copied from filter_alc_ontologies.py) ─────
# Exact token matching only — "EL" will never match "ELPLUSPLUS".
ACCEPTABLE_LANGUAGES: frozenset[str] = frozenset({
    "AL",
    "ALC",
    "ALE",
    "EL",
    "FL",
    "FL0",
    "FLMINUS",
})


def is_alc_suitable(dl_languages: str) -> bool:
    """Return ``True`` if *any* DL language in the ``; ``-separated string
    is one of the acceptable languages (exact token match).

    >>> is_alc_suitable("ALC; ALCH; ALCQ")
    True
    >>> is_alc_suitable("ALCH; ALCQ; ELPLUSPLUS")
    False
    >>> is_alc_suitable("EL")
    True
    >>> is_alc_suitable("")
    False
    """
    if not dl_languages:
        return False
    languages = {lang.strip() for lang in dl_languages.split(";")}
    return bool(ACCEPTABLE_LANGUAGES & languages)


# ── Per-stage shared state ────────────────────────────────────────────

StageStep = Literal["pending", "running", "done", "failed", "unsuitable"]


@dataclass
class StageItem:
    """Status of one ontology within a single pipeline stage."""

    name: str
    step: StageStep = "pending"
    elapsed: float = 0.0
    dl_languages: str = ""
    error: str = ""
    _start_time: float = 0.0


class StageState:
    """Thread-safe container for one stage's progress across all ontologies."""

    def __init__(self, names: list[str], n_workers: int, stage_label: str):
        self._lock = threading.Lock()
        self._items: dict[str, StageItem] = {
            n: StageItem(name=n) for n in names
        }
        self._completion_order: list[str] = []
        self.total = len(names)
        self.n_workers = n_workers
        self.stage_label = stage_label
        self._progress = Progress(
            TextColumn("  "),
            BarColumn(bar_width=None),
            TextColumn("  {task.completed}/{task.total}"),
            TimeElapsedColumn(),
        )
        self._task_id = self._progress.add_task("", total=len(names))

    def update(self, name: str, **kwargs: object) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._items[name], k, v)

    def complete(self, name: str) -> None:
        with self._lock:
            self._completion_order.append(name)

    def get_completed(self) -> list[StageItem]:
        with self._lock:
            return [self._items[n] for n in self._completion_order]

    def get_active(self) -> list[StageItem]:
        with self._lock:
            terminal = ("done", "failed", "unsuitable")
            active = [
                s for s in self._items.values()
                if s.step not in terminal
            ]
            active.sort(key=lambda s: (0 if s.step != "pending" else 1, s.name))
            return active[: self.n_workers]

    def get_done_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._items.values() if s.step == "done")

    def get_progress(self) -> Progress:
        done = sum(
            1 for s in self._items.values()
            if s.step in ("done", "failed", "unsuitable")
        )
        self._progress.update(self._task_id, completed=done)
        return self._progress


# ── Display per stage ─────────────────────────────────────────────────


def render_stage_display(state: StageState) -> Group:
    """Build a Group renderable for one stage's live display."""
    completed = state.get_completed()
    active = state.get_active()

    lines: list[str] = [f"── {state.stage_label} ──"]

    for s in completed:
        if s.step == "done":
            lines.append(
                f"  {s.name:<12} done              {s.elapsed:.1f}s"
            )
        elif s.step == "unsuitable":
            lines.append(
                f"  {s.name:<12} unsuitable  [{s.dl_languages}]"
            )
        elif s.step == "failed":
            lines.append(
                f"  {s.name:<12} FAILED  ({s.error})"
            )

    for s in active:
        if s.step == "running":
            lines.append(
                f"  {s.name:<12} running ...        {s.elapsed:.1f}s"
            )
        elif s.step == "pending":
            lines.append(
                f"  {s.name:<12} pending            ---"
            )

    return Group(*lines, state.get_progress())


# ── Pure stage functions (no state awareness) ─────────────────────────


@dataclass
class FilterResult:
    """Result of the classify-and-filter stage."""
    success: bool
    dl_languages: str = ""
    is_alc: bool = False
    error: str = ""


def run_stage_cleanup(name: str, args: argparse.Namespace) -> bool:
    """Stage 1: run CleanupOntology on one ontology.
    Returns True if the cleanup output exists on exit (fresh or cached).
    """
    original_file = ORIGINAL_DIR / f"{name}.owl"
    cleanup_file = CLEANUP_DIR / f"{name}.owl"

    if not original_file.exists():
        return False

    if cleanup_file.exists() and not args.force:
        return True

    return run_cleanup_ontology(
        input_path=original_file,
        output_path=cleanup_file,
        java_mem=args.java_mem,
        verbose=args.verbose,
        ontology_name=name,
    )


def run_stage_filter(name: str, args: argparse.Namespace) -> FilterResult:
    """Stage 2: classify the cleaned file, copy to alc/ if ALC-suitable."""
    cleanup_file = CLEANUP_DIR / f"{name}.owl"
    alc_file = ALC_DIR / f"{name}.owl"

    if alc_file.exists() and not args.force:
        return FilterResult(success=True, is_alc=True)

    cls_result = classify_ontology(
        owl_path=cleanup_file,
        java_mem=args.java_mem,
    )
    if cls_result is None:
        return FilterResult(success=False)

    dl = cls_result.dl_languages

    if not is_alc_suitable(dl):
        return FilterResult(success=True, dl_languages=dl, is_alc=False)

    try:
        shutil.copy2(str(cleanup_file), str(alc_file))
        return FilterResult(success=True, dl_languages=dl, is_alc=True)
    except OSError as e:
        return FilterResult(success=False, dl_languages=dl, error=str(e))


def run_stage_make_inconsistent(name: str, args: argparse.Namespace) -> bool:
    """Stage 3: run MakeInconsistent on one ALC ontology."""
    alc_file = ALC_DIR / f"{name}.owl"
    inconsistent_file = INCONSISTENT_DIR / f"{name}.owl"

    if inconsistent_file.exists() and not args.force:
        return True

    return run_make_inconsistent(
        input_path=alc_file,
        output_path=inconsistent_file,
        java_mem=args.java_mem,
        verbose=args.verbose,
        ontology_name=name,
    )


# ── Stage worker wrappers (state-aware, for parallel execution) ───────


def _cleanup_worker(
    name: str, state: StageState, args: argparse.Namespace,
) -> bool:
    state.update(name, step="running", _start_time=time.monotonic())
    result = run_stage_cleanup(name, args)
    elapsed = time.monotonic() - state._items[name]._start_time
    if result:
        state.update(name, step="done", elapsed=elapsed)
    else:
        state.update(name, step="failed", elapsed=elapsed,
                     error="cleanup failed or file missing")
    state.complete(name)
    return result


def _filter_worker(
    name: str, state: StageState, args: argparse.Namespace,
) -> FilterResult:
    state.update(name, step="running", _start_time=time.monotonic())
    result = run_stage_filter(name, args)
    elapsed = time.monotonic() - state._items[name]._start_time
    if not result.success:
        state.update(name, step="failed", elapsed=elapsed,
                     error="classification failed")
    elif result.is_alc:
        state.update(name, step="done", elapsed=elapsed,
                     dl_languages=result.dl_languages)
    else:
        state.update(name, step="unsuitable", elapsed=elapsed,
                     dl_languages=result.dl_languages)
    state.complete(name)
    return result


def _incon_worker(
    name: str, state: StageState, args: argparse.Namespace,
) -> bool:
    state.update(name, step="running", _start_time=time.monotonic())
    result = run_stage_make_inconsistent(name, args)
    elapsed = time.monotonic() - state._items[name]._start_time
    if result:
        state.update(name, step="done", elapsed=elapsed)
    else:
        state.update(name, step="failed", elapsed=elapsed,
                     error="make-inconsistent failed")
    state.complete(name)
    return result


# ── Generic batch runner ──────────────────────────────────────────────


def run_batch(
    stage_label: str,
    items: list[str],
    worker_fn: Callable[[str, StageState, argparse.Namespace], object],
    args: argparse.Namespace,
    sequential: bool = False,
) -> StageState:
    """Run *worker_fn* for each *item* in *items*.

    In parallel mode (default) a ``rich.live.Live`` display shows progress.
    In sequential mode (``sequential=True``) simple log lines are printed.

    Returns a ``StageState`` whose ``_items`` dict holds per-item status.
    """
    if not items:
        return StageState([], 1, stage_label)

    n_workers = min(args.workers, len(items))
    state = StageState(items, n_workers, stage_label)

    if sequential:
        total = len(items)
        log(f"Starting {stage_label} ({total} ontologies) ...")
        for idx, name in enumerate(items, start=1):
            log(f"  [{idx}/{total}] {name} ...")
            worker_fn(name, state, args)
    else:
        with Live(
            get_renderable=lambda: render_stage_display(state),
            refresh_per_second=10,
            transient=True,
        ):
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=n_workers
            ) as pool:
                futures = {
                    pool.submit(worker_fn, name, state, args): name
                    for name in items
                }
                for _ in concurrent.futures.as_completed(futures):
                    pass

    return state


# ── Helpers ───────────────────────────────────────────────────────────


def list_original_ontologies() -> list[str]:
    """Return ontology names (without .owl) from the original/ directory."""
    names = sorted(p.stem for p in ORIGINAL_DIR.glob("*.owl"))
    if not names:
        print("[WARN] No .owl files found in original/", file=sys.stderr)
    return names


def log(msg: str) -> None:
    """Print a timestamped message to stderr."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def _classify_and_sort_alc(names: list[str], java_mem: str) -> list[str]:
    """Classify each ``alc/{name}.owl``, sort by axiom count ascending.

    Ontologies that fail to classify are placed at the end so they do not
    block the pipeline.
    """
    log("Classifying ALC ontologies to determine processing order ...")
    counts: list[tuple[int | float, str]] = []
    for name in names:
        owl_path = ALC_DIR / f"{name}.owl"
        if not owl_path.exists():
            counts.append((float("inf"), name))
            continue
        result = classify_ontology(owl_path=owl_path, java_mem=java_mem)
        if result is not None:
            counts.append((result.axioms, name))
        else:
            counts.append((float("inf"), name))
    counts.sort(key=lambda x: x[0])
    sorted_names = [name for _, name in counts]
    log(f"Processing order (by axioms): {', '.join(sorted_names)}")
    return sorted_names


# ── Pipeline orchestration ────────────────────────────────────────────


def run_preprocessing(args: argparse.Namespace) -> None:
    """Run the 3-stage batch preprocessing pipeline."""
    # Resolve ontology names
    if args.ontologies:
        names = args.ontologies
    else:
        names = list_original_ontologies()
        if not names:
            log("[ERROR] No ontology names given and no .owl files found "
                "in original/.")
            sys.exit(1)

    total = len(names)
    n_workers = min(args.workers, total)
    sequential = args.no_progress or total <= 1

    log(f"Ontologies: {total}, workers: {n_workers}")
    log(f"  Original dir:       {ORIGINAL_DIR}")
    log(f"  Cleanup dir:        {CLEANUP_DIR}")
    log(f"  ALC dir:            {ALC_DIR}")
    log(f"  Inconsistent dir:   {INCONSISTENT_DIR}")

    CLEANUP_DIR.mkdir(parents=True, exist_ok=True)
    ALC_DIR.mkdir(parents=True, exist_ok=True)
    INCONSISTENT_DIR.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        log("\n[Dry run mode -- no commands will be executed]")
        for name in names:
            original_file = ORIGINAL_DIR / f"{name}.owl"
            cleanup_file = CLEANUP_DIR / f"{name}.owl"
            alc_file = ALC_DIR / f"{name}.owl"
            inconsistent_file = INCONSISTENT_DIR / f"{name}.owl"
            print(f"  {name}:")
            print(f"    Stage 1: CleanupOntology  -o {cleanup_file}  "
                  f"{original_file}")
            print(f"    Stage 2: ClassifyOntology + is_alc_suitable "
                  f"-> copy to {alc_file}")
            print(
                f"    Stage 3: MakeInconsistent  --normalize --basic-cache "
                f"--strict-sroiq --strict-simple-roles "
                f"--simple-ria-weakening --strict-owl2 --verbose "
                f"-o {inconsistent_file}  {alc_file}"
            )
        return

    # ── Stage 1: Cleanup (all ontologies) ────────────────────────────
    log("=== Stage 1: Cleanup ===")
    state1 = run_batch("Cleanup", names, _cleanup_worker, args, sequential)
    cleaned = [n for n in names if state1._items[n].step == "done"]
    cleanup_failed = [n for n in names if state1._items[n].step == "failed"]

    # ── Stage 2: Classify & Filter (only successfully cleaned) ──────
    log("=== Stage 2: Classify & Filter ===")
    state2 = run_batch("Filter", cleaned, _filter_worker, args, sequential)
    alc_suitable = [n for n in cleaned if state2._items[n].step == "done"]
    unsuitable = [n for n in cleaned if state2._items[n].step == "unsuitable"]
    filter_failed = [n for n in cleaned if state2._items[n].step == "failed"]

    # ── Sort ALC-suitable by axiom count (on alc/ files, before Stage 3) ─
    log("=== Sorting ALC ontologies by axiom count ===")
    sorted_alc = _classify_and_sort_alc(alc_suitable, args.java_mem)

    # ── Stage 3: Make Inconsistent (ALC only, sorted) ───────────────
    log("=== Stage 3: Make Inconsistent ===")
    state3 = run_batch(
        "MakeInconsistent", sorted_alc, _incon_worker, args, sequential,
    )
    incon_done = [n for n in sorted_alc
                  if state3._items[n].step == "done"]
    incon_failed = [n for n in sorted_alc
                    if state3._items[n].step == "failed"]

    # ── Summary ──────────────────────────────────────────────────────
    log("=" * 50)
    log("SUMMARY")
    log("=" * 50)
    log(f"  Total ontologies:             {total}")
    log(f"  Stage 1 - Cleanup OK:         {len(cleaned)}")
    log(f"  Stage 1 - Cleanup failed:     {len(cleanup_failed)}")
    log(f"  Stage 2 - ALC suitable:       {len(alc_suitable)}")
    log(f"  Stage 2 - Unsuitable:         {len(unsuitable)}")
    log(f"  Stage 2 - Filter failed:      {len(filter_failed)}")
    log(f"  Stage 3 - Inconsistent OK:    {len(incon_done)}")
    log(f"  Stage 3 - Inconsistent failed: {len(incon_failed)}")

    all_failed = (
        len(cleanup_failed) + len(filter_failed) + len(incon_failed)
    )
    if all_failed:
        log(f"  Total failures:               {all_failed}")
        sys.exit(1)
    else:
        log("  All ontologies processed successfully!")
