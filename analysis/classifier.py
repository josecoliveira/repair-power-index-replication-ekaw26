"""Classify all ontologies across original/cleanup/inconsistent folders.

Scans the three ontology folders dynamically, runs
``ClassifyOntology`` from the shaded JAR on each
ontology file, and produces a single markdown table with axioms count,
class (concept) count, and DL language per folder.

Supports parallel execution and a live progress display,
mirroring the pattern established in ``preprocessor.py``.
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

from rich.console import Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from analysis.ontologyutils_service import classify_ontology

# ── Paths ──
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGIES_DIR = PACKAGE_ROOT / "ontologies"

FOLDER_NAMES = ["original", "cleanup", "inconsistent"]
FOLDER_DISPLAY = ["Original", "Cleanup", "Inconsistent"]

DEFAULT_JAVA_MEM = "-Xms1g -Xmx8g -Xss8m"
DEFAULT_TIMEOUT = 120  # seconds per ontology
DEFAULT_WORKERS = os.cpu_count() or 4


# ── Helpers ──────────────────────────────────────────────────────────────


def discover_ontology_names() -> set[str]:
    """Return the union of all ontology stems across the three folders."""
    names: set[str] = set()
    for folder_name in FOLDER_NAMES:
        folder = ONTOLOGIES_DIR / folder_name
        if folder.is_dir():
            for owl in folder.glob("*.owl"):
                names.add(owl.stem)
    return names


def log(msg: str) -> None:
    """Print a timestamped message to stderr."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


# ── Pre-classify and sort ───────────────────────────────────────────────


def _classify_and_sort_names(
    names: list[str],
    java_mem: str,
    timeout: int,
) -> tuple[list[str], dict[str, dict | None]]:
    """Classify ``original/`` to obtain axiom counts, then sort smallest first.

    Results from this pre-pass are stored in *original_results* so they
    do not need to be classified again later.

    Returns
    -------
    sorted_names
        Ontology names sorted by axiom count ascending (fewest first).
        Ontologies that fail to classify are placed at the end.
    original_results
        Mapping ``name → data dict`` (or ``None`` if the file was missing
        or classification failed).
    """
    log("Classifying original/ folder to determine axiom counts ...")
    original_results: dict[str, dict | None] = {}
    counts: list[tuple[int | float, str]] = []

    for name in names:
        owl_path = ONTOLOGIES_DIR / "original" / f"{name}.owl"
        if not owl_path.exists():
            counts.append((float("inf"), name))
            original_results[name] = None
            continue

        cls_result = classify_ontology(
            owl_path=owl_path,
            java_mem=java_mem,
            timeout=timeout,
        )
        if cls_result is not None:
            data = {
                "axioms": cls_result.axioms,
                "classes": cls_result.classes,
                "dl_languages": cls_result.dl_languages,
            }
            counts.append((cls_result.axioms, name))
            original_results[name] = data
        else:
            counts.append((float("inf"), name))
            original_results[name] = None

    counts.sort(key=lambda x: x[0])
    sorted_names = [name for _, name in counts]
    log(f"Processing order (by axioms): {', '.join(sorted_names)}")
    return sorted_names, original_results


# ── Thread-safe shared state ────────────────────────────────────────────


@dataclass
class ClassificationStatus:
    """Status of one (folder, ontology) classification pair."""

    name: str
    folder: str
    step: str = "pending"       # pending | running | done | failed
    elapsed: float = 0.0
    error: str | None = None
    _start_time: float = 0.0


class SharedState:
    """Thread-safe container for classification progress."""

    def __init__(self, pairs: list[tuple[str, str]], n_workers: int):
        self._lock = threading.Lock()
        self._statuses: dict[tuple[str, str], ClassificationStatus] = {
            (n, f): ClassificationStatus(name=n, folder=f) for n, f in pairs
        }
        self._completion_order: list[tuple[str, str]] = []
        self.total = len(pairs)
        self.n_workers = n_workers
        self._progress = Progress(
            TextColumn("  "),
            BarColumn(bar_width=None),
            TextColumn("  {task.completed}/{task.total}"),
            TimeElapsedColumn(),
        )
        self._task_id = self._progress.add_task("", total=len(pairs))

    def update(self, name: str, folder: str, **kwargs: object) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._statuses[(name, folder)], k, v)

    def complete(self, name: str, folder: str) -> None:
        with self._lock:
            self._completion_order.append((name, folder))

    def get_completed(self) -> list[ClassificationStatus]:
        with self._lock:
            return [self._statuses[k] for k in self._completion_order]

    def get_active(self) -> list[ClassificationStatus]:
        with self._lock:
            active = [
                s for s in self._statuses.values()
                if s.step not in ("done", "failed")
            ]
            active.sort(
                key=lambda s: (
                    0 if s.step != "pending" else 1,
                    s.name,
                    s.folder,
                )
            )
            return active[: self.n_workers]

    def get_done_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._statuses.values() if s.step == "done")

    def get_failed_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._statuses.values() if s.step == "failed")

    def get_progress(self) -> Progress:
        """Return the persistent Progress bar with updated completion count."""
        self._progress.update(self._task_id, completed=self.get_done_count())
        return self._progress


# ── Worker ──────────────────────────────────────────────────────────────


def classify_one(
    name: str,
    folder: str,
    state: SharedState,
    args: argparse.Namespace,
) -> dict | None:
    """Classify a single ontology in a single folder.

    Updates *state* so the live display stays current.
    Returns the data dict on success, or ``None`` on failure.
    """
    owl_path = ONTOLOGIES_DIR / folder / f"{name}.owl"
    if not owl_path.exists():
        state.update(name, folder, step="failed", error="file not found")
        state.complete(name, folder)
        return None

    step_start = time.monotonic()
    state.update(name, folder, _start_time=step_start, step="running")

    cls_result = classify_ontology(
        owl_path=owl_path,
        java_mem=args.java_mem,
        timeout=args.timeout,
    )
    elapsed = time.monotonic() - step_start

    if cls_result is not None:
        data = {
            "axioms": cls_result.axioms,
            "classes": cls_result.classes,
            "dl_languages": cls_result.dl_languages,
        }
        state.update(name, folder, step="done", elapsed=elapsed)
        state.complete(name, folder)
        return data

    state.update(
        name, folder,
        step="failed", error="classification failed", elapsed=elapsed,
    )
    state.complete(name, folder)
    return None


# ── Display ─────────────────────────────────────────────────────────────


def render_display(state: SharedState) -> Group:
    """Build a Group renderable for the live terminal display.

    Layout (top to bottom):
      1. Completed (or failed) pairs
      2. Active (running / pending) pairs
      3. Progress bar
    """
    completed = state.get_completed()
    active = state.get_active()

    lines: list[str] = []

    for s in completed:
        if s.step == "done":
            lines.append(
                f"  {s.name:<12} [{s.folder:<12}] done            {s.elapsed:.1f}s"
            )
        elif s.step == "failed":
            lines.append(
                f"  {s.name:<12} [{s.folder:<12}] FAILED  ({s.error or 'unknown error'})"
            )

    for s in active:
        if s.step == "running":
            lines.append(
                f"  {s.name:<12} [{s.folder:<12}] classifying ...  {s.elapsed:.1f}s"
            )
        elif s.step == "pending":
            lines.append(
                f"  {s.name:<12} [{s.folder:<12}] pending          ---"
            )

    return Group(*lines, state.get_progress())


# ── Table helpers ───────────────────────────────────────────────────────


def cell_value(data: dict | None, key: str) -> str:
    """Return the table cell string for a given data dict and key.

    - ``None`` (file not present) → blank
    - data present but parse failed → ``-1``
    - data present with value → string representation
    """
    if data is None:
        return ""
    val = data.get(key)
    if val is None:
        return "-1"
    return str(val)


def build_table(all_data: dict[str, dict[str, dict | None]]) -> str:
    """Assemble the markdown table from classified data."""
    names = sorted(all_data.keys())

    # Header
    cols = ["Ontology"]
    for display in FOLDER_DISPLAY:
        cols.append(f"{display} Axioms")
        cols.append(f"{display} Classes")
        cols.append(f"{display} DL")

    header = "| " + " | ".join(cols) + " |"
    separator = "| " + " | ".join("-" * len(c) for c in cols) + " |"

    lines = [header, separator]

    # Rows
    for name in names:
        row_data = all_data[name]
        row = [name]
        for folder_name in FOLDER_NAMES:
            d = row_data.get(folder_name)
            row.append(cell_value(d, "axioms"))
            row.append(cell_value(d, "classes"))
            row.append(cell_value(d, "dl_languages"))
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines) + "\n"


# ── Core classification ─────────────────────────────────────────────────


def classify_all(
    names: list[str],
    args: argparse.Namespace,
    preclassified_original: dict[str, dict | None] | None = None,
) -> dict[str, dict[str, dict | None]]:
    """Classify every ontology in every folder.

    Parameters
    ----------
    names
        Ontology names to process (already sorted as desired).
    args
        Parsed CLI arguments (provides ``java_mem``, ``timeout``,
        ``workers``, ``no_progress``).
    preclassified_original
        If provided (from ``--sort-by-axioms``), these results are
        reused and ``original/`` is skipped during iteration.

    Returns
    -------
    dict
        ``result[ontology_name][folder_name] = data | None``
        where *data* is a dict with keys ``axioms``, ``classes``,
        ``dl_languages``, or ``None`` if the file did not exist
        or Java failed.
    """
    result: dict[str, dict[str, dict | None]] = {
        name: {} for name in names
    }

    # Apply pre-classified original/ results (--sort-by-axioms mode)
    if preclassified_original is not None:
        for name in names:
            if name in preclassified_original:
                result[name]["original"] = preclassified_original[name]

    # Build list of (name, folder) pairs to process
    pairs: list[tuple[str, str]] = []
    for name in names:
        for folder in FOLDER_NAMES:
            # Skip original/ if already done via --sort-by-axioms
            if preclassified_original is not None and folder == "original":
                continue
            pairs.append((name, folder))

    total_pairs = len(pairs)
    n_workers = min(args.workers, total_pairs)

    if args.no_progress or total_pairs <= 1:
        # ── Sequential mode: original behaviour ──
        for idx, (name, folder) in enumerate(pairs, start=1):
            log(f"[{idx}/{total_pairs}] {folder}/{name}.owl ...")
            owl_path = ONTOLOGIES_DIR / folder / f"{name}.owl"
            if not owl_path.exists():
                result[name][folder] = None
                continue

            cls_result = classify_ontology(
                owl_path=owl_path,
                java_mem=args.java_mem,
                timeout=args.timeout,
            )
            if cls_result is not None:
                result[name][folder] = {
                    "axioms": cls_result.axioms,
                    "classes": cls_result.classes,
                    "dl_languages": cls_result.dl_languages,
                }
            else:
                result[name][folder] = None
                if args.verbose:
                    log(f"  -> FAILED (marked as -1)")
        return result

    # ── Parallel mode with live display ──
    state = SharedState(pairs, n_workers)
    failed_pairs: list[tuple[str, str]] = []

    with Live(
        get_renderable=lambda: render_display(state),
        refresh_per_second=10,
        transient=True,
    ):
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=n_workers
        ) as pool:
            futures_map: dict[concurrent.futures.Future, tuple[str, str]] = {}
            for name, folder in pairs:
                fut = pool.submit(classify_one, name, folder, state, args)
                futures_map[fut] = (name, folder)

            for future in concurrent.futures.as_completed(futures_map):
                name, folder = futures_map[future]
                try:
                    data = future.result()
                    if data is not None:
                        result[name][folder] = data
                    else:
                        result[name][folder] = None
                        failed_pairs.append((name, folder))
                except Exception as exc:
                    result[name][folder] = None
                    failed_pairs.append((name, folder))
                    log(f"[ERROR] {folder}/{name}: unexpected exception: {exc}")

    # Fallback: ensure any pair that wasn't stored in result is None
    for name, folder in pairs:
        if folder not in result[name]:
            result[name][folder] = None

    return result


# ── Pipeline ─────────────────────────────────────────────────────────────


def run_classification(args: argparse.Namespace) -> None:
    """Run the classification pipeline with the given parsed arguments."""
    # Discover ontology names
    names = sorted(discover_ontology_names())
    if not names:
        log("No ontologies found.")
        return

    total = len(names)

    if args.verbose or args.no_progress:
        log(f"Output: {args.output}")
        log(f"Memory: {args.java_mem}")
        log(f"Timeout: {args.timeout}s")
        log(f"Ontologies: {total}")
        log(f"Workers: {args.workers}")
        if args.sort_by_axioms:
            log("Sort mode: by axiom count (smallest first)")
        else:
            log("Sort mode: alphabetical")
        log()

    # Optionally pre-classify original/ and sort by axiom count
    preclassified_original: dict[str, dict | None] | None = None
    if args.sort_by_axioms:
        names, preclassified_original = _classify_and_sort_names(
            names, args.java_mem, args.timeout,
        )

    # Classify all
    t0 = time.perf_counter()
    all_data = classify_all(names, args, preclassified_original)
    elapsed = time.perf_counter() - t0

    # Build and write table
    table = build_table(all_data)
    args.output.write_text(table, encoding="utf-8")

    # Summary
    total_cells = total * len(FOLDER_NAMES)
    present = sum(
        1 for row in all_data.values()
        for d in row.values()
        if d is not None
    )
    java_ok = sum(
        1 for row in all_data.values()
        for d in row.values()
        if d is not None and "axioms" in d
    )

    if args.verbose or args.no_progress:
        log("=" * 50)
        log("SUMMARY")
        log("=" * 50)
        absent = total_cells - present
        java_failed = present - java_ok
        log(f"  Ontologies:           {total}")
        log(f"  Folders:              {len(FOLDER_NAMES)}")
        log(f"  Total cells:          {total_cells}")
        log(f"  Files present:        {present}")
        log(f"  Files absent:         {absent}")
        log(f"  Java OK:              {java_ok}")
        log(f"  Java failed (-1):     {java_failed}")
        log(f"  Elapsed:              {elapsed:.1f}s")

    print(f"Written: {args.output}", file=sys.stderr)
