# CLI Reference

Every command-line entry point of the replication package lives in `cli/`. They
are thin views over the modules in `analysis/`, so anything the CLIs expose is
also reachable as a library call.

Run them from the package root with the virtual environment, either as a module
or as a script:

```bash
.venv/bin/python -m cli.run_trials_parallel --help
.venv/bin/python cli/run_trials_parallel.py --help
```

`cli/analyze_results.py` and `cli/estimator.py` take positional arguments;
everything else uses `argparse` and supports `--help`.

| CLI | Purpose |
|---|---|
| `run_trials.py` | run one ontology at a time, sequentially |
| `run_trials_parallel.py` | run the corpus with several trials in flight |
| `run_trials_parallel_ablation.py` | same, over the whole `B1`-`B9` arm grid |
| `analyze_results.py` | aggregate the trials into the report files |
| `estimator.py` | fit the runtime-complexity model of an older run |
| `preprocess_ontologies.py` | cleanup and classification of a raw corpus |
| `classify_ontologies.py` | classify a folder of ontologies into ALC / non-ALC |
| `classify_original_corpus.py` | classify raw originals into a JSON manifest |
| `build_nocleanup_corpus.py` | build the corpus without the cleanup stage |
| `telegram_notify.py` | test and drive the progress notifier |

---

## Trial runners

### `cli/run_trials.py`

Runs the ontologies one after another. Each ontology is retried until it holds
`-n` successful trials; failures are recorded in the outcome table and the seed
advances.

| Option | Default | Meaning |
|---|---|---|
| `-n`, `--n-trials` | 100 | successful trials required per ontology |
| `--run-id` | timestamp | name of the run, used for the output directories |
| `--inconsistent-dir` | package corpus | directory holding the inconsistent ontologies |
| `--data-dir`, `--results-dir` | derived from `--run-id` | where the trial CSVs and the reports go |
| `--lib-dir` | `lib/` | directory holding the shaded jar |
| `--base-seed` | 13 | seed of the first attempt |
| `--step` | 100 | seed increment between attempts of the same ontology |
| `--seed` | — | overrides `--base-seed`, and on resume restores the base seed of the run |
| `--analysis-interval` | 1 | run the analysis every *n* completed ontologies; `0` disables the interim runs |
| `--removal-timeout`, `--weakening-timeout`, `--power-index-timeout`, `--make-inconsistent-timeout` | 300 | per-stage timeouts, in seconds |
| `--consecutive-failure-limit` | 10 | abort an ontology after this many failures in a row |
| `--java-mem` | `-Xms1g -Xmx64g -Xss8m` | heap and stack passed to the JVM |
| `--notify` / `--no-notify` | auto | send Telegram progress messages (see `telegram_notify.py`) |
| `--dry-run` | off | print the plan without running any trial |

### `cli/run_trials_parallel.py`

Same experiment, but several trials run concurrently. It accepts every option of
`run_trials.py` plus:

| Option | Default | Meaning |
|---|---|---|
| `-K`, `--parallel-trials` | 1 | trials in flight |
| `-t`, `--threads-per-trial` | 16 | sampling threads inside each trial |
| `--cap-jvm-cpus` | off | pin each JVM to its share of the CPUs |
| `--ontologies` | all | restrict the run to the named ontologies |

Sizing advice: `-K` times `--java-mem` must stay well below the memory of the
machine, and `-K` against `-t` matters more than either alone. `-K 3 -t 8` is a
safe default on a 24-core machine; large `-K` with a large `-t` oversubscribes
the CPUs and slows the run down.

The run is resumable: passing the same `--run-id` skips the ontologies that
already hold `-n` successful trials.

### `cli/run_trials_parallel_ablation.py`

Takes no options of its own; it forwards to `run_trials_parallel.py` after
selecting the ablation experiment, which runs the three baselines and the nine
indexed arms `B1`-`B9` in one trial. Use it to explore the grid; the paper's
numbers come from `run_trials_parallel.py`, which runs the power-index arm alone.

### Seeds

A trial's seed is derived from the attempt, not chosen freely:

```
seed = base_seed + ontology_index + attempt * step * ontology_count
```

The two arms then take `seed + 1 + their position in the plan list`, which is
inside the Java experiment. A consequence worth knowing: the same arm receives a
different seed depending on how many arms run before it, so per-trial values from
different experiment compositions are not comparable. Compare aggregates.

### Outputs

```
data/data-<run-id>/
    iic-<ontology>.csv        one row per trial: information content against each baseline
    runtime-<ontology>.csv    one row per trial: outcome, seed and the timing of each repair
    run_trials-<ontology>.log one block per attempt, headed by its seed
data/results-<run-id>/        written by the analysis
```

The run id is a free-form label; nothing parses it. It names the two directories,
tags every CSV row, is passed to the Java experiment and appears in the
notifications. The examples use `<experiment>-K<K>t<t>` so that the label records
how the run was launched, for instance `shapley-K3t8` for three trials in flight
with eight sampling threads each.

---

## Analysis

### When the analysis runs

The trial runners call the analysis themselves; you do not have to run it to get
results. The orchestrators invoke it:

- **after every `--analysis-interval` completed ontologies** (default `1`, so
after each one), and
- **once at the end of the run**, unconditionally, whose output is also reported
to the notifier.

An interim failure is printed as a warning and does not stop the run, so a run
that aborts early still leaves the tables of the ontologies it finished. Passing
`--analysis-interval 0` disables the interim runs and keeps only the final one.
The analysis reads the CSVs only, so it can always be re-run by hand later.

### Two reports, one per experiment

| Runner | Java experiment | Report | Output directory |
|---|---|---|---|
| `run_trials.py`, `run_trials_parallel.py` | `SingleTrialExperiment` — the paper's Shapley + Shapley arm, with A1/A2/A3 | one power-index column against each baseline, as published | `data/results-<run-id>/` |
| `run_trials_parallel_ablation.py` | `SingleTrialAblationExperiment` — A1/A2/A3 plus the nine arms B1-B9 | a matrix of arm against baseline for every arm | `data/results-<run-id>-ablation/` |

The report is chosen automatically from `ONTOLOGYUTILS_EXPERIMENT_CLASS`, and the
ablation appends `-ablation` to the results directory so that the two outputs can
never overwrite each other.

### `cli/analyze_results.py`

```bash
.venv/bin/python -m cli.analyze_results [data_dir] [out_dir]
```

With no arguments it takes the newest `data/data-*` directory and writes to the
matching `data/results-*`. It reads the CSVs only, so the report can be rebuilt
without rerunning a trial.

| File | Content |
|---|---|
| `iic_summary.csv` | `ontology, comparison, mean, sd, n, ci_low, ci_high, reject_h0` |
| `runtime_summary.csv` | `ontology, repair_method, mean_ms, sd_ms, median_ms, min_ms, max_ms, n, ci_low_ms, ci_high_ms` |
| `outcome_summary.csv` | success, timeout and memory-limit counts and rates |
| `combined_report.md` | the three tables, one section per baseline |
| `combined_report.tex` | the same tables as `booktabs` tables |

The comparisons are named after the baseline (`iic_power_vs_random`,
`iic_power_vs_not_in_largest_mcs`, `iic_power_vs_weakening`) and the repair
methods are `random_removal_ms`, `not_in_largest_mcs_removal_ms`, `weakening_ms`
and `power_index_ms`. `reject_h0` applies the paper's decision rule: reject when
the lower bound of the 95 % confidence interval is greater than 0.5.

The reported arm is `B5`, the paper's Shapley + Shapley arm. Setting
`ONTOLOGYUTILS_B_REPAIRS` to a single arm name reports that arm instead.

When the command is run while `ONTOLOGYUTILS_EXPERIMENT_CLASS` names the ablation
experiment, the same command produces the ablation report instead: one
`iic_summary.csv` row per arm and baseline (`b_repair`, `a_repair`), a runtime row
per repair id, and an extra `per_repair_outcome_summary.csv`. Those files are
written to `<out_dir>-ablation`.

### `cli/estimator.py`

```bash
.venv/bin/python -m cli.estimator --run-id <run> [--data-dir DIR] [--inconsistent-dir DIR] [--legacy]
```

Fits a polynomial and an exponential model to the runtime of an older run and
prints the better fit. It needs the axiom counts of the corpus, and it is
independent of `analyze_results.py`, which no longer estimates complexity.

---

## Corpus preparation

These CLIs are only needed to rebuild a corpus; the package ships its ontologies
already prepared.

| CLI | What it does |
|---|---|
| `preprocess_ontologies.py` | cleanup, ALC filtering and classification, in parallel (`-j`, `-v`, `--force`, `--dry-run`, `--start-stage`, `--java-mem`, `--timeout`, `--no-progress`) |
| `classify_ontologies.py` | classify a folder and print a Markdown table (`-o`, `-j`, `-v`, `--sort-by-axioms`, `--no-progress`, `--java-mem`, `--timeout`) |
| `classify_original_corpus.py` | classify raw originals into a JSON manifest (`--ontologies-dir`, `-o`, `-j`, `--checkpoint-every`, `--force`, `--java-mem`, `--big-java-mem`, `--big-threshold-mb`, `--timeout`) |
| `build_nocleanup_corpus.py` | build the corpus without the cleanup stage (`--manifest`, `--source-dir`, `--alc-dir`, `--inconsistent-dir`, `--report`, `--only-names`, `--min-axioms`, `--max-mb`, `-j`, `--timeout`, `--force`, `--dry-run`, `--include-truncated`, `--skip-verify`, `--java-mem`, `--big-java-mem`, `--big-threshold-mb`) |

All of them are resumable and skip existing outputs unless `--force` is given.

---

## Utilities

### `cli/telegram_notify.py`

```bash
.venv/bin/python -m cli.telegram_notify chat-id                  # list the chat ids of recent bot updates
.venv/bin/python -m cli.telegram_notify test                     # send the default test message
.venv/bin/python -m cli.telegram_notify test --message "hello"   # send a specific message
```

`chat-id` calls `getUpdates`, so send the bot any message in Telegram first or the
list is empty. Both subcommands accept `--dry-run`, which prints the request
instead of making it, and the top level accepts `--env-file` to read the
credentials from another file.

Credentials come from `.env` at the package root (`TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`), which is git-ignored; `.env.example` documents the keys.
Note that `.env` overrides real environment variables.

---

## Environment variables and system properties

| Name | Effect |
|---|---|
| `ONTOLOGYUTILS_EXPERIMENT_CLASS` | Java class driving a trial, and the switch that picks the report. Default `www.ontologyutils.apps.SingleTrialExperiment` (the paper's arm); the ablation runner sets the nine-arm experiment |
| `ONTOLOGYUTILS_B_REPAIRS` | arms the ablation report covers (default `B1`-`B9`); a single arm name picks the arm reported as the power index in the paper report |
| `-Dontologyutils.samplingThreads` | sampling threads per strategy call inside the JVM (default 16) |
| `-Dontologyutils.ignoreImports` | load ontologies without resolving `owl:imports`; needed for raw corpora |

---

## Typical invocations

Reproduce the paper's experiment:

```bash
.venv/bin/python -m cli.run_trials_parallel \
    --inconsistent-dir ontologies.publication --run-id shapley-K3t8 \
    -n 100 -K 3 -t 8 \
    --removal-timeout 600 --weakening-timeout 600 \
    --power-index-timeout 600 --make-inconsistent-timeout 600
.venv/bin/python -m cli.analyze_results data/data-shapley-K3t8 data/results-shapley-K3t8
```

Rebuild the report of a finished run without touching the trials:

```bash
.venv/bin/python -m cli.analyze_results data/data-shapley-K3t8 /tmp/report
```

Explore the arm grid on a subset of the corpus:

```bash
.venv/bin/python -m cli.run_trials_parallel_ablation \
    --ontologies bctt,elig --inconsistent-dir ontologies.publication \
    --run-id grid-probe -n 10 -K 2 -t 8
```
