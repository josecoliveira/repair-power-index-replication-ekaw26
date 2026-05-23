"""Run multiple single-trial Java runs via the bundled shaded jar and collect JSON results.

The Java app prints one JSON object per trial. This runner writes:

- one IIC master CSV with one row per successful trial
- one runtime CSV with one row per trial and all four repair runtimes
- one run log with stdout/stderr excerpts and attempt-level summaries

Failures are classified into:

- success
- time_limit_exceeded
- memory_limit_exceeded
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ----------------- CONFIGURE HERE -----------------
# Number of successful trials to collect
N_TRIALS = 100
BASE_SEED = 13
STEP = 100
# Resume counters (set non-zero to resume a previous run)
START_SUCCESSFUL_TRIALS = 0
START_ATTEMPTED_TRIALS = 0
# Output directory for run artifacts
OUT_DIR = Path(__file__).parent / "data" / "shapley-shapley"
# Timeouts (seconds) — match names used in RepairComparisonExperiment.java
REMOVAL_TIMEOUT_SECONDS = 300
WEAKENING_TIMEOUT_SECONDS = 300
POWER_INDEX_TIMEOUT_SECONDS = 300
MAKE_INCONSISTENT_TIMEOUT_SECONDS = 300

# Bundled shaded jar used by the replication package.
REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"


def find_shaded_jar():
	candidates = sorted(LIB_DIR.glob("shaded-ontologyutils-*.jar"))
	if not candidates:
		raise FileNotFoundError(f"Could not find shaded-ontologyutils-*.jar under {LIB_DIR}")
	return candidates[-1]


SHADED_JAR = find_shaded_jar()

# JVM flags to pass to the Java process
JAVA_BASE = [
	"java",
	"-Xms1g",
	"-Xmx8g",
	"-Xss8m",
	"-cp",
	str(SHADED_JAR),
	"www.ontologyutils.apps.SingleTrialExperiment",
]
# -------------------------------------------------

OUT_DIR.mkdir(parents=True, exist_ok=True)

# Run id (timestamp) appended to output filenames so downstream scripts can group by run
RUN_ID = datetime.now().strftime("%Y%m%d%H%M%S%f")


def timestamp():
	return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def timestamp_for_filename():
	return datetime.now().strftime("%Y%m%d%H%M%S%f")


def make_run_log_path(ontology_path):
	return Path(__file__).parent / f"run_trials-{ontology_path.stem}-{timestamp_for_filename()}.log"


def write_log_header(handle, trial_index, seed):
	handle.write(f"----- Trial {trial_index} seed={seed} at {timestamp()} -----\n")


def run_trial(seed, ontology_path):
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
	proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False)
	return proc.returncode, proc.stdout, proc.stderr


def ensure_csv_with_header(path, fieldnames):
	if path.exists() and path.stat().st_size > 0:
		return
	with open(path, "w", encoding="utf-8", newline="") as handle:
		writer = csv.DictWriter(handle, fieldnames=fieldnames)
		writer.writeheader()


def normalize_status(status):
	status = (status or "").strip().lower()
	if status in {"success", "time_limit_exceeded", "memory_limit_exceeded"}:
		return status
	return "memory_limit_exceeded"


def parse_trial_json(stdout):
	payload = json.loads(stdout.strip() or "{}")
	if not isinstance(payload, dict):
		raise ValueError("JSON root must be an object")
	return payload


def append_iic_row(master_path, payload):
	iic_values = payload.get("iic_values") or {}
	with open(master_path, "a", encoding="utf-8", newline="") as handle:
		writer = csv.writer(handle)
		writer.writerow([
			iic_values.get("power_vs_random"),
			iic_values.get("power_vs_not_in_largest_mcs"),
			iic_values.get("power_vs_weakening"),
			payload.get("run_id", RUN_ID),
		])


def runtime_row_from_payload(trial_index, seed, payload, trial_elapsed_seconds, outcome):
	repair_runtimes = payload.get("repair_runtimes_ms") if isinstance(payload, dict) else {}
	repair_runtimes = repair_runtimes if isinstance(repair_runtimes, dict) else {}
	return {
		"trial_number": trial_index,
		"seed": seed,
		"run_id": RUN_ID,
		"trial_status": outcome,
		"error_type": (payload or {}).get("error_type", "") if isinstance(payload, dict) else "",
		"failure_stage": (payload or {}).get("failure_stage", "") if isinstance(payload, dict) else "",
		"error_message": (payload or {}).get("error_message", "") if isinstance(payload, dict) else "",
		"trial_elapsed_seconds": f"{trial_elapsed_seconds:.6f}",
		"random_removal_ms": repair_runtimes.get("random_removal"),
		"not_in_largest_mcs_removal_ms": repair_runtimes.get("not_in_largest_mcs_removal"),
		"weakening_ms": repair_runtimes.get("weakening"),
		"power_index_ms": repair_runtimes.get("power_index"),
	}


def append_dict_row(path, fieldnames, row):
	with open(path, "a", encoding="utf-8", newline="") as handle:
		writer = csv.DictWriter(handle, fieldnames=fieldnames)
		writer.writerow(row)


def main():
	if len(sys.argv) < 2:
		raise SystemExit(f"Usage: {Path(sys.argv[0]).name} <ontology-path>")

	ontology_path = Path(sys.argv[1])
	max_trials = N_TRIALS
	if len(sys.argv) >= 3:
		try:
			max_trials = int(sys.argv[2])
		except Exception:
			raise SystemExit(f"Invalid N_TRIALS value: {sys.argv[2]}")
	if max_trials <= 0:
		raise SystemExit("N_TRIALS must be positive")

	run_log_path = make_run_log_path(ontology_path)
	successes = START_SUCCESSFUL_TRIALS
	failures = []
	outcome_stats = {
		"success": {"count": 0, "total": 0.0},
		"time_limit_exceeded": {"count": 0, "total": 0.0},
		"memory_limit_exceeded": {"count": 0, "total": 0.0},
	}
	attempt_runtime_records = []
	attempted = START_ATTEMPTED_TRIALS
	wall_clock_start = time.perf_counter()
	wall_clock_start_stamp = timestamp()

	ontology_basename = ontology_path.stem
	master_path = OUT_DIR / f"iic-{ontology_basename}-{RUN_ID}.csv"
	runtime_path = OUT_DIR / f"runtime-{ontology_basename}-{RUN_ID}.csv"
	iic_header = ["iic_power_vs_random", "iic_power_vs_not_in_largest_mcs", "iic_power_vs_weakening", "run_id"]
	runtime_header = [
		"trial_number",
		"seed",
		"run_id",
		"trial_status",
		"error_type",
		"failure_stage",
		"error_message",
		"trial_elapsed_seconds",
		"random_removal_ms",
		"not_in_largest_mcs_removal_ms",
		"weakening_ms",
		"power_index_ms",
	]
	ensure_csv_with_header(master_path, iic_header)
	ensure_csv_with_header(runtime_path, runtime_header)

	with open(run_log_path, "w", encoding="utf-8") as log:
		log.write(f"\n=== run_trials started at {wall_clock_start_stamp} run_id={RUN_ID} ===\n")
		log.write(f"ontology_path={ontology_path}\n")
		log.write(f"run_log_path={run_log_path}\n")
		log.write(f"iic_master_csv={master_path}\n")
		log.write(f"runtime_csv={runtime_path}\n")
		while successes < max_trials:
			attempt_number = attempted + 1
			seed = BASE_SEED + attempted * STEP
			write_log_header(log, attempt_number, seed)
			log.flush()

			trial_start = time.perf_counter()
			retcode, stdout, stderr = run_trial(seed, ontology_path)
			trial_elapsed = time.perf_counter() - trial_start
			attempt_outcome = "memory_limit_exceeded"
			payload = None
			error_message = ""

			if stdout:
				log.write("--- stdout ---\n")
				log.write(stdout + "\n")
			if stderr:
				log.write("--- stderr ---\n")
				log.write(stderr[:2000] + ("\n...[truncated]\n" if len(stderr) > 2000 else "\n"))

			if retcode < 0:
				error_message = f"negative return code {retcode}"
				log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
			else:
				try:
					payload = parse_trial_json(stdout)
				except Exception as e:
					error_message = f"JSON parse error: {e}"
					log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
				else:
					attempt_outcome = normalize_status(payload.get("trial_status"))
					if attempt_outcome == "success":
						try:
							append_iic_row(master_path, payload)
							successes += 1
							log.write(f"SUCCESS: attempt={attempt_number} seed={seed} appended to {master_path}\n")
						except Exception as e:
							attempt_outcome = "memory_limit_exceeded"
							error_message = f"IIC append error: {e}"
							failures.append((attempt_number, seed, error_message))
							log.write(f"FAIL: attempt={attempt_number} seed={seed} - {error_message}\n")
					else:
						error_message = payload.get("error_message") or payload.get("error_type") or ""
						log.write(
							f"FAIL: attempt={attempt_number} seed={seed} - status={attempt_outcome} "
							f"stage={payload.get('failure_stage')} message={error_message}\n"
						)

			if attempt_outcome != "success" and not error_message:
				if payload is not None:
					error_message = payload.get("error_message") or payload.get("error_type") or ""
				if not error_message:
					error_message = f"process exited with code {retcode}"
				failures.append((attempt_number, seed, error_message))

			runtime_row = runtime_row_from_payload(attempt_number, seed, payload, trial_elapsed, attempt_outcome)
			append_dict_row(runtime_path, runtime_header, runtime_row)

			attempt_runtime_records.append((attempt_number, seed, attempt_outcome, trial_elapsed))
			if attempt_outcome in outcome_stats:
				outcome_stats[attempt_outcome]["count"] += 1
				outcome_stats[attempt_outcome]["total"] += trial_elapsed

			attempted += 1
			log.write(f"progress: {successes} successful, {len(failures)} failed, {attempted} attempted so far\n")
			log.flush()
			time.sleep(0.1)

		wall_clock_seconds = time.perf_counter() - wall_clock_start
		success_count = outcome_stats["success"]["count"]
		success_total = outcome_stats["success"]["total"]
		success_avg = success_total / success_count if success_count else 0.0
		timeout_count = outcome_stats["time_limit_exceeded"]["count"]
		timeout_total = outcome_stats["time_limit_exceeded"]["total"]
		timeout_avg = timeout_total / timeout_count if timeout_count else 0.0
		mem_count = outcome_stats["memory_limit_exceeded"]["count"]
		mem_total = outcome_stats["memory_limit_exceeded"]["total"]
		mem_avg = mem_total / mem_count if mem_count else 0.0
		success_rate = successes / attempted if attempted else 0.0

		log.write(f"=== run_trials finished at {timestamp()} - {successes} successes, {len(failures)} failures, {attempted} attempted ===\n")
		if failures:
			log.write("Failed trials:\n")
			for t, s, m in failures:
				log.write(f"  trial={t} seed={s} msg={m}\n")
		log.write("Timing summary:\n")
		log.write(f"  wall_clock_start={wall_clock_start_stamp}\n")
		log.write(f"  wall_clock_end={timestamp()}\n")
		log.write(f"  total_wall_clock_seconds={wall_clock_seconds:.6f}\n")
		log.write(f"  successful: count={success_count} total_seconds={success_total:.6f} avg_seconds={success_avg:.6f}\n")
		log.write(f"  time_limit_exceeded: count={timeout_count} total_seconds={timeout_total:.6f} avg_seconds={timeout_avg:.6f}\n")
		log.write(f"  memory_limit_exceeded: count={mem_count} total_seconds={mem_total:.6f} avg_seconds={mem_avg:.6f}\n")
		log.write(f"  success_rate={success_rate:.4f}\n")
		log.write("  per_attempt_runtime_seconds:\n")
		for attempt_number, seed, outcome, elapsed_seconds in attempt_runtime_records:
			log.write(f"    attempt={attempt_number} seed={seed} outcome={outcome} runtime_seconds={elapsed_seconds:.6f}\n")
		log.write("\n")

	print(
		f"Done: {successes} successes, {len(failures)} failures. "
		f"IIC CSV: {master_path}. Runtime CSV: {runtime_path}. See {run_log_path}"
	)


if __name__ == "__main__":
	main()


