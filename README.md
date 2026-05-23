# Power Index Replication Package

This repository is self-contained for reproducing the power-index experiments from the paper.

It includes:

- `lib/shaded-ontologyutils-0.1.0.jar`
- `inconsistent/*.owl`
- `analysis/run_trials.py`
- `analysis/analyze_results.py`
- `analysis/estimator.py`

## Requirements

- Java 17
- Python 3.10+
- `pip`

Install the Python dependencies from the repository root:

```bash
python -m pip install -r analysis/requirements.txt
```

No Maven build is required for the replication scripts because they run against the bundled shaded jar.

## Run The Trials

Run a batch of trials against one of the included ontologies. The runner writes the per-trial CSVs and logs under `analysis/data/shapley-shapley/`.

```bash
python analysis/run_trials.py inconsistent/taxrank.owl
```

The optional second argument sets the number of successful trials to collect:

```bash
python analysis/run_trials.py inconsistent/taxrank.owl 25
```

The runner uses the bundled jar in `lib/` and passes the same repair flags used in the paper:

- `--ontology`
- `--seed`
- `--run-id`
- `--removal-timeout-secs`
- `--weakening-timeout-secs`
- `--power-index-timeout-secs`
- `--make-inconsistent-timeout-secs`

## Aggregate Results

After the trial CSVs are present, generate the summary tables and the combined report:

```bash
python analysis/analyze_results.py
```

The aggregated outputs are written to `analysis/output/shapley-shapley/`.
