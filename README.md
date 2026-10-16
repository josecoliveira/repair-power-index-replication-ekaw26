# Power Index Replication Package

This repository is self-contained for reproducing the power-index experiments from the paper.

It includes:

- `lib/shaded-ontologyutils-0.1.0.jar`
- `ontologies/inconsistent/*.owl`
- `analysis/` — model layer: reusable computation modules
  - `orchestrator.py` — experiment orchestration
  - `parallel_orchestrator.py` — experiment orchestration with several trials in flight
  - `analyzer.py` — analysis and report generation
  - `preprocessor.py` — ontology preprocessing pipeline
  - `classifier.py` — ontology classification
  - `notifier.py` — optional Telegram progress notifications
- `cli/` — view layer: thin CLI entry points
  - `run_trials.py` — CLI for the experiment runner
  - `run_trials_parallel.py` — CLI for the parallel experiment runner
  - `analyze_results.py` — CLI for the analysis module
  - `preprocess_ontologies.py` — CLI for the preprocessing pipeline
  - `classify_ontologies.py` — CLI for ontology classification
  - `estimator.py` — CLI for complexity estimation
  - `telegram_notify.py` — chat-ID discovery and notifier test helper

## Requirements

- Java 17
- Python 3.10+
- `pip`

Install the Python dependencies from the repository root:

```bash
python -m pip install -r requirements.txt
```

No Maven build is required for the replication scripts because they run against the bundled shaded jar.

## CLI Reference

### `preprocess_ontologies.py`

Clean up and make ontologies inconsistent. Runs a 3-stage pipeline:
1. **Cleanup** — `original/{name}.owl` → `cleanup/{name}.owl`
2. **Classify & Filter** — `cleanup/{name}.owl` → `alc/{name}.owl` (ALC only)
3. **MakeInconsistent** — `alc/{name}.owl` → `inconsistent/{name}.owl`

Processes all ontologies in `ontologies/original/` by default.

| Flag / Arg | Type | Default | Description |
|---|---|---|---|
| `ontologies` | `str [...]` | all | Ontology name(s) without `.owl` extension (e.g. `bctt co-wheat`). If omitted, all ontologies in `original/` are processed. |
| `-v`, `--verbose` | flag | `False` | Print detailed progress and Java subprocess output. |
| `--dry-run` | flag | `False` | Print the operations that would be performed without executing them. |
| `--force` | flag | `False` | Re-process ontologies even if the output files already exist. |
| `--java-mem` | `str` | `-Xms1g -Xmx8g -Xss8m` | JVM memory and stack options. |
| `-j`, `--workers` | `int` | CPU count | Number of parallel workers. |
| `--no-progress` | flag | `False` | Disable the live-progress display; fall back to simple log output. |
| `--start-stage` | `int` (1–3) | `1` | Pipeline stage to start from: `1`=cleanup (default), `2`=classify/filter, `3`=make-inconsistent. |
| `--timeout` | `int` | no timeout | Timeout in seconds for the MakeInconsistent stage. |

**Examples:**
```bash
python cli/preprocess_ontologies.py
python cli/preprocess_ontologies.py bctt elig
python cli/preprocess_ontologies.py --verbose co-wheat
python cli/preprocess_ontologies.py --dry-run --force
python cli/preprocess_ontologies.py --workers 8 --start-stage 2
python cli/preprocess_ontologies.py --start-stage 3 --timeout 600
```

### `classify_ontologies.py`

Classify all ontologies across the `original/`, `cleanup/`, and `inconsistent/` folders using a Java reasoner (HermiT via the bundled shaded jar) and produce a markdown table with logical axiom counts and classification results.

| Flag / Arg | Type | Default | Description |
|---|---|---|---|
| `-o`, `--output` | `Path` | `ontology_classification.md` | Output markdown file path. |
| `--java-mem` | `str` | `-Xms1g -Xmx8g -Xss8m` | JVM memory and stack options. |
| `--timeout` | `int` | `300` | Timeout per ontology in seconds. |
| `-v`, `--verbose` | flag | `False` | Print detailed progress and Java subprocess output. |
| `-j`, `--workers` | `int` | CPU count | Number of parallel workers. |
| `--no-progress` | flag | `False` | Disable the live-progress display; fall back to simple log output. |
| `--sort-by-axioms` | flag | `False` | Sort ontologies by axiom count (smallest first) instead of alphabetically. |

**Examples:**
```bash
python cli/classify_ontologies.py
python cli/classify_ontologies.py -o my_table.md
python cli/classify_ontologies.py --verbose --java-mem '-Xms2g -Xmx8g'
python cli/classify_ontologies.py --workers 8 --sort-by-axioms
```

### `run_trials.py`

Run round-robin single-trial Java experiments for all ontologies and collect A/B results comparing removal vs weakening repair strategies. The runner writes per-trial CSVs and logs under `data/data-<run-id>/`.

| Flag / Arg | Type | Default | Description |
|---|---|---|---|
| **Trial control** | | | |
| `-n`, `--n-trials` | `int` | `100` | Target number of successful trials per ontology. |
| `--base-seed` | `int` | `13` | Base random seed. |
| `--step` | `int` | `100` | Seed step between attempts. |
| `--analysis-interval` | `int` | `1` | Run the interim analysis after every K completed ontologies; `0` disables interim analysis (the final analysis after the last ontology always runs). |
| **Timeouts** | | | |
| `--removal-timeout` | `int` | `300` | Removal repair timeout in seconds. |
| `--weakening-timeout` | `int` | `300` | Weakening repair timeout in seconds. |
| `--power-index-timeout` | `int` | `300` | Power index computation timeout in seconds. |
| `--make-inconsistent-timeout` | `int` | `300` | Make-inconsistent timeout in seconds. |
| **Paths** | | | |
| `--inconsistent-dir` | `Path` | `ontologies/inconsistent/` | Directory containing inconsistent ontologies. |
| `--lib-dir` | `Path` | `lib/` | Directory containing the shaded jar. |
| `--data-dir` | `Path` | auto-generated | Output data directory. With `--run-id`, defaults to `data/data-<run-id>`. |
| `--results-dir` | `Path` | auto-generated | Output results directory. With `--run-id`, defaults to `data/results-<run-id>`. |
| **Run identity** | | | |
| `--run-id` | `str` | auto (timestamp) | Run identifier. When resuming, point to an existing run ID to continue from where it left off. |
| `--seed` | `int` | — | Explicit seed override. When used with `--run-id`, sets the base seed for the resumed run. |
| **Notifications** | | | |
| `--notify` | flag | auto | Force Telegram progress notifications on. Without it, notifications are enabled automatically when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` resolve (environment or `.env` file). |
| `--no-notify` | flag | — | Disable Telegram progress notifications for this run. |
| **Miscellaneous** | | | |
| `--java-mem` | `str` | `-Xms1g -Xmx8g -Xss8m` | JVM memory and stack options. |
| `--dry-run` | flag | `False` | Print the effective configuration and exit without running any experiments. |
| `n_trials_pos` | `int` | — | **[DEPRECATED]** Positional argument for backward compatibility. Use `-n` instead. |

The runner uses the bundled jar in `lib/` and passes the same repair flags used in the paper:
`--ontology`, `--seed`, `--run-id`, `--removal-timeout-secs`, `--weakening-timeout-secs`, `--power-index-timeout-secs`, `--make-inconsistent-timeout-secs`.

**Examples:**
```bash
python cli/run_trials.py
python cli/run_trials.py --dry-run
python cli/run_trials.py -n 50
python cli/run_trials.py -n 25 --base-seed 42 --step 50
python cli/run_trials.py --removal-timeout 600 --weakening-timeout 600
python cli/run_trials.py --run-id experiment-01
python cli/run_trials.py --run-id experiment-01 --seed 13
```

#### Telegram progress notifications

The runner can report progress to a Telegram chat. Messages are sent at
milestones only — run start, each ontology start/completion, aborts, run end
and crashes/interrupts — never once per trial. Sends are best-effort: a missing
token or an unreachable network can never abort or slow down an experiment.

Set up credentials in a git-ignored `.env` file at the replication package root
(copy `.env.example`):

```dotenv
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=123456789
```

Values in `.env` override variables already present in the process environment.
Credentials are optional — when they are absent the runner runs normally and
stays quiet. Use `--notify` to require notifications and `--no-notify` to
suppress them even when credentials are present.

```bash
# Find your chat ID (send any message to the bot first)
python -m cli.telegram_notify chat-id

# Verify the token and chat ID end-to-end
python -m cli.telegram_notify test

# Run the experiment with notifications
python cli/run_trials.py -n 50

# Force off for a quiet run
python cli/run_trials.py -n 50 --no-notify
```

### `run_trials_parallel.py`

Runs the *same* experiment as `run_trials.py`, but keeps **K** Java trials in flight for
one ontology instead of running them one at a time. The sequential runner remains the
reference implementation; this runner only changes the scheduling.

It writes the same per-trial CSVs and logs under `data/data-<run-id>/` and the same
aggregates under `data/results-<run-id>/`, so `analyze_results.py` and `estimator.py`
work unchanged. Rows appear in *completion* order rather than attempt order — sort by
the `trial_number` column. The consecutive-failure abort is evaluated in completion
order too, so the exact abort point can differ.

| Flag / Arg | Type | Default | Description |
|---|---|---|---|
| **Parallelism** | | | |
| `-K`, `--parallel-trials` | `int` | `1` | Trials kept in flight for one ontology. `1` behaves exactly like the sequential runner. |
| `-t`, `--threads-per-trial` | `int` | `16` | Sampling threads per trial JVM, passed as `-Dontologyutils.samplingThreads`. |
| `--cap-jvm-cpus` / `--no-cap-jvm-cpus` | flag | `--cap-jvm-cpus` | Also pass `-XX:ActiveProcessorCount=<t>` so each JVM sizes its own thread pools (GC, ForkJoin) for its share of the CPU budget instead of the whole machine. |
| `--ontologies` | `str` | all | Comma-separated ontology names to run (e.g. `--ontologies OGR,XEO`). |
| **Trial control** | | | |
| `-n`, `--n-trials` | `int` | `100` | Target number of successful trials per ontology. |
| `--base-seed` | `int` | `13` | Base random seed. |
| `--step` | `int` | `100` | Seed step between attempts. |
| `--analysis-interval` | `int` | `1` | Run the interim analysis after every K completed ontologies; `0` disables interim analysis (the final analysis always runs). |
| **Timeouts** | | | |
| `--removal-timeout` | `int` | `300` | Removal repair timeout in seconds. |
| `--weakening-timeout` | `int` | `300` | Weakening repair timeout in seconds. |
| `--power-index-timeout` | `int` | `300` | Power index computation timeout in seconds. |
| `--make-inconsistent-timeout` | `int` | `300` | Make-inconsistent timeout in seconds. |
| `--consecutive-failure-limit` | `int` | `10` | Abort an ontology after this many consecutive failures. |
| **Paths** | | | |
| `--inconsistent-dir` | `Path` | `ontologies/inconsistent/` | Directory containing inconsistent ontologies. |
| `--lib-dir` | `Path` | `lib/` | Directory containing the shaded jar. |
| `--data-dir` / `--results-dir` | `Path` | auto | Output directories. With `--run-id`, default to `data/data-<run-id>` / `data/results-<run-id>`. |
| `--java-mem` | `str` | `-Xms1g -Xmx64g -Xss8m` | JVM memory/stack options; the sampling-thread property is appended automatically. |
| **Run identity** | | | |
| `--run-id` | `str` | auto (timestamp) | Run identifier. Point it at an existing run ID to resume. |
| `--seed` | `int` | — | Explicit base-seed override. |
| **Notifications** | | | |
| `--notify` / `--no-notify` | flag | auto | Same semantics as `run_trials.py`. |
| **Miscellaneous** | | | |
| `--dry-run` | flag | `False` | Print the effective configuration (including `K`, `t` and the total thread count) and exit. |

#### Choosing `K` and `t`

Each trial JVM already spawns `t` sampling threads, so keep `K * t` within the machine's
CPU budget (leave headroom for the JVM's main thread, GC and JIT threads). Measured on a
24-core container with 63 GB RAM (`--cap-jvm-cpus`, no interim analysis):

| config | OGR | ELIG | XEO |
|---|---|---|---|
| `K=1, t=16` (sequential default) | 1.00x | 1.00x | 1.00x |
| `K=1, t=4` | 0.80x | — | — |
| `K=2, t=8` | 1.48x | 1.45x | 1.34x |
| **`K=4, t=4`** | **2.17x** | **1.71x** | **2.06x** |
| `K=8, t=2` | 2.32x | **0.80x** | — |

Speed-up in trials/second over `K=1, t=16`.

* **`K=4, t=4` is the recommended configuration** — 1.7-2.2x on every ontology measured.
* Lowering `t` alone is a loss (`K=1, t=4` is *slower* than `K=1, t=16`); the gain comes
  from overlapping the serial part of several trials.
* A large `K` with a small `t` can be *worse* than sequential: `K=8, t=2` collapsed to
  0.80x on ELIG because eight JVMs' main/GC/JIT threads exceed the core budget. Measure
  before raising `K`.

#### Two caveats

* **Changing `t` changes the results, not just the speed.** The 735 samples are split
  across the workers, so a different `t` samples different coalitions and yields
  different approximate values. Only compare IIC values between runs that used the same
  `t`.
* **Per-trial IIC values are not reproducible even sequentially.** Two identical
  `run_trials.py` runs with the same seeds already differ (e.g. 21 of 27 values for one
  HOM trial). The cause is that `Utils` keeps its RNG in a `ThreadLocal` and
  `Utils.randomSeed()` seeds only the calling thread, so random draws made on other
  threads (parallel streams, power-index base seeds) are unseeded. Attempt numbers,
  seeds, trial statuses and row counts *are* reproducible. See
  `plans/parallel-trials-plan.md` for details; treat only aggregates as meaningful.

**Examples:**
```bash
# Sanity check the effective configuration
python cli/run_trials_parallel.py -K 4 -t 4 --dry-run

# Recommended configuration
python cli/run_trials_parallel.py -K 4 -t 4

# Same, but only two ontologies and 10 trials each (quick calibration)
python cli/run_trials_parallel.py -K 4 -t 4 -n 10 --ontologies OGR,XEO

# Reproduce the sequential runner exactly
python cli/run_trials_parallel.py -K 1 -t 16
```

### `analyze_results.py`

Aggregate trial outputs from a completed experiment run into CSV, Markdown, and LaTeX report files. Auto-detects the latest `data/data-*` directory if no argument is given.

| Arg | Type | Default | Description |
|---|---|---|---|
| `data_dir` (positional, optional) | `Path` | latest `data/data-*` | Path to the data directory containing per-trial CSV files. |
| `out_dir` (positional, optional) | `Path` | `data/results-<stamp>/` | Output directory for the generated reports. |

The aggregated outputs are written to `data/results-<stamp>/`.

**Examples:**
```bash
python cli/analyze_results.py
python cli/analyze_results.py data/data-20250301
python cli/analyze_results.py data/data-20250301 my_results/
```

### `estimator.py`

Run the complexity estimation for the repair algorithm. Uses hardcoded data points from the paper experiments (ignoring `bctt` and `co-wheat` as outliers) and fits both polynomial (`O(N^k)`) and exponential (`O(b^N)`) models, reporting the R² values to show which model fits better.

This app takes **no arguments**.

**Example:**
```bash
python cli/estimator.py
```

## Module vs CLI Architecture

The code is organized following a model/view separation:

- **`analysis/` (Model)**: Core computation modules — reusable, importable, testable.
- **`cli/` (View)**: Thin CLI entry points — parse arguments and delegate to model functions.

For example, `analysis/analyzer.py` provides `run_analysis()` which is imported by both
`cli/analyze_results.py` (standalone CLI) and `analysis/orchestrator.py` (experiment runner).
