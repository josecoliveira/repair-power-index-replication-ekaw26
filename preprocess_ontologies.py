"""Pre-process ontologies from the ekaw26 test resources in parallel.

Pipeline for each ontology name:
  1. CleanupOntology:  original/{name}.owl  ->  cleanup/{name}.owl
  2. MakeInconsistent:  cleanup/{name}.owl  ->  inconsistent/{name}.owl

Usage:
    python preprocess_ontologies.py [options] [ontology_name ...]

If no ontology names are given, all .owl files found in the original/
directory are processed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.console import Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

# ── Paths ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPLICATION_DIR = SCRIPT_DIR

LIB_DIR = REPLICATION_DIR / "lib"
ORIGINAL_DIR = REPLICATION_DIR / "ontologies" / "original"
CLEANUP_DIR = REPLICATION_DIR / "ontologies" / "cleanup"
INCONSISTENT_DIR = REPLICATION_DIR / "ontologies" / "inconsistent"

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


def find_shaded_jar() -> Path:
    """Return the latest shaded-ontologyutils-*.jar from the lib directory."""
    candidates = sorted(LIB_DIR.glob("shaded-ontologyutils-*.jar"))
    if not candidates:
        raise FileNotFoundError(
            f"Could not find shaded-ontologyutils-*.jar under {LIB_DIR}. "
            "Run 'mvn package' first to build the shaded JAR."
        )
    return candidates[-1]


def list_original_ontologies() -> list[str]:
    """Return ontology names (without .owl) from the original/ directory."""
    names = sorted(p.stem for p in ORIGINAL_DIR.glob("*.owl"))
    if not names:
        log("[WARN] No .owl files found in original/")
    return names


def log(msg: str) -> None:
    """Print a timestamped message to stderr."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def run_java(
    main_class: str,
    args: list[str],
    java_mem: str,
    verbose: bool,
    ontology_name: str,
) -> tuple[bool, str]:
    """Run a Java main class via the shaded JAR.

    Returns (success, stderr_output). In verbose mode Java stderr is
    printed with a per-ontology prefix.
    """
    jar = find_shaded_jar()
    cmd = ["java"] + java_mem.split() + ["-cp", str(jar), main_class] + args

    if verbose:
        print(f"  [{ontology_name}] $ {' '.join(cmd)}", file=sys.stderr, flush=True)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []

    with process.stderr:
        for line in iter(process.stderr.readline, ""):
            line = line.rstrip("\n")
            if line:
                if verbose:
                    print(
                        f"  [{ontology_name}] {line}",
                        file=sys.stderr,
                        flush=True,
                    )
                stderr_lines.append(line)

    process.wait()
    full_stderr = "\n".join(stderr_lines)

    if verbose and process.returncode != 0:
        print(
            f"  [{ontology_name}] exit code {process.returncode}",
            file=sys.stderr,
            flush=True,
        )

    return process.returncode == 0, full_stderr


def makeinc_flags() -> list[str]:
    """Return the MakeInconsistent flags used in the paper."""
    return [
        "--normalize",
        "--basic-cache",
        "--strict-sroiq",
        "--strict-simple-roles",
        "--simple-ria-weakening",
        "--strict-owl2",
        "--verbose",
    ]


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
        success, _ = run_java(
            main_class="www.ontologyutils.apps.CleanupOntology",
            args=["-n", "-o", str(cleanup_file), str(original_file)],
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
    success, _ = run_java(
        main_class="www.ontologyutils.apps.MakeInconsistent",
        args=makeinc_flags() + ["-o", str(inconsistent_file), str(cleanup_file)],
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


# ── CLI ────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-process ontologies: clean up then make inconsistent.",
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


# ── Main ───────────────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()

    # ── Resolve ontology names ──────────────────────────────────────
    if args.ontologies:
        names = args.ontologies
    else:
        names = list_original_ontologies()
        if not names:
            log("[ERROR] No ontology names given and no .owl files found in original/.")
            sys.exit(1)

    total = len(names)
    n_workers = min(args.workers, total)

    log(f"Ontologies: {total}, workers: {n_workers}")
    log(f"  Original dir:      {ORIGINAL_DIR}")
    log(f"  Cleanup dir:       {CLEANUP_DIR}")
    log(f"  Inconsistent dir:  {INCONSISTENT_DIR}")

    CLEANUP_DIR.mkdir(parents=True, exist_ok=True)
    INCONSISTENT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        jar = find_shaded_jar()
        log(f"  Shaded JAR:        {jar}")
    except FileNotFoundError as e:
        log(f"[ERROR] {e}")
        sys.exit(1)

    if args.dry_run:
        log("\n[Dry run mode -- no commands will be executed]")
        for name in names:
            original_file = ORIGINAL_DIR / f"{name}.owl"
            cleanup_file = CLEANUP_DIR / f"{name}.owl"
            inconsistent_file = INCONSISTENT_DIR / f"{name}.owl"
            print(f"  {name}:")
            print(f"    CleanupOntology  -o {cleanup_file}  {original_file}")
            print(
                f"    MakeInconsistent  {' '.join(makeinc_flags())}  -o {inconsistent_file}  {cleanup_file}"
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
                success, _ = run_java(
                    main_class="www.ontologyutils.apps.CleanupOntology",
                    args=["-n", "-o", str(cleanup_file), str(original_file)],
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
                success, _ = run_java(
                    main_class="www.ontologyutils.apps.MakeInconsistent",
                    args=makeinc_flags()
                    + ["-o", str(inconsistent_file), str(cleanup_file)],
                    java_mem=args.java_mem,
                    verbose=args.verbose,
                    ontology_name=name,
                )
                if success:
                    ok_inconsistent += 1

        # ── Summary (sequential) ──
        failed: list[str] = []
        log("=" * 50)
        log("SUMMARY")
        log("=" * 50)
        log(f"  Total ontologies:     {total}")
        log(f"  Cleanup succeeded:    {ok_cleanup}")
        log(f"  Cleanup skipped:      {skipped_cleanup}")
        log(f"  Inconsistent succeeded: {ok_inconsistent}")
        log(f"  Inconsistent skipped:   {skipped_inconsistent}")
        if failed:
            log(f"  Failed ontologies:    {len(failed)} -> {', '.join(failed)}")
        else:
            log("  All ontologies processed successfully!")
        if failed:
            sys.exit(1)
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


if __name__ == "__main__":
    main()
