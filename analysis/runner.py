"""Trial runner of the paper's experiment.

Runs up to ``--parallel-trials K`` ``SingleTrialExperiment`` trials **concurrently** for
one ontology, records the results of the four arms the paper reports - ``A1``, ``A2``,
``A3`` and the Shapley repair ``SHAPLEY`` - and calls the paper's analysis
(:mod:`analysis.analysis`) after every ``--analysis-interval`` ontologies and at the end.

This module owns everything this experiment writes: the arm ids, the CSV headers, the
row building, the log layout, and the results directory (``data/results-<run-id>/``, no
suffix). The nine-arm grid has its own, independent :mod:`analysis.ablation_runner`; the
two modules never import each other, so the grid cannot change what the paper produces.

Design
------
* The unit of parallelism is one trial, i.e. one ``java`` subprocess.  The pool
  threads only *wait* on subprocesses; they do no computation, so the GIL is
  irrelevant.
* At most ``K`` trials are in flight for one ontology, and never more attempts
  are launched than are still needed (``successes + in_flight < target``).
* Attempt numbers are assigned **at launch time**, so the seed of the k-th
  attempt depends only on the configuration, not on the scheduling: two runs of the
  same configuration produce the same seeds, and an interrupted run can be resumed.
* All CSV/log/notifier side effects happen on the main thread, which is the one
  consuming finished futures, so no two threads write the same file.
* On interruption the in-flight JVMs are killed by process group, so a run that
  is stopped never leaves orphaned JVMs behind.
* Ontologies are still processed one at a time (the parallelism is *within* an
  ontology); the smallest ontology still finishes first, giving early feedback.
* With ``--parallel-trials 1`` the behaviour, seeds and rows are identical to the
  reference sequential runner this module replaced.
"""

from __future__ import annotations

import argparse
import csv
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from analysis import notifier
from analysis.analysis import run_analysis
from analysis.ontologyutils_service import (
    build_single_trial_command,
    classify_ontology,
    parse_trial_stdout,
)

# ── This experiment's schema ────────────────────────────────────────────
#: The three baselines, reported for every arm.
A_REPAIRS = ["A1", "A2", "A3"]
#: The paper's own arm: bad axiom and weaker replacement both ranked by Shapley value.
SHAPLEY_ARM = "SHAPLEY"
#: Columns of the IIC table: one comparison per baseline.
IIC_KEYS = [f"{SHAPLEY_ARM}_vs_{baseline}" for baseline in A_REPAIRS]
#: Repairs whose runtime is recorded.
RUNTIME_KEYS = A_REPAIRS + [SHAPLEY_ARM]

#: The Java class that drives one trial.
EXPERIMENT_CLASS = "www.ontologyutils.apps.SingleTrialExperiment"

# ── Path constants (package layout, not user-configurable) ──────────────
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
INCONSISTENT_DIR = PACKAGE_ROOT / "ontologies" / "inconsistent"
ANALYSIS_DIR = PACKAGE_ROOT / "data"


# ── Helpers ─────────────────────────────────────────────────────────────


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_ontologies(inconsistent_dir: Path) -> list[Path]:
    ontologies = sorted(inconsistent_dir.glob("*.owl"))
    if not ontologies:
        raise FileNotFoundError(f"No .owl files found in {inconsistent_dir}")
    return ontologies


def make_paths_for_ontology(ontology_name: str, data_dir: Path) -> tuple[Path, Path, Path]:
    iic_path = data_dir / f"iic-{ontology_name}.csv"
    runtime_path = data_dir / f"runtime-{ontology_name}.csv"
    log_path = data_dir / f"run_trials-{ontology_name}.log"
    return iic_path, runtime_path, log_path


def ensure_csv_with_header(path: Path, fieldnames: list[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()


def normalize_status(status: str | None) -> str:
    status = (status or "").strip().lower()
    if status in {"success", "time_limit_exceeded", "memory_limit_exceeded", "java_exception"}:
        return status
    return "memory_limit_exceeded"


def write_log_header(handle, ontology_name: str, attempt_number: int, seed: int) -> None:
    handle.write(
        f"----- {ontology_name} attempt={attempt_number} seed={seed} at {timestamp()} -----\n"
    )


def append_iic_row(iic_path: Path, payload: dict, run_id: str) -> None:
    iic_values = payload.get("iic_values") if isinstance(payload, dict) else {}
    iic_values = iic_values if isinstance(iic_values, dict) else {}
    row = {key: iic_values.get(key) for key in IIC_KEYS}
    row["run_id"] = payload.get("run_id", run_id)
    with open(iic_path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IIC_KEYS + ["run_id"])
        writer.writerow(row)


def runtime_row_from_payload(
    trial_number: int,
    seed: int,
    payload: dict | None,
    trial_elapsed_seconds: float,
    outcome: str,
    run_id: str,
) -> dict:
    repair_runtimes = payload.get("repair_runtimes_ms") if isinstance(payload, dict) else {}
    repair_runtimes = repair_runtimes if isinstance(repair_runtimes, dict) else {}
    row = {
        "trial_number": trial_number,
        "seed": seed,
        "run_id": run_id,
        "trial_status": outcome,
        "error_type": (payload or {}).get("error_type", "") if isinstance(payload, dict) else "",
        "failure_stage": (payload or {}).get("failure_stage", "") if isinstance(payload, dict) else "",
        "error_message": (payload or {}).get("error_message", "") if isinstance(payload, dict) else "",
        "trial_elapsed_seconds": f"{trial_elapsed_seconds:.6f}",
    }
    for repair_id in RUNTIME_KEYS:
        row[f"{repair_id}_ms"] = repair_runtimes.get(repair_id)
    return row


def append_dict_row(path: Path, fieldnames: list[str], row: dict) -> None:
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writerow(row)


def seed_for_attempt(ontology_index: int, attempted_for_ontology: int,
                     ontology_count: int, base_seed: int, step: int) -> int:
    return base_seed + ontology_index + attempted_for_ontology * step * ontology_count


def resume_state(
    data_dir: Path,
    ontology_names: list[str],
) -> tuple[dict[str, int], dict[str, int], dict[str, list[tuple[int, int, str]]]]:
    """Read existing runtime CSVs to recover per-ontology state when resuming.

    Returns (successes, attempts, failures) dicts keyed by ontology name.
    For ontologies with no existing data, counters start at zero.
    """
    successes: dict[str, int] = {}
    attempts: dict[str, int] = {}
    failures: dict[str, list[tuple[int, int, str]]] = {}

    for name in ontology_names:
        runtime_path = data_dir / f"runtime-{name}.csv"
        ont_successes = 0
        ont_attempts = 0
        ont_failures: list[tuple[int, int, str]] = []

        if runtime_path.exists():
            with open(runtime_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ont_attempts += 1
                    status = (row.get("trial_status") or "").strip().lower()
                    if status == "success":
                        ont_successes += 1
                    else:
                        trial_num = int(row.get("trial_number", 0))
                        seed_val = int(row.get("seed", 0))
                        err_msg = (
                            row.get("error_message")
                            or row.get("error_type")
                            or status
                            or "unknown"
                        )
                        ont_failures.append((trial_num, seed_val, err_msg))

        successes[name] = ont_successes
        attempts[name] = ont_attempts
        failures[name] = ont_failures

    return successes, attempts, failures

# ── In-flight process registry (for interrupt cleanup) ──────────────────

_active_processes: set[subprocess.Popen] = set()
_process_lock = threading.Lock()


def _terminate_active_processes() -> None:
    """Kill every in-flight trial JVM (best effort).

    The JVMs are started in their own session, so killing the process group
    also terminates any helper threads/processes the JVM spawned.
    """
    with _process_lock:
        processes = list(_active_processes)
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except Exception:  # pragma: no cover - best effort
                pass


# ── JVM option plumbing ─────────────────────────────────────────────────


def effective_java_mem(
    base_java_mem: str,
    threads_per_trial: int,
    cap_jvm_cpus: bool,
) -> str:
    """Add the sampling-thread property (and optionally the CPU cap) to the
    JVM options string.

    ``-Dontologyutils.samplingThreads`` is what the Java side reads instead of
    its hardcoded default; ``-XX:ActiveProcessorCount`` keeps each JVM's own
    thread pools (GC, ForkJoin) sized for its share of the CPU budget rather
    than for the whole machine.
    """
    parts = base_java_mem.split()
    parts.append(f"-Dontologyutils.samplingThreads={threads_per_trial}")
    if cap_jvm_cpus:
        parts.append(f"-XX:ActiveProcessorCount={threads_per_trial}")
    return " ".join(parts)


# ── One trial, run as a subprocess ──────────────────────────────────────


@dataclass
class TrialOutcome:
    attempt_number: int
    seed: int
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    payload: dict


def _run_trial_process(cmd: list[str], attempt_number: int, seed: int) -> TrialOutcome:
    """Spawn one trial JVM, wait for it, and return its outcome."""
    start = time.perf_counter()
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    with _process_lock:
        _active_processes.add(process)
    try:
        stdout, stderr = process.communicate()
        elapsed = time.perf_counter() - start
    finally:
        with _process_lock:
            _active_processes.discard(process)

    raw_stdout = stdout or ""
    return TrialOutcome(
        attempt_number=attempt_number,
        seed=seed,
        returncode=process.returncode,
        stdout=raw_stdout,
        stderr=stderr or "",
        elapsed_seconds=elapsed,
        payload=parse_trial_stdout(raw_stdout),
    )


# ── Entry point ─────────────────────────────────────────────────────────


def run_experiment(args: argparse.Namespace) -> None:
    """Execute the paper's experiment, reporting fatal errors to the notifier.

    :param args: The parsed command line.
    """
    notify_enabled = notifier.configure(getattr(args, "notify", None))
    if notify_enabled and not getattr(args, "dry_run", False):
        print("[notifier] Telegram progress notifications enabled.")

    try:
        _run_experiment(args)
    except BaseException as exc:
        _terminate_active_processes()
        notifier.notify_exception(exc)
        raise


def _resolve_ontology_subset(
    ontologies: list[Path],
    requested: str | None,
) -> list[Path]:
    """Restrict the ontology list to *requested* (comma-separated stems)."""
    if not requested:
        return ontologies
    wanted = {token.strip().lower() for token in requested.split(",") if token.strip()}
    selected = [o for o in ontologies if o.stem.lower() in wanted]
    missing = wanted - {o.stem.lower() for o in selected}
    if missing:
        raise SystemExit(f"Unknown ontology name(s): {', '.join(sorted(missing))}")
    if not selected:
        raise SystemExit("No ontology matched --ontologies")
    return selected


def _run_experiment(args: argparse.Namespace) -> None:
    parallel_trials = max(1, int(getattr(args, "parallel_trials", 1)))
    threads_per_trial = int(getattr(args, "threads_per_trial", 0) or 16)
    cap_jvm_cpus = bool(getattr(args, "cap_jvm_cpus", True))
    java_mem = effective_java_mem(args.java_mem, threads_per_trial, cap_jvm_cpus)

    if args.dry_run:
        print("[DRY RUN] Effective configuration (paper experiment):")
        print(f"  PARALLEL_TRIALS          = {parallel_trials}")
        print(f"  THREADS_PER_TRIAL        = {threads_per_trial}")
        print(f"  CAP_JVM_CPUS             = {cap_jvm_cpus}")
        print(f"  TOTAL_SAMPLING_THREADS   = {parallel_trials * threads_per_trial}")
        print(f"  JAVA_MEM                 = {java_mem}")
        print(f"  N_TRIALS_PER_ONTOLOGY    = {args.n_trials}")
        print(f"  BASE_SEED                = {args.seed if args.seed is not None else args.base_seed}")
        print(f"  STEP                     = {args.step}")
        print(f"  ANALYSIS_INTERVAL_ROUNDS = {args.analysis_interval}")
        print(f"  CONSECUTIVE_FAILURE_LIMIT= {args.consecutive_failure_limit}")
        print(f"  REMOVAL_TIMEOUT_SECONDS  = {args.removal_timeout}")
        print(f"  WEAKENING_TIMEOUT_SECONDS= {args.weakening_timeout}")
        print(f"  POWER_INDEX_TIMEOUT_SECS = {args.power_index_timeout}")
        print(f"  MAKE_INCONSISTENT_TO     = {args.make_inconsistent_timeout}")
        print(f"  INCONSISTENT_DIR         = {args.inconsistent_dir}")
        print(f"  ONTOLOGIES               = {args.ontologies or '<all>'}")
        print(f"  LIB_DIR                  = {args.lib_dir}")
        print(f"  EXPERIMENT_CLASS         = {EXPERIMENT_CLASS}")
        print(f"  ANALYSIS                 = {run_analysis.__module__}")
        print(f"  RUN_ID                   = {args.run_id or '<auto>'}")
        print()
        print("No experiments run. Omit --dry-run to execute.")
        return

    max_successes = args.n_trials
    if max_successes <= 0:
        raise SystemExit("N_TRIALS_PER_ONTOLOGY must be positive")

    run_id = args.run_id or datetime.now().strftime("%Y%m%d%H%M%S%f")
    data_dir = args.data_dir or (ANALYSIS_DIR / f"data-{run_id}")
    results_dir = args.results_dir or (ANALYSIS_DIR / f"results-{run_id}")

    effective_base_seed = args.seed if args.seed is not None else args.base_seed
    effective_step = args.step
    effective_analysis_interval = args.analysis_interval
    lib_dir = args.lib_dir

    ontologies = _resolve_ontology_subset(
        list_ontologies(args.inconsistent_dir), args.ontologies
    )

    print(
        f"Parallel run: K={parallel_trials} trials in flight, "
        f"{threads_per_trial} sampling threads per trial, {len(ontologies)} ontologies"
    )

    # Sort by axiom count (smallest first) for early feedback, exactly as the
    # sequential runner does; the sort defines the seed indexing, so it must not
    # be changed between configurations that are meant to be comparable.
    print("Classifying ontologies to sort by axiom count...")
    axiom_counts: dict[str, int] = {}
    for ontology in ontologies:
        name = ontology.stem
        result = classify_ontology(ontology, java_mem=java_mem, lib_dir=lib_dir, timeout=120)
        if result is not None:
            axiom_counts[name] = result.axioms
            print(f"  {name}: {result.axioms} axioms")
        else:
            axiom_counts[name] = 10**9
            print(f"  {name}: classification FAILED (placed at end)")
    ontologies.sort(key=lambda p: axiom_counts.get(p.stem, 10**9))
    print(f"Ontology processing order: {[o.stem for o in ontologies]}")

    iic_header = IIC_KEYS + ["run_id"]
    runtime_header = [
        "trial_number",
        "seed",
        "run_id",
        "trial_status",
        "error_type",
        "failure_stage",
        "error_message",
        "trial_elapsed_seconds",
    ] + [f"{repair_id}_ms" for repair_id in RUNTIME_KEYS]

    successes: dict[str, int] = {}
    attempts: dict[str, int] = {}
    failures: dict[str, list[tuple[int, int, str]]] = {}
    outcome_stats: dict[str, dict[str, dict[str, float]]] = {}
    iic_paths: dict[str, Path] = {}
    runtime_paths: dict[str, Path] = {}
    log_paths: dict[str, Path] = {}

    for ontology in ontologies:
        name = ontology.stem
        successes[name] = 0
        attempts[name] = 0
        failures[name] = []
        outcome_stats[name] = {
            "success": {"count": 0, "total": 0.0},
            "time_limit_exceeded": {"count": 0, "total": 0.0},
            "memory_limit_exceeded": {"count": 0, "total": 0.0},
        }
        iic_path, runtime_path, log_path = make_paths_for_ontology(name, data_dir)
        iic_paths[name] = iic_path
        runtime_paths[name] = runtime_path
        log_paths[name] = log_path

    is_resume = args.run_id is not None
    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    if is_resume:
        # ``attempts`` comes back as the number of existing runtime rows, which
        # equals the highest ``trial_number`` because one row is written per
        # attempt; it is therefore safe as the base for the next attempt index.
        successes, attempts, failures = resume_state(data_dir, [o.stem for o in ontologies])

    for ontology in ontologies:
        name = ontology.stem
        ensure_csv_with_header(iic_paths[name], iic_header)
        ensure_csv_with_header(runtime_paths[name], runtime_header)
        log_path = log_paths[name]
        if is_resume and log_path.exists():
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\n=== run_trials RESUMED at {timestamp()} run_id={run_id} ===\n")
                log.write(f"resuming from successes={successes[name]} attempts={attempts[name]}\n")
        else:
            with open(log_path, "w" if not is_resume else "a", encoding="utf-8") as log:
                log.write(f"=== run_trials started at {timestamp()} run_id={run_id} ===\n")
                log.write(f"ontology_path={ontology}\n")
                log.write(f"iic_csv={iic_paths[name]}\n")
                log.write(f"runtime_csv={runtime_paths[name]}\n")
                log.write(f"parallel_trials={parallel_trials} threads_per_trial={threads_per_trial}\n")

    wall_clock_start = time.perf_counter()

    notifier.notify_run_start(
        run_id=run_id,
        n_ontologies=len(ontologies),
        target_successes=max_successes,
        base_seed=effective_base_seed,
        data_dir=data_dir,
        results_dir=results_dir,
    )

    ontologies_finished = 0

    try:
        for ontology_index, ontology in enumerate(ontologies):
            name = ontology.stem
            if successes[name] >= max_successes:
                print(
                    f"Skipping {name} - already has {successes[name]} successes "
                    f"(target {max_successes})"
                )
                continue

            ontology_start = time.perf_counter()
            axiom_count = axiom_counts.get(name)
            if axiom_count is None or axiom_count >= 10**9:
                axiom_count = None
            notifier.notify_ontology_start(
                index=ontology_index + 1,
                total=len(ontologies),
                name=name,
                axioms=axiom_count,
            )

            aborted = _run_ontology_parallel(
                ontology=ontology,
                ontology_index=ontology_index,
                ontologies=ontologies,
                max_successes=max_successes,
                parallel_trials=parallel_trials,
                base_seed=effective_base_seed,
                step=effective_step,
                run_id=run_id,
                java_mem=java_mem,
                lib_dir=lib_dir,
                args=args,
                iic_path=iic_paths[name],
                runtime_path=runtime_paths[name],
                log_path=log_paths[name],
                runtime_header=runtime_header,
                successes=successes,
                attempts=attempts,
                failures=failures,
                outcome_stats=outcome_stats,
            )

            print(f"Completed {name}: {successes[name]} successes in {attempts[name]} attempts")
            notifier.notify_ontology_end(
                index=ontology_index + 1,
                total=len(ontologies),
                name=name,
                successes=successes[name],
                attempts=attempts[name],
                elapsed_seconds=time.perf_counter() - ontology_start,
            )

            ontologies_finished += 1
            if effective_analysis_interval > 0 and ontologies_finished % effective_analysis_interval == 0:
                try:
                    analysis_outputs = run_analysis(data_dir, results_dir)
                    print(f"Analysis updated after {name}:")
                    for key, value in analysis_outputs.items():
                        print(f"  {key}={value}")
                except Exception as exc:
                    print(f"Warning: analysis failed after {name}: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted - terminating in-flight trials...", file=sys.stderr)
        _terminate_active_processes()
        raise

    wall_clock_seconds = time.perf_counter() - wall_clock_start

    for ontology in ontologies:
        name = ontology.stem
        with open(log_paths[name], "a", encoding="utf-8") as log:
            log.write(
                f"=== run_trials finished at {timestamp()} for {name} - "
                f"{successes[name]} successes, {len(failures[name])} failures, "
                f"{attempts[name]} attempts ===\n"
            )
            if failures[name]:
                log.write("Failed attempts:\n")
                for attempt_number, seed_val, msg in failures[name]:
                    log.write(f"  attempt={attempt_number} seed={seed_val} msg={msg}\n")
            stats_map = outcome_stats[name]
            log.write("Timing summary:\n")
            for outcome in ("success", "time_limit_exceeded", "memory_limit_exceeded"):
                count = int(stats_map[outcome]["count"])
                total = float(stats_map[outcome]["total"])
                avg = total / count if count else 0.0
                log.write(f"  {outcome}: count={count} total_seconds={total:.6f} avg_seconds={avg:.6f}\n")
            success_rate = successes[name] / attempts[name] if attempts[name] else 0.0
            log.write(f"  success_rate={success_rate:.4f}\n")
            log.write(f"  wall_clock_seconds_total_run={wall_clock_seconds:.6f}\n")

    analysis_outputs = run_analysis(data_dir, results_dir)

    notifier.notify_run_end(
        run_id=run_id,
        n_ontologies=len(ontologies),
        wall_clock_seconds=wall_clock_seconds,
        data_dir=data_dir,
        results_dir=results_dir,
        analysis_outputs=analysis_outputs,
    )

    print("Done:")
    print(f"  run_id={run_id}")
    print(f"  ontologies={len(ontologies)}")
    print(f"  parallel_trials={parallel_trials}")
    print(f"  threads_per_trial={threads_per_trial}")
    print(f"  wall_clock_seconds={wall_clock_seconds:.1f}")
    print(f"  target_successes_per_ontology={max_successes}")
    print(f"  data_dir={data_dir}")
    print(f"  results_dir={results_dir}")


def _run_ontology_parallel(
    *,
    ontology: Path,
    ontology_index: int,
    ontologies: list[Path],
    max_successes: int,
    parallel_trials: int,
    base_seed: int,
    step: int,
    run_id: str,
    java_mem: str,
    lib_dir: Path,
    args: argparse.Namespace,
    iic_path: Path,
    runtime_path: Path,
    log_path: Path,
    runtime_header: list[str],
    successes: dict[str, int],
    attempts: dict[str, int],
    failures: dict[str, list[tuple[int, int, str]]],
    outcome_stats: dict[str, dict[str, dict[str, float]]],
) -> bool:
    """Run trials for one ontology with up to ``parallel_trials`` in flight.

    Returns ``True`` when the ontology was aborted for repeated failures.
    """
    name = ontology.stem
    consecutive_failures = 0
    aborted = False

    def log_line(text: str) -> None:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(text)

    log_line(f"\n=== Starting parallel trials for {name} at {timestamp()} ===\n")

    in_flight: dict[Future, tuple[int, int]] = {}

    def launch(pool: ThreadPoolExecutor) -> None:
        """Assign the next attempt number (and its seed) and submit it."""
        attempts[name] += 1
        attempt_number = attempts[name]
        seed = seed_for_attempt(
            ontology_index, attempt_number - 1, len(ontologies), base_seed, step
        )
        cmd = build_single_trial_command(
            ontology_path=ontology,
            seed=seed,
            run_id=run_id,
            java_mem=java_mem,
            lib_dir=lib_dir,
            removal_timeout=args.removal_timeout,
            weakening_timeout=args.weakening_timeout,
            power_index_timeout=args.power_index_timeout,
            make_inconsistent_timeout=args.make_inconsistent_timeout,
        )
        with open(log_path, "a", encoding="utf-8") as log:
            write_log_header(log, name, attempt_number, seed)
        in_flight[pool.submit(_run_trial_process, cmd, attempt_number, seed)] = (
            attempt_number,
            seed,
        )

    with ThreadPoolExecutor(max_workers=parallel_trials, thread_name_prefix="trial") as pool:
        while True:
            if not aborted:
                while (
                    len(in_flight) < parallel_trials
                    and successes[name] + len(in_flight) < max_successes
                ):
                    launch(pool)

            if not in_flight:
                break

            done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for future in done:
                attempt_number, seed = in_flight.pop(future)
                outcome = future.result()
                if _record_attempt(
                    outcome=outcome,
                    name=name,
                    run_id=run_id,
                    iic_path=iic_path,
                    runtime_path=runtime_path,
                    runtime_header=runtime_header,
                    log_path=log_path,
                    successes=successes,
                    failures=failures,
                    outcome_stats=outcome_stats,
                ):
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

            if not aborted and consecutive_failures >= args.consecutive_failure_limit:
                aborted = True
                log_line(
                    f"=== ABORTED {name} after {consecutive_failures} consecutive "
                    f"failures at {timestamp()} (draining in-flight trials) ===\n"
                )
                print(
                    f"Aborted {name}: {consecutive_failures} consecutive failures, "
                    f"{successes[name]} successes before abort"
                )
                notifier.notify_abort(
                    index=ontology_index + 1,
                    total=len(ontologies),
                    name=name,
                    consecutive_failures=consecutive_failures,
                    successes=successes[name],
                )

    if aborted:
        abort_dir = runtime_path.parent / "aborted"
        abort_dir.mkdir(parents=True, exist_ok=True)
        for csv_path in (iic_path, runtime_path):
            if csv_path.exists():
                destination = abort_dir / csv_path.name
                csv_path.rename(destination)
                print(f"  Moved {csv_path.name} to {destination}")

    return aborted


def _record_attempt(
    *,
    outcome: TrialOutcome,
    name: str,
    run_id: str,
    iic_path: Path,
    runtime_path: Path,
    runtime_header: list[str],
    log_path: Path,
    successes: dict[str, int],
    failures: dict[str, list[tuple[int, int, str]]],
    outcome_stats: dict[str, dict[str, dict[str, float]]],
) -> bool:
    """Persist one finished attempt. Returns ``True`` when it succeeded."""
    attempt_number = outcome.attempt_number
    seed = outcome.seed
    payload = outcome.payload
    attempt_outcome = "memory_limit_exceeded"
    error_message = ""

    with open(log_path, "a", encoding="utf-8") as log:
        if outcome.stdout:
            log.write("--- stdout ---\n")
            log.write(outcome.stdout + "\n")
        if outcome.stderr:
            log.write("--- stderr ---\n")
            log.write(
                outcome.stderr[:3000]
                + ("\n...[truncated]\n" if len(outcome.stderr) > 3000 else "\n")
            )

        if outcome.returncode < 0:
            error_message = f"negative return code {outcome.returncode}"
            log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
        elif payload:
            attempt_outcome = normalize_status(payload.get("trial_status"))
            if attempt_outcome == "success":
                try:
                    append_iic_row(iic_path, payload, run_id)
                    successes[name] += 1
                    log.write(
                        f"SUCCESS: attempt={attempt_number} seed={seed} "
                        f"success_count={successes[name]}\n"
                    )
                except Exception as exc:
                    attempt_outcome = "memory_limit_exceeded"
                    error_message = f"IIC append error: {exc}"
                    log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
            else:
                error_message = payload.get("error_message") or payload.get("error_type") or ""
                log.write(
                    f"FAIL: attempt={attempt_number} seed={seed} status={attempt_outcome} "
                    f"stage={payload.get('failure_stage')} message={error_message}\n"
                )
        else:
            log.write(f"FAIL: attempt={attempt_number} seed={seed} - empty payload\n")

        # Re-classify Java ExecutionExceptions the Java side mislabels as memory.
        if attempt_outcome != "success" and "ExecutionException" in error_message:
            attempt_outcome = "java_exception"

        if attempt_outcome != "success":
            if not error_message:
                if payload:
                    error_message = payload.get("error_message") or payload.get("error_type") or ""
                if not error_message:
                    error_message = f"process exited with code {outcome.returncode}"
            failures[name].append((attempt_number, seed, error_message))

        log.write(
            f"progress: ontology={name} attempt={attempt_number} "
            f"success={successes[name]}\n"
        )

    runtime_row = runtime_row_from_payload(
        attempt_number,
        seed,
        payload,
        outcome.elapsed_seconds,
        attempt_outcome,
        run_id,
    )
    append_dict_row(runtime_path, runtime_header, runtime_row)

    if attempt_outcome in outcome_stats[name]:
        outcome_stats[name][attempt_outcome]["count"] += 1
        outcome_stats[name][attempt_outcome]["total"] += outcome.elapsed_seconds

    return attempt_outcome == "success"
