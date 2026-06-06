"""Run round-robin single-trial Java runs for all ontologies and collect A/B results.

Execution policy (Option B):
- iterate ontologies in rounds
- run one attempt per ontology per round
- continue until each ontology reaches N successful trials

Artifacts are grouped by one run timestamp:
- analysis/data-<timestamp>/
    - iic-<ontology>.csv
    - runtime-<ontology>.csv
    - run_trials-<ontology>.log
- analysis/results-<timestamp>/
    - iic_summary.csv
    - runtime_summary.csv
    - outcome_summary.csv
    - combined_report.md
    - combined_report.tex
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import analyze_results

# ----------------- CONFIGURE HERE -----------------
N_TRIALS_PER_ONTOLOGY = 100
BASE_SEED = 13
STEP = 100

# run analysis every K rounds (set to 1 to run after every round)
ANALYSIS_INTERVAL_ROUNDS = 1

REMOVAL_TIMEOUT_SECONDS = 300
WEAKENING_TIMEOUT_SECONDS = 300
POWER_INDEX_TIMEOUT_SECONDS = 300
MAKE_INCONSISTENT_TIMEOUT_SECONDS = 300

A_REPAIRS = ["A1", "A2", "A3"]
B_REPAIRS = [f"B{i}" for i in range(1, 10)]
IIC_KEYS = [f"{b}_vs_{a}" for b in B_REPAIRS for a in A_REPAIRS]
RUNTIME_KEYS = A_REPAIRS + B_REPAIRS

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"
INCONSISTENT_DIR = REPO_ROOT / "inconsistent"
ANALYSIS_DIR = Path(__file__).parent
RUN_ID = datetime.now().strftime("%Y%m%d%H%M%S%f")
RUN_DATA_DIR = ANALYSIS_DIR / f"data-{RUN_ID}"
RUN_RESULTS_DIR = ANALYSIS_DIR / f"results-{RUN_ID}"
# -------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all configurable options.

    Defaults are read from the CONFIGURE HERE section above.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run round-robin single-trial Java experiments for all ontologies "
            "and collect A/B results."
        ),
    )

    # --- Trial control ---
    parser.add_argument(
        "-n", "--n-trials",
        type=int, default=N_TRIALS_PER_ONTOLOGY,
        help="Target number of successful trials per ontology (default: %(default)s)",
    )
    parser.add_argument(
        "--base-seed",
        type=int, default=BASE_SEED,
        help="Base random seed (default: %(default)s)",
    )
    parser.add_argument(
        "--step",
        type=int, default=STEP,
        help="Seed step between attempts (default: %(default)s)",
    )
    parser.add_argument(
        "--analysis-interval",
        type=int, default=ANALYSIS_INTERVAL_ROUNDS,
        help="Run analysis every K rounds; 0 to disable (default: %(default)s)",
    )

    # --- Timeouts ---
    parser.add_argument(
        "--removal-timeout",
        type=int, default=REMOVAL_TIMEOUT_SECONDS,
        help="Removal repair timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--weakening-timeout",
        type=int, default=WEAKENING_TIMEOUT_SECONDS,
        help="Weakening repair timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--power-index-timeout",
        type=int, default=POWER_INDEX_TIMEOUT_SECONDS,
        help="Power index computation timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--make-inconsistent-timeout",
        type=int, default=MAKE_INCONSISTENT_TIMEOUT_SECONDS,
        help="Make-inconsistent timeout in seconds (default: %(default)s)",
    )

    # --- Path overrides ---
    parser.add_argument(
        "--inconsistent-dir",
        type=Path, default=INCONSISTENT_DIR,
        help="Directory containing inconsistent ontologies (default: %(default)s)",
    )
    parser.add_argument(
        "--lib-dir",
        type=Path, default=LIB_DIR,
        help="Directory containing the shaded jar (default: %(default)s)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path, default=None,
        help=(
            "Output data directory. "
            "If --run-id is given but --data-dir is not, defaults to analysis/data-<run-id>. "
            "Otherwise defaults to analysis/data-<auto-run-id>."
        ),
    )
    parser.add_argument(
        "--results-dir",
        type=Path, default=None,
        help=(
            "Output results directory. "
            "If --run-id is given but --results-dir is not, defaults to analysis/results-<run-id>. "
            "Otherwise defaults to analysis/results-<auto-run-id>."
        ),
    )

    # --- Resume / Run identity ---
    parser.add_argument(
        "--run-id",
        type=str, default=None,
        help=(
            "Run identifier. Auto-generated as timestamp if not provided. "
            "When resuming an existing experiment, point this to the existing run ID "
            "and the script will continue from where it left off."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int, default=None,
        help=(
            "Explicit seed override. When used with --run-id, this sets the base seed "
            "for the resumed run. When omitted during resume, the base seed from the "
            "CONFIGURE HERE section is used."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the effective configuration and exit without running any experiments.",
    )

    # --- Positional (backward compatible) ---
    parser.add_argument(
        "n_trials_pos",
        type=int, nargs="?",
        help="[DEPRECATED] Positional argument for N_TRIALS_PER_ONTOLOGY",
    )

    return parser


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def find_shaded_jar() -> Path:
    candidates = sorted(LIB_DIR.glob("shaded-ontologyutils-*.jar"))
    if not candidates:
        raise FileNotFoundError(f"Could not find shaded-ontologyutils-*.jar under {LIB_DIR}")
    return candidates[-1]


SHADED_JAR = find_shaded_jar()
JAVA_BASE = [
    "java",
    "-Xms1g",
    "-Xmx8g",
    "-Xss8m",
    "-cp",
    str(SHADED_JAR),
    "www.ontologyutils.apps.SingleTrialExperiment",
]


def list_ontologies() -> list[Path]:
    ontologies = sorted(INCONSISTENT_DIR.glob("*.owl"))
    if not ontologies:
        raise FileNotFoundError(f"No .owl files found in {INCONSISTENT_DIR}")
    return ontologies


def make_paths_for_ontology(ontology_name: str) -> tuple[Path, Path, Path]:
    iic_path = RUN_DATA_DIR / f"iic-{ontology_name}.csv"
    runtime_path = RUN_DATA_DIR / f"runtime-{ontology_name}.csv"
    log_path = RUN_DATA_DIR / f"run_trials-{ontology_name}.log"
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


def parse_trial_json(stdout: str) -> dict:
    payload = json.loads(stdout.strip() or "{}")
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def run_trial(seed: int, ontology_path: Path) -> tuple[int, str, str]:
    args_list = [
        "--ontology", str(ontology_path),
        "--seed", str(seed),
        "--run-id", str(RUN_ID),
        "--removal-timeout-secs", str(REMOVAL_TIMEOUT_SECONDS),
        "--weakening-timeout-secs", str(WEAKENING_TIMEOUT_SECONDS),
        "--power-index-timeout-secs", str(POWER_INDEX_TIMEOUT_SECONDS),
        "--make-inconsistent-timeout-secs", str(MAKE_INCONSISTENT_TIMEOUT_SECONDS),
    ]
    cmd = JAVA_BASE + args_list
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", shell=False)
    return proc.returncode, proc.stdout, proc.stderr


def write_log_header(handle, round_number: int, attempt_number: int, seed: int) -> None:
    handle.write(
        f"----- Round {round_number} attempt={attempt_number} seed={seed} at {timestamp()} -----\n"
    )


def append_iic_row(iic_path: Path, payload: dict) -> None:
    iic_values = payload.get("iic_values") if isinstance(payload, dict) else {}
    iic_values = iic_values if isinstance(iic_values, dict) else {}
    row = {key: iic_values.get(key) for key in IIC_KEYS}
    row["run_id"] = payload.get("run_id", RUN_ID)
    with open(iic_path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IIC_KEYS + ["run_id"])
        writer.writerow(row)


def runtime_row_from_payload(
    trial_number: int,
    seed: int,
    payload: dict | None,
    trial_elapsed_seconds: float,
    outcome: str,
) -> dict:
    repair_runtimes = payload.get("repair_runtimes_ms") if isinstance(payload, dict) else {}
    repair_runtimes = repair_runtimes if isinstance(repair_runtimes, dict) else {}
    row = {
        "trial_number": trial_number,
        "seed": seed,
        "run_id": RUN_ID,
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


def seed_for_attempt(ontology_index: int, attempted_for_ontology: int, ontology_count: int) -> int:
    return BASE_SEED + ontology_index + attempted_for_ontology * STEP * ontology_count


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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

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

    globals().update({
        "N_TRIALS_PER_ONTOLOGY": max_successes,
        "BASE_SEED": effective_base_seed,
        "STEP": args.step,
        "ANALYSIS_INTERVAL_ROUNDS": args.analysis_interval,
        "REMOVAL_TIMEOUT_SECONDS": args.removal_timeout,
        "WEAKENING_TIMEOUT_SECONDS": args.weakening_timeout,
        "POWER_INDEX_TIMEOUT_SECONDS": args.power_index_timeout,
        "MAKE_INCONSISTENT_TIMEOUT_SECONDS": args.make_inconsistent_timeout,
        "LIB_DIR": args.lib_dir,
        "INCONSISTENT_DIR": args.inconsistent_dir,
        "RUN_ID": run_id,
        "RUN_DATA_DIR": data_dir,
        "RUN_RESULTS_DIR": results_dir,
    })

    # Recompute jar- and java-related globals since LIB_DIR may have changed
    globals()["SHADED_JAR"] = find_shaded_jar()
    globals()["JAVA_BASE"] = [
        "java",
        "-Xms1g",
        "-Xmx8g",
        "-Xss8m",
        "-cp",
        str(SHADED_JAR),
        "www.ontologyutils.apps.SingleTrialExperiment",
    ]

    ontologies = list_ontologies()

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
        outcome_stats[name] = {
            "success": {"count": 0, "total": 0.0},
            "time_limit_exceeded": {"count": 0, "total": 0.0},
            "memory_limit_exceeded": {"count": 0, "total": 0.0},
        }
        iic_path, runtime_path, log_path = make_paths_for_ontology(name)
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
                log.write(f"\n=== run_trials RESUMED at {timestamp()} run_id={RUN_ID} ===\n")
                log.write(f"resuming from successes={successes[name]} attempts={attempts[name]}\n")
        else:
            with open(log_path, "w" if not is_resume else "a", encoding="utf-8") as log:
                log.write(f"=== run_trials started at {timestamp()} run_id={RUN_ID} ===\n")
                log.write(f"ontology_path={ontology}\n")
                log.write(f"iic_csv={iic_paths[name]}\n")
                log.write(f"runtime_csv={runtime_paths[name]}\n")

    wall_clock_start = time.perf_counter()
    round_number = 0

    while any(successes[name] < max_successes for name in successes):
        round_number += 1
        for ontology_index, ontology in enumerate(ontologies):
            name = ontology.stem
            if successes[name] >= max_successes:
                continue

            attempts[name] += 1
            attempt_number = attempts[name]
            seed = seed_for_attempt(ontology_index, attempt_number - 1, len(ontologies))
            log_path = log_paths[name]

            with open(log_path, "a", encoding="utf-8") as log:
                write_log_header(log, round_number, attempt_number, seed)
                trial_start = time.perf_counter()
                retcode, stdout, stderr = run_trial(seed, ontology)
                trial_elapsed = time.perf_counter() - trial_start

                payload = None
                attempt_outcome = "memory_limit_exceeded"
                error_message = ""

                if stdout:
                    log.write("--- stdout ---\n")
                    log.write(stdout + "\n")
                if stderr:
                    log.write("--- stderr ---\n")
                    log.write(stderr[:3000] + ("\n...[truncated]\n" if len(stderr) > 3000 else "\n"))

                if retcode < 0:
                    error_message = f"negative return code {retcode}"
                    log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
                else:
                    try:
                        payload = parse_trial_json(stdout)
                    except Exception as exc:
                        error_message = f"JSON parse error: {exc}"
                        log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
                    else:
                        attempt_outcome = normalize_status(payload.get("trial_status"))
                        if attempt_outcome == "success":
                            try:
                                append_iic_row(iic_paths[name], payload)
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

                if attempt_outcome != "success":
                    if not error_message:
                        if payload is not None:
                            error_message = payload.get("error_message") or payload.get("error_type") or ""
                        if not error_message:
                            error_message = f"process exited with code {retcode}"
                    failures[name].append((attempt_number, seed, error_message))

                runtime_row = runtime_row_from_payload(
                    attempt_number,
                    seed,
                    payload,
                    trial_elapsed,
                    attempt_outcome,
                )
                append_dict_row(runtime_paths[name], runtime_header, runtime_row)

                if attempt_outcome in outcome_stats[name]:
                    outcome_stats[name][attempt_outcome]["count"] += 1
                    outcome_stats[name][attempt_outcome]["total"] += trial_elapsed

                total_successes = sum(successes.values())
                total_attempts = sum(attempts.values())
                log.write(
                    f"progress: ontology_success={successes[name]}/{max_successes} "
                    f"global_success={total_successes}/{max_successes * len(ontologies)} "
                    f"global_attempts={total_attempts}\n"
                )

            time.sleep(0.05)

        # End of this round: refresh aggregated analysis every K rounds so results are updated incrementally
        try:
            if ANALYSIS_INTERVAL_ROUNDS and ANALYSIS_INTERVAL_ROUNDS > 0 and (round_number % ANALYSIS_INTERVAL_ROUNDS) == 0:
                analysis_outputs = analyze_results.run_analysis(RUN_DATA_DIR, RUN_RESULTS_DIR)
                print(f"Round {round_number} analysis updated:")
                for key, value in analysis_outputs.items():
                    print(f"  {key}={value}")
        except Exception as exc:  # keep the run going even if analysis fails temporarily
            print(f"Warning: analysis failed at round {round_number}: {exc}", file=sys.stderr)

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
                for attempt_number, seed, msg in failures[name]:
                    log.write(f"  attempt={attempt_number} seed={seed} msg={msg}\n")
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

    analysis_outputs = analyze_results.run_analysis(RUN_DATA_DIR, RUN_RESULTS_DIR)

    print("Done:")
    print(f"  run_id={RUN_ID}")
    print(f"  ontologies={len(ontologies)}")
    print(f"  target_successes_per_ontology={max_successes}")
    print(f"  data_dir={RUN_DATA_DIR}")
    print(f"  results_dir={RUN_RESULTS_DIR}")
    print("  analysis_outputs:")
    for key, value in analysis_outputs.items():
        print(f"    {key}={value}")


if __name__ == "__main__":
    main()


