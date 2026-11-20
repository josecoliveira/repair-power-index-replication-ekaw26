# CLI Reference

Every command-line entry point of the replication package lives in `cli/`. They are thin
views over the modules in `analysis/`, so anything the CLIs expose is also reachable as a
library call.

The reference has three parts:

1. **The paper's replication** — what the package is for, and the only part the README
   points at.
2. **The nine-arm ablation grid** — work in progress, not part of the paper's results.
3. **Development tools** — the corpus pipeline, the estimator and the notifier.

Run the commands from the package root with the virtual environment, either as a module or
as a script:

```bash
.venv/bin/python -m cli.run_trials --help
.venv/bin/python cli/run_trials.py --help
```

`cli/analyze_results.py`, `cli/analyze_results_ablation.py` and `cli/estimator.py` take
positional arguments; everything else uses `argparse` and supports `--help`.

| CLI | Part | Purpose |
|---|---|---|
| `run_trials.py` | paper | run the corpus, up to `-K` trials in flight |
| `analyze_results.py` | paper | aggregate a paper run into the report files |
| `run_trials_ablation.py` | ablation (WIP) | run the whole `B1`-`B9` arm grid |
| `analyze_results_ablation.py` | ablation (WIP) | aggregate an ablation run into the per-arm report files |
| `estimator.py` | development | fit the runtime-complexity model of an older run |
| `preprocess_ontologies.py` | development | cleanup and classification of a raw corpus |
| `classify_ontologies.py` | development | classify a folder of ontologies into ALC / non-ALC |
| `classify_original_corpus.py` | development | classify raw originals into a JSON manifest |
| `build_nocleanup_corpus.py` | development | build the corpus without the cleanup stage |
| `telegram_notify.py` | development | test and drive the progress notifier |

---

# Part 1 — The paper's replication

## `cli/run_trials.py`

Runs `SingleTrialExperiment` on every ontology of the corpus: up to `-K` trials in flight
for the same ontology, each with `-t` sampling threads, until the ontology holds `-n`
successful trials. Failures are recorded in the outcome table and the seed advances. It is
the only trial runner of the paper's experiment.

| Option | Default | Meaning |
|---|---|---|
| `-n`, `--n-trials` | 100 | successful trials required per ontology |
| `-K`, `--parallel-trials` | 1 | trials in flight for one ontology |
| `-t`, `--threads-per-trial` | 8 | sampling threads inside each trial |
| `--cap-jvm-cpus` | on | also pass `-XX:ActiveProcessorCount=<t>` to each JVM |
| `--ontologies` | all | restrict the run to the named ontologies |
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

Sizing advice: `-K` times `--java-mem` must stay well below the memory of the machine, and
`-K` against `-t` matters more than either alone. One trial in flight with eight sampling
threads is a safe choice on a single desktop; more trials in flight only pay off on many
cores, and a large `-K` with a large `-t` oversubscribes the CPUs and slows the run down.

Resuming: passing the same `--run-id` skips the ontologies that already hold `-n`
successful trials. With `-K 1` the trials run one at a time and the rows appear in attempt
order; with `-K > 1` they appear in completion order. The `trial_number` column identifies
each attempt either way, so the report is unaffected.

### Seeds

A trial's seed is derived from the attempt, not chosen freely:

```
seed = base_seed + ontology_index + attempt * step * ontology_count
```

It depends on the corpus and on the `--ontologies` selection, so two runs are comparable
when they use the same selection — which is what the report relies on. Inside the Java
experiment the Shapley arm then takes `seed + 1 + its position in the plan list`.

### Outputs

```
data/data-<run-id>/
    iic-<ontology>.csv          one row per trial: information content against each baseline
    runtime-<ontology>.csv      one row per trial: outcome, seed and the timing of each repair
    run_trials-<ontology>.log   one block per attempt, headed by its seed
data/results-<run-id>/          written by the analysis
```

The IIC table has one column per baseline (`SHAPLEY_vs_A1`, `SHAPLEY_vs_A2`,
`SHAPLEY_vs_A3`) and the runtime table one column per repair (`A1_ms`, `A2_ms`, `A3_ms`,
`SHAPLEY_ms`).

The run id is a free-form label; nothing parses it. It names the two directories, tags
every CSV row, is passed to the Java experiment and appears in the notifications. When it
is omitted the run gets a timestamp, and that is the id to pass back with `--run-id` in
order to resume the run.

## When the analysis runs

The runner calls the analysis itself; you do not have to run it to get results. It invokes
it:

- **after every `--analysis-interval` completed ontologies** (default `1`, so after each
  one), and
- **once at the end of the run**, unconditionally, whose output is also reported to the
  notifier.

An interim failure is printed as a warning and does not stop the run, so a run that aborts
early still leaves the tables of the ontologies it finished. Passing `--analysis-interval 0`
disables the interim runs and keeps only the final one. The analysis reads the CSVs only,
so it can always be re-run by hand later.

## `cli/analyze_results.py`

```bash
.venv/bin/python -m cli.analyze_results [data_dir] [out_dir]
```

With no arguments it takes the newest `data/data-*` directory and writes to the matching
`data/results-*`. It reads the CSVs only, so the report can be rebuilt without rerunning a
trial.

| File | Content |
|---|---|
| `iic_summary.csv` | `ontology, comparison, mean, sd, n, ci_low, ci_high, reject_h0` |
| `runtime_summary.csv` | `ontology, repair_method, mean_ms, sd_ms, median_ms, min_ms, max_ms, n, ci_low_ms, ci_high_ms` |
| `outcome_summary.csv` | success, timeout and memory-limit counts and rates |
| `combined_report.md` | the three tables, one section per baseline |
| `combined_report.tex` | the same tables as `booktabs` tables |

The comparisons are named after the baseline (`iic_power_vs_random`,
`iic_power_vs_not_in_largest_mcs`, `iic_power_vs_weakening`) and the repair methods are
`random_removal_ms`, `not_in_largest_mcs_removal_ms`, `weakening_ms` and `power_index_ms`.
`reject_h0` applies the paper's decision rule: reject when the lower bound of the 95 %
confidence interval is greater than 0.5.

The reported arm is `SHAPLEY`, the paper's Shapley + Shapley repair.

---

# Part 2 — Ablation grid (work in progress)

The ablation is not part of the paper's replication. Its arms, its report and the shape of
its output may still change, and its numbers must not be presented as the paper's. It is
documented here because it lives in the same package, not because it is part of the
replication the README describes.

One trial runs twelve repairs: the three baselines plus nine arms that combine a bad-axiom
strategy with a weaker-axiom strategy.

| Arm | Bad axiom | Weaker axiom |
|---|---|---|
| `B1` | in-some-MUS | random |
| `B2` | in-some-MUS | Shapley |
| `B3` | in-some-MUS | Banzhaf |
| `B4` | Shapley | random |
| `B5` | Shapley | Shapley |
| `B6` | Shapley | Banzhaf |
| `B7` | Banzhaf | random |
| `B8` | Banzhaf | Shapley |
| `B9` | Banzhaf | Banzhaf |

## `cli/run_trials_ablation.py`

```bash
.venv/bin/python -m cli.run_trials_ablation \
    --ontologies bctt,elig --inconsistent-dir ontologies/inconsistent \
    -n 10 -K 1 -t 8
```

Accepts every option of `run_trials.py`; the differences are that it drives
`SingleTrialAblationExperiment`, defaults to `-n 10`, prints a work-in-progress banner, and
writes its reports to `data/results-<run-id>-ablation/` so they can never overwrite the
paper's. Because one trial runs twelve repairs, the defaults are the exploration setting.

## `cli/analyze_results_ablation.py`

```bash
.venv/bin/python -m cli.analyze_results_ablation [data_dir] [out_dir]
```

The same positional arguments as `cli/analyze_results.py`, but it reports the whole
`B1`-`B9` grid: one `iic_summary.csv` row per arm and baseline (`b_repair`, `a_repair`), a
runtime row per repair id, and an extra `per_repair_outcome_summary.csv`. The files are
written to `<out_dir>-ablation`, the same directory the ablation runner reports to.
`ONTOLOGYUTILS_B_REPAIRS` narrows the report to a subset of arms.

## A caveat about the numbers

The seed of an arm inside a trial is `seed + 1 + its position in the plan list`, so `B5`
here starts from a different seed than the paper's `SHAPLEY` arm does. Per-trial values
from the two experiments are therefore not comparable; compare aggregates, and never quote
a grid number as the paper's.

---

# Part 3 — Development

## Module map

| Module | Responsibility |
|---|---|
| `analysis/runner.py` | the paper's trial runner: arms `A1`,`A2`,`A3`,`SHAPLEY`, its CSV/log layout, and its results directory |
| `analysis/analysis.py` | the paper's report |
| `analysis/ablation_runner.py` | the grid's trial runner: arms `A1`-`A3`,`B1`-`B9`, its own layout and the `-ablation` directory |
| `analysis/ablation_analysis.py` | the grid's report |
| `analysis/notifier.py` | Telegram messages; both runners call it |
| `analysis/ontologyutils_service.py` | launches one JVM and parses the JSON it prints |
| `analysis/estimator.py`, `analysis/preprocessor.py`, `analysis/classifier.py` | complexity fitting and corpus preparation |

The two experiments are independent on purpose: separate Java app, separate runner,
separate arm schema, separate report, separate results directory. Nothing dispatches
between them, and their modules never import each other — a bug fix in one runner has to be
applied to the other. The same rule holds for the analyses.

The dry run of each runner prints the pair it uses, for example
`EXPERIMENT_CLASS = www.ontologyutils.apps.SingleTrialExperiment` and
`ANALYSIS = analysis.analysis`.

## `cli/estimator.py`

```bash
.venv/bin/python -m cli.estimator --run-id <run> [--data-dir DIR] [--inconsistent-dir DIR] [--legacy]
```

Fits a polynomial and an exponential model to the runtime of an older run and prints the
better fit. It needs the axiom counts of the corpus, and it is independent of
`analyze_results.py`, which estimates nothing.

## Corpus preparation

These CLIs are only needed to rebuild a corpus; the package ships its ontologies already
prepared.

| CLI | What it does |
|---|---|
| `preprocess_ontologies.py` | cleanup, ALC filtering and classification, in parallel (`-j`, `-v`, `--force`, `--dry-run`, `--start-stage`, `--java-mem`, `--timeout`, `--no-progress`) |
| `classify_ontologies.py` | classify a folder and print a Markdown table (`-o`, `-j`, `-v`, `--sort-by-axioms`, `--no-progress`, `--java-mem`, `--timeout`) |
| `classify_original_corpus.py` | classify raw originals into a JSON manifest (`--ontologies-dir`, `-o`, `-j`, `--checkpoint-every`, `--force`, `--java-mem`, `--big-java-mem`, `--big-threshold-mb`, `--timeout`) |
| `build_nocleanup_corpus.py` | build the corpus without the cleanup stage (`--manifest`, `--source-dir`, `--alc-dir`, `--inconsistent-dir`, `--report`, `--only-names`, `--min-axioms`, `--max-mb`, `-j`, `--timeout`, `--force`, `--dry-run`, `--include-truncated`, `--skip-verify`, `--java-mem`, `--big-java-mem`, `--big-threshold-mb`) |

All of them are resumable and skip existing outputs unless `--force` is given.

## `cli/telegram_notify.py`

```bash
.venv/bin/python -m cli.telegram_notify chat-id                  # list the chat ids of recent bot updates
.venv/bin/python -m cli.telegram_notify test                     # send the default test message
.venv/bin/python -m cli.telegram_notify test --message "hello"   # send a specific message
```

`chat-id` calls `getUpdates`, so send the bot any message in Telegram first or the list is
empty. Both subcommands accept `--dry-run`, which prints the request instead of making it,
and the top level accepts `--env-file` to read the credentials from another file.

Credentials come from `.env` at the package root (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`),
which is git-ignored; `.env.example` documents the keys. Note that `.env` overrides real
environment variables.

## Environment variables and system properties

| Name | Effect |
|---|---|
| `ONTOLOGYUTILS_EXPERIMENT_CLASS` | Java class driving a trial. Each runner CLI pins it (`SingleTrialExperiment` or `SingleTrialAblationExperiment`) before starting, so a run always drives the experiment it says it does |
| `ONTOLOGYUTILS_B_REPAIRS` | arms the ablation report covers (default `B1`-`B9`); the paper's report always covers `SHAPLEY` |
| `-Dontologyutils.samplingThreads` | sampling threads per strategy call inside the JVM, set from `-t` (default 8) |
| `-Dontologyutils.ignoreImports` | load ontologies without resolving `owl:imports`; needed for raw corpora |

## Typical invocations

Reproduce the paper's experiment:

```bash
.venv/bin/python -m cli.run_trials \
    --inconsistent-dir ontologies/inconsistent -n 100 -K 1 -t 8 \
    --removal-timeout 600 --weakening-timeout 600 \
    --power-index-timeout 600 --make-inconsistent-timeout 600
.venv/bin/python -m cli.analyze_results
```

Rebuild the report of a finished run without touching the trials:

```bash
.venv/bin/python -m cli.analyze_results data/data-<run-id> /tmp/report
```

Rebuild the grid report of a finished ablation run:

```bash
.venv/bin/python -m cli.analyze_results_ablation data/data-<run-id> /tmp/report
```
