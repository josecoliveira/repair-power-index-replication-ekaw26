"""Run sequential per-ontology Java trials for all ontologies and collect A/B results.

Execution policy:
- sort ontologies by axiom count (smallest first)
- for each ontology, run trials until N successful trials are achieved
- move to the next ontology
- this way, if interrupted, completed ontologies have full results

Artifacts are grouped by one run timestamp:
- data-<timestamp>/
    - iic-<ontology>.csv
    - runtime-<ontology>.csv
    - run_trials-<ontology>.log
- results-<timestamp>/
    - iic_summary.csv
    - runtime_summary.csv
    - outcome_summary.csv
    - combined_report.md
    - combined_report.tex
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

from analysis.analyzer import run_analysis
from analysis.ontologyutils_service import (
    classify_ontology,
    run_single_trial_experiment,
)

# ── Experiment design constants (not user-configurable) ────────────────
A_REPAIRS = ["A1", "A2", "A3"]
B_REPAIRS = [f"B{i}" for i in range(1, 10)]
IIC_KEYS = [f"{b}_vs_{a}" for b in B_REPAIRS for a in A_REPAIRS]
RUNTIME_KEYS = A_REPAIRS + B_REPAIRS

# ── Path constants (based on package layout, not user-configurable) ────
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
INCONSISTENT_DIR = PACKAGE_ROOT / "ontologies" / "inconsistent"
ANALYSIS_DIR = PACKAGE_ROOT / "data"


# ── Helpers ────────────────────────────────────────────────────────────


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
    if status in {"success", "time_limit_exceeded", "memory_limit_exceeded"}:
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


# ── Experiment entry point ─────────────────────────────────────────────


def run_experiment(args: argparse.Namespace) -> None:
    """Execute the experiment with the given parsed arguments.

    All configuration defaults are supplied by the CLI layer
    (cli/run_trials.py) via the argparse.Namespace.
    """
    # Dry-run: print effective configuration and exit
    if args.dry_run:
        print("[DRY RUN] Effective configuration:")
        print(f"  N_TRIALS_PER_ONTOLOGY    = {args.n_trials_pos if args.n_trials_pos is not None else args.n_trials}")
        print(f"  BASE_SEED                = {args.seed if args.seed is not None else args.base_seed}")
        print(f"  STEP                     = {args.step}")
        print(f"  ANALYSIS_INTERVAL_ROUNDS = {args.analysis_interval}")
        print(f"  REMOVAL_TIMEOUT_SECONDS  = {args.removal_timeout}")
        print(f"  WEAKENING_TIMEOUT_SECONDS= {args.weakening_timeout}")
        print(f"  POWER_INDEX_TIMEOUT_SECS = {args.power_index_timeout}")
        print(f"  MAKE_INCONSISTENT_TO     = {args.make_inconsistent_timeout}")
        print(f"  JAVA_MEM                 = {args.java_mem}")
        print(f"  A_REPAIRS (from config)  = {A_REPAIRS}")
        print(f"  B_REPAIRS (from config)  = {B_REPAIRS}")
        print(f"  INCONSISTENT_DIR         = {args.inconsistent_dir}")
        print(f"  LIB_DIR                  = {args.lib_dir}")
        print(f"  RUN_ID                   = {args.run_id or '<auto>'}")
        print(f"  SEED (override)          = {args.seed}")
        print(f"  DATA_DIR                 = {args.data_dir or '<auto>'}")
        print(f"  RESULTS_DIR              = {args.results_dir or '<auto>'}")
        print()
        print("No experiments run. Use --dry-run=false or omit to execute.")
        return

    # Backward-compatible positional argument
    max_successes = args.n_trials
    if args.n_trials_pos is not None:
        max_successes = args.n_trials_pos
    if max_successes <= 0:
        raise SystemExit("N_TRIALS_PER_ONTOLOGY must be positive")

    # Resolve run identity and output directories
    run_id = args.run_id or datetime.now().strftime("%Y%m%d%H%M%S%f")
    data_dir = args.data_dir or (ANALYSIS_DIR / f"data-{run_id}")
    results_dir = args.results_dir or (ANALYSIS_DIR / f"results-{run_id}")

    effective_base_seed = args.seed if args.seed is not None else args.base_seed
    effective_step = args.step
    effective_analysis_interval = args.analysis_interval
    java_mem = args.java_mem
    lib_dir = args.lib_dir
    inconsistent_dir = args.inconsistent_dir

    ontologies = list_ontologies(inconsistent_dir)

    # Sort ontologies by axiom count (smallest first) so smaller ontologies
    # are processed earlier, giving faster initial feedback.
    print("Classifying ontologies to sort by axiom count...")
    axiom_counts: dict[str, int] = {}
    for ontology in ontologies:
        name = ontology.stem
        result = classify_ontology(ontology, java_mem=java_mem, lib_dir=lib_dir, timeout=120)
        if result is not None:
            axiom_counts[name] = result.axioms
            print(f"  {name}: {result.axioms} axioms")
        else:
            axiom_counts[name] = 10**9  # push unclassifiable to the end
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

    is_resume = args.run_id is not None

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

    if is_resume:
        successes, attempts, failures = resume_state(data_dir, [o.stem for o in ontologies])
        data_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)

    # Ensure CSV headers exist (for fresh runs) or are present (resume may have empty files)
    for ontology in ontologies:
        name = ontology.stem
        ensure_csv_with_header(iic_paths[name], iic_header)
        ensure_csv_with_header(runtime_paths[name], runtime_header)

    # Write or append to log files
    for ontology in ontologies:
        name = ontology.stem
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

    wall_clock_start = time.perf_counter()

    for ontology_index, ontology in enumerate(ontologies):
        name = ontology.stem
        if successes[name] >= max_successes:
            print(f"Skipping {name} - already has {successes[name]} successes (target {max_successes})")
            continue

        log_path = log_paths[name]
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n=== Starting sequential trials for {name} at {timestamp()} ===\n")

        while successes[name] < max_successes:
            attempts[name] += 1
            attempt_number = attempts[name]
            seed = seed_for_attempt(ontology_index, attempt_number - 1,
                                    len(ontologies), effective_base_seed, effective_step)

            with open(log_path, "a", encoding="utf-8") as log:
                write_log_header(log, name, attempt_number, seed)
                trial_start = time.perf_counter()
                result = run_single_trial_experiment(
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
                trial_elapsed = time.perf_counter() - trial_start

                payload = result.payload
                attempt_outcome = "memory_limit_exceeded"
                error_message = ""

                if result.stdout:
                    log.write("--- stdout ---\n")
                    log.write(result.stdout + "\n")
                if result.stderr:
                    log.write("--- stderr ---\n")
                    log.write(result.stderr[:3000] + ("\n...[truncated]\n" if len(result.stderr) > 3000 else "\n"))

                if result.returncode < 0:
                    error_message = f"negative return code {result.returncode}"
                    log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
                else:
                    if payload:
                        attempt_outcome = normalize_status(payload.get("trial_status"))
                        if attempt_outcome == "success":
                            try:
                                append_iic_row(iic_paths[name], payload, run_id)
                                successes[name] += 1
                                log.write(
                                    f"SUCCESS: attempt={attempt_number} seed={seed} "
                                    f"success_count={successes[name]}/{max_successes}\n"
                                )
                            except Exception as exc:
                                attempt_outcome = "memory_limit_exceeded"
                                error_message = f"IIC append error: {exc}"
                                failures[name].append((attempt_number, seed, error_message))
                                log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
                        else:
                            error_message = payload.get("error_message") or payload.get("error_type") or ""
                            log.write(
                                f"FAIL: attempt={attempt_number} seed={seed} status={attempt_outcome} "
                                f"stage={payload.get('failure_stage')} message={error_message}\n"
                            )
                    else:
                        log.write(f"FAIL: attempt={attempt_number} seed={seed} - empty payload\n")

                if attempt_outcome != "success":
                    if not error_message:
                        if payload:
                            error_message = payload.get("error_message") or payload.get("error_type") or ""
                        if not error_message:
                            error_message = f"process exited with code {result.returncode}"
                    failures[name].append((attempt_number, seed, error_message))

                runtime_row = runtime_row_from_payload(
                    attempt_number, seed, payload, trial_elapsed,
                    attempt_outcome, run_id,
                )
                append_dict_row(runtime_paths[name], runtime_header, runtime_row)

                if attempt_outcome in outcome_stats[name]:
                    outcome_stats[name][attempt_outcome]["count"] += 1
                    outcome_stats[name][attempt_outcome]["total"] += trial_elapsed

                log.write(
                    f"progress: ontology={name} attempt={attempt_number} "
                    f"success={successes[name]}/{max_successes}\n"
                )

            time.sleep(0.05)

        # Ontology complete: run analysis so partial results are saved if interrupted
        print(f"Completed {name}: {successes[name]} successes in {attempts[name]} attempts")
        try:
            analysis_outputs = run_analysis(data_dir, results_dir)
            print(f"Analysis updated after {name}:")
            for key, value in analysis_outputs.items():
                print(f"  {key}={value}")
        except Exception as exc:
            print(f"Warning: analysis failed after {name}: {exc}", file=sys.stderr)

    wall_clock_seconds = time.perf_counter() - wall_clock_start

    for ontology in ontologies:
        name = ontology.stem
        with open(log_paths[name], "a", encoding="utf-8") as log:
            log.write(
                f"=== run_trials finished at {timestamp()} for {name} - "
                f"{successes[name]} successes, {len(failures[name])} failures, {attempts[name]} attempts ===\n"
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

    print("Done:")
    print(f"  run_id={run_id}")
    print(f"  ontologies={len(ontologies)}")
    print(f"  target_successes_per_ontology={max_successes}")
    print(f"  data_dir={data_dir}")
    print(f"  results_dir={results_dir}")
    print("  analysis_outputs:")
    for key, value in analysis_outputs.items():
        print(f"    {key}={value}")
