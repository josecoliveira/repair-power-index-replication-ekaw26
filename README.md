# Power Index Replication Package

This repository is self-contained for reproducing the power-index experiments from the paper.

It includes:

- `lib/shaded-ontologyutils-0.1.0.jar`
- `ontologies/inconsistent/*.owl`
- `analysis/` — model layer: reusable computation modules
  - `orchestrator.py` — experiment orchestration
  - `analyzer.py` — analysis and report generation
  - `preprocessor.py` — ontology preprocessing pipeline
  - `classifier.py` — ontology classification
- `cli/` — view layer: thin CLI entry points
  - `run_trials.py` — CLI for the experiment runner
  - `analyze_results.py` — CLI for the analysis module
  - `preprocess_ontologies.py` — CLI for the preprocessing pipeline
  - `classify_ontologies.py` — CLI for ontology classification
  - `estimator.py` — CLI for complexity estimation

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
| `--analysis-interval` | `int` | `1` | Run analysis every K rounds; `0` to disable. |
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
