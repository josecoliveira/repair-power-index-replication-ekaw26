# Power Index Replication Package

This repository reproduces the power-index experiment of the paper *Guiding
Ontology Repair with Axiom Weakening and Shapley Values* (EKAW 2026, LNAI 16897,
pp. 208-223).

The experiment compares one repair configuration against three baselines on the
paper's corpus of eight inconsistent ontologies, 100 trials each. The
configuration is **Shapley + Shapley**: the bad axiom is chosen by its Shapley
value in the inconsistency game, and the replacement is the weakening with the
lowest Shapley value in the game that its own replacement produces.

| Arm | Repair |
|---|---|
| `A1` | random removal |
| `A2` | not-in-largest-MCS removal |
| `A3` | default weakening (in-MUS bad axiom + random weakening) |
| `SHAPLEY` | Shapley bad axiom + Shapley weakening |

## Contents

- `lib/shaded-ontologyutils-0.1.0.jar` — the Java implementation, already built.
- `ontologies/` — the corpus of the paper: `cleanup/` and `original/` hold the
  inputs, and `inconsistent/` the eight ontologies the trials run on.
- `cli/run_trials.py` — the trial runner.
- `cli/analyze_results.py` — aggregates the trials into the published tables.
- `analysis/` — the runner (`runner.py`) and the analysis (`analysis.py`) behind
  those two commands.

`DOCUMENTATION.md` has the full reference, including the development tools and a
nine-arm ablation that is still work in progress.

## Requirements

- Java 17
- Python 3.10+
- `pip`

```bash
python -m venv .venv
.venv/bin/pip install -r analysis/requirements.txt
```

No Maven build is needed: the trials run against the jar in `lib/`.

## Run The Trials

```bash
.venv/bin/python -m cli.run_trials \
    --inconsistent-dir ontologies/inconsistent -n 100 -K 1 -t 8 \
    --java-mem "-Xms2g -Xmx6g -Xss8m" \
    --removal-timeout 600 --weakening-timeout 600 \
    --power-index-timeout 600 --make-inconsistent-timeout 600
```

`-n` is the number of successful trials per ontology, `-K` the number of trials
in flight for one ontology and `-t` the number of sampling threads inside each
trial. `-K 1 -t 8` is the default and a safe choice on a single desktop; raise
`-K` only when the machine has cores to spare, keeping `-K` times `--java-mem`
well below its memory. A run can be resumed by passing the same `--run-id`:
ontologies that already hold `-n` successful trials are skipped.

The trials write to `data/data-<run-id>/`, one pair of files per ontology:

- `iic-<ontology>.csv` — one row per trial, with the information content of the
  Shapley repair relative to each baseline;
- `runtime-<ontology>.csv` — the outcome and the timing of each repair.

Both cover the three baselines as well, so a single run yields every number the
paper reports.

## Aggregate The Results

```bash
.venv/bin/python -m cli.analyze_results
```

With no arguments it takes the newest `data/data-*` directory and writes next to
it, so the reports land in `data/results-<run-id>/`:

| File | Content |
|---|---|
| `iic_summary.csv` | mean, standard deviation, 95 % confidence interval and the decision of the test, for each ontology and each baseline |
| `runtime_summary.csv` | mean, median, range and confidence interval of the runtime of each repair method |
| `outcome_summary.csv` | success, timeout and memory-limit rates |
| `combined_report.md`, `combined_report.tex` | the three tables, ready for Markdown or LaTeX |

The decision rule is the one of the paper: the null hypothesis that the power
index does not improve on a baseline is rejected when the lower bound of the
95 % confidence interval of the mean improvement is greater than 0.5.

## Reading The Numbers

The report is organised by baseline, not by arm: `vs random`, `vs
non_in_largest_mcs` and `vs weakening` are the three comparisons of the paper's
Table 2. The `reject_h0` column is the outcome of the test described above.

Note that a trial's seed is derived from the trial index, so results are only
comparable within the same corpus and the same `--ontologies` selection. Compare
aggregates, which is what the report does.

## Reproduction Notes

- Every trial runs the Java experiment in its own JVM; a run of the whole corpus
  at 100 trials per ontology takes several hours.
- A trial that exceeds a timeout is retried with a fresh seed and is counted in
  the outcome table, so `outcome_summary.csv` is part of the result.
- The reports are regenerated from the CSVs alone, so they can be rebuilt
  without rerunning any trial.
