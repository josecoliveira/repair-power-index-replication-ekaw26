"""Pre-process ontologies from the ekaw26 test resources in parallel.

Pipeline for each ontology name:
  1. CleanupOntology:  original/{name}.owl  ->  cleanup/{name}.owl
  2. MakeInconsistent:  cleanup/{name}.owl  ->  inconsistent/{name}.owl
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

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
INCONSISTENT_DIR = PACKAGE_ROOT / "ontologies" / "inconsistent"

DEFAULT_JAVA_MEM = "-Xms1g -Xmx8g -Xss8m"
DEFAULT_WORKERS = os.cpu_count() or 4

Step = Literal["pending", "cleaning", "making_inconsistent", "done", "failed"]


# ── Thread-safe shared state ──────────────────────────────────────────


@dataclass
class OntologyStatus:
    """Status of one ontology throughout the pipeline."""

    name: str
    step: Step = "pending"
    cleanup_time: float = 0.0
    inconsistent_time: float = 0.0
    error: str | None = None
    _start_time: float = 0.0

    @property
    def elapsed(self) -> float:
        if self.step in ("done", "failed"):
            return self.cleanup_time + self.inconsistent_time
        if self._start_time > 0:
            return time.monotonic() - self._start_time
        return 0.0


class SharedState:
    """Thread-safe container for ontology processing progress."""

    def __init__(self, names: list[str], n_workers: int):
        self._lock = threading.Lock()
        self._statuses: dict[str, OntologyStatus] = {
            n: OntologyStatus(name=n) for n in names
        }
        self._completion_order: list[str] = []
        self.total = len(names)
        self.n_workers = n_workers
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
                setattr(self._statuses[name], k, v)

    def complete(self, name: str) -> None:
        with self._lock:
            self._completion_order.append(name)

    def get_completed(self) -> list[OntologyStatus]:
        with self._lock:
            return [self._statuses[n] for n in self._completion_order]

    def get_active(self) -> list[OntologyStatus]:
        with self._lock:
            active = [
                s for s in self._statuses.values()
                if s.step not in ("done", "failed")
            ]
            active.sort(key=lambda s: (0 if s.step != "pending" else 1, s.name))
            return active[: self.n_workers]

    def get_done_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._statuses.values() if s.step == "done")

    def get_failed(self) -> list[OntologyStatus]:
        with self._lock:
            return [s for s in self._statuses.values() if s.step == "failed"]

    def get_progress(self) -> Progress:
        """Return the persistent Progress bar with updated completion count."""
        self._progress.update(self._task_id, completed=self.get_done_count())
        return self._progress


# ── Helpers ────────────────────────────────────────────────────────────


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


# ── Worker ─────────────────────────────────────────────────────────────


def process_ontology(
    name: str,
    state: SharedState,
    args: argparse.Namespace,
) -> bool:
    """Process a single ontology through cleanup then make-inconsistent.

    Updates *state* after each step so the live display stays current.
    Returns True if both steps succeeded (or were skipped).
    """
    original_file = ORIGINAL_DIR / f"{name}.owl"
    cleanup_file = CLEANUP_DIR / f"{name}.owl"
    inconsistent_file = INCONSISTENT_DIR / f"{name}.owl"

    if not original_file.exists():
        state.update(name, step="failed", error="original file not found")
        state.complete(name)
        return False

    step_start = time.monotonic()
    state.update(name, _start_time=step_start)

    # ── Step 1: Cleanup ─────────────────────────────────────────────
    if cleanup_file.exists() and not args.force:
        pass  # skip cleanup, keep cleanup_time = 0
    else:
        state.update(name, step="cleaning")
        t0 = time.monotonic()
        success = run_cleanup_ontology(
            input_path=original_file,
            output_path=cleanup_file,
            java_mem=args.java_mem,
            verbose=args.verbose,
            ontology_name=name,
        )
        state.update(name, cleanup_time=time.monotonic() - t0)

        if not success:
            state.update(name, step="failed", error="cleanup failed")
            state.complete(name)
            return False

    # ── Step 2: Make inconsistent ───────────────────────────────────
    if inconsistent_file.exists() and not args.force:
        state.update(name, step="done")
        state.complete(name)
        return True

    state.update(name, step="making_inconsistent")
    t0 = time.monotonic()
    success = run_make_inconsistent(
        input_path=cleanup_file,
        output_path=inconsistent_file,
        java_mem=args.java_mem,
        verbose=args.verbose,
        ontology_name=name,
    )
    state.update(name, inconsistent_time=time.monotonic() - t0)

    if not success:
        state.update(name, step="failed", error="make-inconsistent failed")
        state.complete(name)
        return False

    state.update(name, step="done")
    state.complete(name)
    return True


# ── Display ────────────────────────────────────────────────────────────


def render_display(state: SharedState) -> Group:
    """Build a Group renderable for the live terminal display.

    Layout (top to bottom):
      1. Completed ontologies (at most *m - n* lines, scroll upward)
      2. Active ontologies (*n* lines, one per worker)
      3. Progress bar with total elapsed time
    """
    completed = state.get_completed()
    active = state.get_active()

    lines: list[str] = []

    for s in completed:
        if s.step == "done":
            lines.append(
                f"  {s.name:<12}"
                f" cleanup {s.cleanup_time:.1f}s"
                f" + inconsistent {s.inconsistent_time:.1f}s"
                f"  done"
            )
        elif s.step == "failed":
            lines.append(
                f"  {s.name:<12} FAILED  ({s.error or 'unknown error'})"
            )

    for s in active:
        if s.step == "cleaning":
            lines.append(
                f"  {s.name:<12} cleaning up ...                      {s.elapsed:.1f}s"
            )
        elif s.step == "making_inconsistent":
            lines.append(
                f"  {s.name:<12} making inconsistent ...              {s.elapsed:.1f}s"
            )
        elif s.step == "pending":
            lines.append(
                f"  {s.name:<12} pending                              ---"
            )

    return Group(*lines, state.get_progress())


# ── Pipeline ───────────────────────────────────────────────────────────


def _classify_and_sort(names: list[str], java_mem: str) -> list[str]:
    """Classify each ontology to obtain its axiom count, then sort
    from smallest to largest (fewest axioms first).

    Ontologies that fail to classify are placed at the end so they
    do not block the pipeline.
    """
    log("Classifying ontologies to determine axiom counts for ordering ...")
    counts: list[tuple[int | float, str]] = []
    for name in names:
        owl_path = ORIGINAL_DIR / f"{name}.owl"
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


def run_preprocessing(args: argparse.Namespace) -> None:
    """Run the pre-processing pipeline with the given parsed arguments."""
    # Resolve ontology names
    if args.ontologies:
        names = args.ontologies
    else:
        names = list_original_ontologies()
        if not names:
            log("[ERROR] No ontology names given and no .owl files found in original/.")
            sys.exit(1)

    # Classify and sort by axiom count (smallest first)
    names = _classify_and_sort(names, args.java_mem)

    total = len(names)
    n_workers = min(args.workers, total)

    log(f"Ontologies: {total}, workers: {n_workers}")
    log(f"  Original dir:      {ORIGINAL_DIR}")
    log(f"  Cleanup dir:       {CLEANUP_DIR}")
    log(f"  Inconsistent dir:  {INCONSISTENT_DIR}")

    CLEANUP_DIR.mkdir(parents=True, exist_ok=True)
    INCONSISTENT_DIR.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        log("\n[Dry run mode -- no commands will be executed]")
        for name in names:
            original_file = ORIGINAL_DIR / f"{name}.owl"
            cleanup_file = CLEANUP_DIR / f"{name}.owl"
            inconsistent_file = INCONSISTENT_DIR / f"{name}.owl"
            print(f"  {name}:")
            print(f"    CleanupOntology  -o {cleanup_file}  {original_file}")
            print(
                f"    MakeInconsistent  --normalize --basic-cache "
                f"--strict-sroiq --strict-simple-roles "
                f"--simple-ria-weakening --strict-owl2 --verbose "
                f"-o {inconsistent_file}  {cleanup_file}"
            )
        return

    # ── Batch: sequential (--no-progress) vs parallel (live display) ─
    ok_cleanup = 0
    ok_inconsistent = 0
    skipped_cleanup = 0
    skipped_inconsistent = 0

    if args.no_progress or total == 1:
        # ── Sequential mode: original behaviour ──
        for idx, name in enumerate(names, start=1):
            log(f"[{idx}/{total}] Processing '{name}' ...")

            original_file = ORIGINAL_DIR / f"{name}.owl"
            cleanup_file = CLEANUP_DIR / f"{name}.owl"
            inconsistent_file = INCONSISTENT_DIR / f"{name}.owl"

            if not original_file.exists():
                log(f"  [SKIP] Original file not found: {original_file}")
                continue

            # Step 1
            if cleanup_file.exists() and not args.force:
                log(
                    f"  [SKIP] Cleanup output already exists: {cleanup_file.name}"
                    " (use --force to redo)"
                )
                skipped_cleanup += 1
            else:
                success = run_cleanup_ontology(
                    input_path=original_file,
                    output_path=cleanup_file,
                    java_mem=args.java_mem,
                    verbose=args.verbose,
                    ontology_name=name,
                )
                if success:
                    ok_cleanup += 1
                else:
                    continue

            # Step 2
            if inconsistent_file.exists() and not args.force:
                log(
                    f"  [SKIP] Inconsistent output already exists:"
                    f" {inconsistent_file.name} (use --force to redo)"
                )
                skipped_inconsistent += 1
            else:
                success = run_make_inconsistent(
                    input_path=cleanup_file,
                    output_path=inconsistent_file,
                    java_mem=args.java_mem,
                    verbose=args.verbose,
                    ontology_name=name,
                )
                if success:
                    ok_inconsistent += 1

        # ── Summary (sequential) ──
        log("=" * 50)
        log("SUMMARY")
        log("=" * 50)
        log(f"  Total ontologies:     {total}")
        log(f"  Cleanup succeeded:    {ok_cleanup}")
        log(f"  Cleanup skipped:      {skipped_cleanup}")
        log(f"  Inconsistent succeeded: {ok_inconsistent}")
        log(f"  Inconsistent skipped:   {skipped_inconsistent}")
        if ok_inconsistent == 0 and ok_cleanup == 0:
            log("  All ontologies processed successfully!")
        return

    # ── Parallel mode with live display ──────────────────────────────
    state = SharedState(names, n_workers)
    failed_names: list[str] = []

    with Live(
        get_renderable=lambda: render_display(state),
        refresh_per_second=10,
        transient=True,
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(process_ontology, name, state, args): name
                for name in names
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    if not future.result():
                        failed_names.append(name)
                except Exception as exc:
                    failed_names.append(name)
                    log(f"[ERROR] {name}: unexpected exception: {exc}")

    # ── Summary ──
    log("=" * 50)
    log("SUMMARY")
    log("=" * 50)
    log(f"  Total ontologies:     {total}")
    log(f"  Processed:            {state.get_done_count()} done,"
        f" {len(state.get_failed())} failed")
    if failed_names:
        log(f"  Failed ontologies:    {len(failed_names)} ->"
            f" {', '.join(failed_names)}")
    else:
        log("  All ontologies processed successfully!")
    if failed_names:
        sys.exit(1)
