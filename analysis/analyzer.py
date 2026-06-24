#!/usr/bin/env python3
"""Aggregate trial outputs into CSV, Markdown, and LaTeX reports."""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy import stats

from analysis.estimator import (
    build_estimation_latex,
    build_estimation_markdown,
    estimate_complexity,
    load_axiom_counts,
)

A_REPAIRS = ["A1", "A2", "A3"]
B_REPAIRS = [f"B{i}" for i in range(2, 10)]
REPAIR_IDS = A_REPAIRS + B_REPAIRS
IIC_KEYS = [f"{b}_vs_{a}" for b in B_REPAIRS for a in A_REPAIRS]
OUTCOME_ORDER = ["success", "time_limit_exceeded", "memory_limit_exceeded"]
CSV_NAME_RE = re.compile(r"^(?P<kind>iic|runtime)-(?P<ontology>.+)\.csv$")

REPAIR_LABELS = {
    "A1": "Random removal",
    "A2": "Not-in-largest-MCS removal",
    "A3": "Default weakening (in-MUS + random)",
    "B2": "in-MUS + Shapley",
    "B3": "in-MUS + Banzhaf",
    "B4": "Shapley + random",
    "B5": "Shapley + Shapley",
    "B6": "Shapley + Banzhaf",
    "B7": "Banzhaf + random",
    "B8": "Banzhaf + Shapley",
    "B9": "Banzhaf + Banzhaf",
}


def discover_csvs(data_dir: Path, kind: str) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(data_dir.glob(f"{kind}-*.csv")):
        match = CSV_NAME_RE.match(path.name)
        if match and match.group("kind") == kind:
            paths.append(path)
    return paths


def mean_n_ci(values: np.ndarray, conf: float = 0.95) -> tuple[float, int, float, float]:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    n = int(arr.size)
    if n == 0:
        return float("nan"), 0, float("nan"), float("nan")
    mean = float(np.mean(arr))
    if n == 1:
        return mean, 1, mean, mean
    sd = float(np.std(arr, ddof=1))
    se = sd / math.sqrt(n)
    alpha = 1.0 - conf
    tcrit = stats.t.ppf(1.0 - alpha / 2.0, n - 1)
    ci_low = mean - tcrit * se
    ci_high = mean + tcrit * se
    return mean, n, ci_low, ci_high


def latex_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", r"\textbackslash{}")
        .replace("_", r"\_")
        .replace("%", r"\%")
        .replace("&", r"\&")
        .replace("#", r"\#")
        .replace("$", r"\$")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def percent(value: float) -> str:
    return f"{value:.1%}"


def iic_cell(mean: float, ci_low: float, ci_high: float, n: int) -> str:
    if n == 0 or math.isnan(mean):
        return "-"
    return f"{mean:.4f} [{ci_low:.4f}; {ci_high:.4f}]"


def runtime_cell(mean_ms: float, n: int) -> str:
    if n == 0 or math.isnan(mean_ms):
        return "-"
    return f"{mean_ms:.2f} ms"


def outcome_cell(count: int, rate: float) -> str:
    return f"{count} ({percent(rate)})"


def load_iic_rows(paths: Iterable[Path]) -> pd.DataFrame:
    rows: list[dict] = []
    for path in paths:
        match = CSV_NAME_RE.match(path.name)
        if not match:
            continue
        ontology = match.group("ontology")
        frame = pd.read_csv(path, keep_default_na=False)
        if frame.empty:
            continue
        for _, row in frame.iterrows():
            for key in IIC_KEYS:
                value = row.get(key)
                if pd.isna(value) or value == "":
                    continue
                b_repair, a_repair = key.split("_vs_")
                rows.append(
                    {
                        "ontology": ontology,
                        "b_repair": b_repair,
                        "a_repair": a_repair,
                        "value": float(value),
                        "run_id": str(row.get("run_id", "")),
                    }
                )
    if not rows:
        return pd.DataFrame(columns=["ontology", "b_repair", "a_repair", "value", "run_id"])
    return pd.DataFrame(rows)


def load_runtime_rows(paths: Iterable[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        match = CSV_NAME_RE.match(path.name)
        if not match:
            continue
        ontology = match.group("ontology")
        frame = pd.read_csv(path, keep_default_na=False)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["ontology"] = ontology
        frame["trial_status"] = frame["trial_status"].astype(str).str.strip().str.lower()
        frame["trial_elapsed_seconds"] = pd.to_numeric(frame.get("trial_elapsed_seconds"), errors="coerce")
        for repair_id in REPAIR_IDS:
            frame[f"{repair_id}_ms"] = pd.to_numeric(frame.get(f"{repair_id}_ms"), errors="coerce")
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["ontology", "trial_status"] + [f"{r}_ms" for r in REPAIR_IDS])
    return pd.concat(frames, ignore_index=True)


def build_iic_summary(iic_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    if iic_df.empty:
        return pd.DataFrame(columns=["ontology", "b_repair", "a_repair", "mean", "n", "ci_low", "ci_high"])

    ontologies = sorted([o for o in iic_df["ontology"].unique() if o != "overall"])
    for b_repair in B_REPAIRS:
        for a_repair in A_REPAIRS:
            for ontology in ontologies:
                values = iic_df.loc[
                    (iic_df["ontology"] == ontology)
                    & (iic_df["b_repair"] == b_repair)
                    & (iic_df["a_repair"] == a_repair),
                    "value",
                ].to_numpy(dtype=float)
                mean, n, ci_low, ci_high = mean_n_ci(values)
                rows.append(
                    {
                        "ontology": ontology,
                        "b_repair": b_repair,
                        "a_repair": a_repair,
                        "mean": mean,
                        "n": n,
                        "ci_low": ci_low,
                        "ci_high": ci_high,
                    }
                )

            overall_values = iic_df.loc[
                (iic_df["b_repair"] == b_repair) & (iic_df["a_repair"] == a_repair),
                "value",
            ].to_numpy(dtype=float)
            mean, n, ci_low, ci_high = mean_n_ci(overall_values)
            rows.append(
                {
                    "ontology": "overall",
                    "b_repair": b_repair,
                    "a_repair": a_repair,
                    "mean": mean,
                    "n": n,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                }
            )

    return pd.DataFrame(rows)


def build_runtime_summary(runtime_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    if runtime_df.empty:
        return pd.DataFrame(columns=["ontology", "repair_id", "mean_ms", "n"])

    ontologies = sorted([o for o in runtime_df["ontology"].unique() if o != "overall"])
    for ontology in ontologies + ["overall"]:
        subset = runtime_df if ontology == "overall" else runtime_df[runtime_df["ontology"] == ontology]
        success_subset = subset[subset["trial_status"] == "success"]
        for repair_id in REPAIR_IDS:
            values = success_subset[f"{repair_id}_ms"].to_numpy(dtype=float)
            values = values[~np.isnan(values)]
            n = int(values.size)
            mean = float(np.mean(values)) if n else float("nan")
            rows.append(
                {
                    "ontology": ontology,
                    "repair_id": repair_id,
                    "mean_ms": mean,
                    "n": n,
                }
            )
    return pd.DataFrame(rows)


def build_outcome_summary(runtime_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    if runtime_df.empty:
        return pd.DataFrame(
            columns=[
                "ontology",
                "total_trials",
                "success_count",
                "success_rate",
                "time_limit_exceeded_count",
                "time_limit_exceeded_rate",
                "memory_limit_exceeded_count",
                "memory_limit_exceeded_rate",
            ]
        )

    ontologies = sorted([o for o in runtime_df["ontology"].unique() if o != "overall"])
    for ontology in ontologies + ["overall"]:
        subset = runtime_df if ontology == "overall" else runtime_df[runtime_df["ontology"] == ontology]
        counts = subset["trial_status"].value_counts(dropna=False).to_dict()
        total = int(len(subset))
        rows.append(
            {
                "ontology": ontology,
                "total_trials": total,
                "success_count": int(counts.get("success", 0)),
                "success_rate": (counts.get("success", 0) / total if total else 0.0),
                "time_limit_exceeded_count": int(counts.get("time_limit_exceeded", 0)),
                "time_limit_exceeded_rate": (counts.get("time_limit_exceeded", 0) / total if total else 0.0),
                "memory_limit_exceeded_count": int(counts.get("memory_limit_exceeded", 0)),
                "memory_limit_exceeded_rate": (counts.get("memory_limit_exceeded", 0) / total if total else 0.0),
            }
        )
    return pd.DataFrame(rows)


def build_per_repair_outcome_summary(runtime_df: pd.DataFrame) -> pd.DataFrame:
    """Count succeeded, failed, and not-reached trials per repair.

    Repairs run in order: A1, A2, A3, B1, ..., B9.  When a trial fails at
    failure_stage, all earlier repairs succeeded, failure_stage itself failed,
    and all later repairs were never reached.
    """
    columns = ["ontology", "repair_id", "succeeded", "failed", "not_reached",
               "success_rate"]
    if runtime_df.empty:
        return pd.DataFrame(columns=columns)

    repair_order = {rid: i for i, rid in enumerate(REPAIR_IDS)}
    ontologies = sorted([o for o in runtime_df["ontology"].unique() if o != "overall"])

    rows: list[dict] = []
    for ontology in ontologies + ["overall"]:
        subset = runtime_df if ontology == "overall" else runtime_df[runtime_df["ontology"] == ontology]
        for repair_id in REPAIR_IDS:
            ri = repair_order[repair_id]
            succeeded = 0
            failed = 0
            not_reached = 0
            for _, trial in subset.iterrows():
                status = trial.get("trial_status", "")
                fs = trial.get("failure_stage", "")
                if status == "success":
                    succeeded += 1
                elif fs and fs in repair_order:
                    fs_idx = repair_order[fs]
                    if fs_idx == ri:
                        failed += 1
                    elif fs_idx < ri:
                        not_reached += 1
                    else:  # fs_idx > ri — failure happened after this repair
                        succeeded += 1
                # If fs is not a valid repair ID (e.g., "make_inconsistent" or empty), skip
            attempted = succeeded + failed
            rate = succeeded / attempted if attempted > 0 else float("nan")
            rows.append({
                "ontology": ontology,
                "repair_id": repair_id,
                "succeeded": succeeded,
                "failed": failed,
                "not_reached": not_reached,
                "success_rate": rate,
            })
    return pd.DataFrame(rows, columns=columns)


def build_iic_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "# IIC Summary",
        "",
        "Each table reports mean IIC and 95% confidence interval for one repair "
        "across each baseline.",
        "",
    ]
    if summary_df.empty:
        lines.append("No IIC data found.")
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    a_labels = [REPAIR_LABELS[a] for a in A_REPAIRS]
    for b_repair in B_REPAIRS:
        b_label = REPAIR_LABELS.get(b_repair, b_repair)
        lines.append(f"## {b_label}")
        lines.append("| ontology | " + " | ".join(a_labels) + " |")
        lines.append("| --- | " + " | ".join(["---"] * len(a_labels)) + " |")
        for ontology in ontologies:
            row_cells = [ontology]
            for a_repair in A_REPAIRS:
                match = summary_df[
                    (summary_df["ontology"] == ontology)
                    & (summary_df["b_repair"] == b_repair)
                    & (summary_df["a_repair"] == a_repair)
                ]
                if match.empty:
                    row_cells.append("-")
                else:
                    r = match.iloc[0]
                    row_cells.append(iic_cell(float(r["mean"]), float(r["ci_low"]), float(r["ci_high"]), int(r["n"])))
            lines.append("| " + " | ".join(row_cells) + " |")
        lines.append("")
    return lines


def build_iic_overview_markdown(summary_df: pd.DataFrame) -> list[str]:
    """Single collapsed table: each repair's overall mean IIC + 95% CI."""
    lines = [
        "## IIC Overview (overall)",
        "",
        "Mean IIC and 95% confidence interval averaged across all ontologies.",
        "",
    ]
    if summary_df.empty:
        lines.append("No IIC data found.")
        return lines

    overall = summary_df[summary_df["ontology"] == "overall"]
    if overall.empty:
        lines.append("No overall IIC data found.")
        return lines

    a_labels = [REPAIR_LABELS[a] for a in A_REPAIRS]
    lines.append("| Repair | " + " | ".join(a_labels) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(a_labels)) + " |")
    for b_repair in B_REPAIRS:
        b_label = REPAIR_LABELS.get(b_repair, b_repair)
        row_cells = [b_label]
        for a_repair in A_REPAIRS:
            match = overall[
                (overall["b_repair"] == b_repair)
                & (overall["a_repair"] == a_repair)
            ]
            if match.empty:
                row_cells.append("-")
            else:
                r = match.iloc[0]
                row_cells.append(iic_cell(float(r["mean"]), float(r["ci_low"]), float(r["ci_high"]), int(r["n"])))
        lines.append("| " + " | ".join(row_cells) + " |")
    lines.append("")
    return lines


def build_runtime_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "# Runtime Summary",
        "",
        "Average runtime of successful trials (mean only, milliseconds).",
        "",
    ]
    if summary_df.empty:
        lines.append("No runtime data found.")
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    headers = ["ontology"] + [REPAIR_LABELS.get(r, r) for r in REPAIR_IDS]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    row_terminator = "\\\\"
    for ontology in ontologies:
        row = [ontology]
        for repair_id in REPAIR_IDS:
            match = summary_df[(summary_df["ontology"] == ontology) & (summary_df["repair_id"] == repair_id)]
            if match.empty:
                row.append("-")
            else:
                r = match.iloc[0]
                row.append(runtime_cell(float(r["mean_ms"]), int(r["n"])))
        lines.append("| " + " | ".join(row) + " |")
    return lines


def build_outcome_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "# Power Index Outcome Rates",
        "",
        "Outcome rates at the trial level (all repairs executed in sequence).",
        "",
    ]
    if summary_df.empty:
        lines.append("No outcome data found.")
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    headers = ["ontology", "success", "time_limit_exceeded", "memory_limit_exceeded"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for ontology in ontologies:
        match = summary_df[summary_df["ontology"] == ontology]
        if match.empty:
            continue
        r = match.iloc[0]
        lines.append(
            "| " + " | ".join(
                [
                    str(r["ontology"]),
                    outcome_cell(int(r["success_count"]), float(r["success_rate"])),
                    outcome_cell(int(r["time_limit_exceeded_count"]), float(r["time_limit_exceeded_rate"])),
                    outcome_cell(int(r["memory_limit_exceeded_count"]), float(r["memory_limit_exceeded_rate"])),
                ]
            ) + " |"
        )
    return lines


def build_per_repair_outcome_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "## Per-Repair Outcome Rates",
        "",
        "Succeeded / Failed / Not-reached counts per repair algorithm. ",
        "Trials abort at the first failing repair, so repairs after the ",
        "failure stage are counted as \"not reached\".  The success rate ",
        "excludes not-reached trials from the denominator.",
        "",
    ]
    if summary_df.empty:
        lines.append("No per-repair outcome data found.")
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    for ontology in ontologies:
        subset = summary_df[summary_df["ontology"] == ontology]
        if subset.empty:
            continue
        lines.append(f"### {ontology}")
        lines.append("| Repair | Succeeded | Failed | Not Reached | Success Rate |")
        lines.append("| --- | --- | --- | --- | --- |")
        for repair_id in REPAIR_IDS:
            match = subset[subset["repair_id"] == repair_id]
            if match.empty:
                continue
            r = match.iloc[0]
            rate_str = percent(float(r["success_rate"])) if not math.isnan(float(r["success_rate"])) else "-"
            label = REPAIR_LABELS.get(repair_id, repair_id)
            lines.append(
                f"| {label} | {int(r['succeeded'])} | {int(r['failed'])} "
                f"| {int(r['not_reached'])} | {rate_str} |"
            )
        lines.append("")
    return lines


def build_iic_latex(summary_df: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    if summary_df.empty:
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    a_labels_tex = [latex_escape(REPAIR_LABELS[a]) for a in A_REPAIRS]
    for b_repair in B_REPAIRS:
        b_label = latex_escape(REPAIR_LABELS.get(b_repair, b_repair))
        lines.extend(
            [
                "% Auto-generated IIC table",
                "\\begin{table}[ht]",
                "  \\centering",
                f"  \\caption{{IIC results for {b_label} (mean and 95\\% CI)}}",
                "  \\begin{tabular}{lccc}",
                "    \\toprule",
                "    Ontology & " + " & ".join(a_labels_tex) + " \\\\",
                "    \\midrule",
            ]
        )
        for ontology in ontologies:
            cells: list[str] = []
            for a_repair in A_REPAIRS:
                match = summary_df[
                    (summary_df["ontology"] == ontology)
                    & (summary_df["b_repair"] == b_repair)
                    & (summary_df["a_repair"] == a_repair)
                ]
                if match.empty:
                    cells.append("-")
                else:
                    r = match.iloc[0]
                    cells.append(iic_cell(float(r["mean"]), float(r["ci_low"]), float(r["ci_high"]), int(r["n"])))
            lines.append(
                f"    {latex_escape(ontology)} & {latex_escape(cells[0])} & {latex_escape(cells[1])} & {latex_escape(cells[2])} \\\\")
        lines.extend(
            [
                "    \\bottomrule",
                "  \\end{tabular}",
                "\\end{table}",
                "",
            ]
        )
    return lines


def build_iic_overview_latex(summary_df: pd.DataFrame) -> list[str]:
    """Single collapsed LaTeX table: each repair's overall mean IIC + 95% CI."""
    lines: list[str] = []
    if summary_df.empty:
        return lines

    overall = summary_df[summary_df["ontology"] == "overall"]
    if overall.empty:
        return lines

    a_labels_tex = [latex_escape(REPAIR_LABELS[a]) for a in A_REPAIRS]
    lines.extend(
        [
            "% Auto-generated IIC overview table",
            "\\begin{table}[ht]",
            "  \\centering",
            "  \\caption{IIC overview --- overall mean and 95\\% CI across all ontologies}",
            "  \\label{tab:iic-overview}",
            "  \\begin{tabular}{lccc}",
            "    \\toprule",
            "    Repair & " + " & ".join(a_labels_tex) + " \\\\",
            "    \\midrule",
        ]
    )
    for b_repair in B_REPAIRS:
        b_label = latex_escape(REPAIR_LABELS.get(b_repair, b_repair))
        cells: list[str] = []
        for a_repair in A_REPAIRS:
            match = overall[
                (overall["b_repair"] == b_repair)
                & (overall["a_repair"] == a_repair)
            ]
            if match.empty:
                cells.append("-")
            else:
                r = match.iloc[0]
                cells.append(iic_cell(float(r["mean"]), float(r["ci_low"]), float(r["ci_high"]), int(r["n"])))
        lines.append(
            f"    {b_label} & {latex_escape(cells[0])} & {latex_escape(cells[1])} & {latex_escape(cells[2])} \\\\"
        )
    lines.extend(
        [
            "    \\bottomrule",
            "  \\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    return lines


def build_runtime_latex(summary_df: pd.DataFrame) -> list[str]:
    runtime_labels = [REPAIR_LABELS.get(r, r) for r in REPAIR_IDS]
    latex_runtime_labels = [latex_escape(l) for l in runtime_labels]
    lines: list[str] = [
        "% Auto-generated runtime table",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Average runtime of successful trials (milliseconds)}",
        "  \\begin{tabular}{l" + "c" * len(REPAIR_IDS) + "}",
        "    \\toprule",
        "    Ontology & " + " & ".join(latex_runtime_labels) + " \\\\",
        "    \\midrule",
    ]
    if summary_df.empty:
        lines.extend([
            "    No data \\\\",
            "    \\bottomrule",
            "  \\end{tabular}",
            "\\end{table}",
        ])
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    row_terminator = "\\\\"
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    for ontology in ontologies:
        cells: list[str] = []
        for repair_id in REPAIR_IDS:
            match = summary_df[(summary_df["ontology"] == ontology) & (summary_df["repair_id"] == repair_id)]
            if match.empty:
                cells.append("-")
            else:
                r = match.iloc[0]
                cells.append(runtime_cell(float(r["mean_ms"]), int(r["n"])))
        lines.append("    " + latex_escape(ontology) + " & " + " & ".join(latex_escape(c) for c in cells) + " " + row_terminator)

    lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])
    return lines


def build_outcome_latex(summary_df: pd.DataFrame) -> list[str]:
    lines: list[str] = [
        "% Auto-generated outcome table",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Power-index trial outcome rates}",
        "  \\begin{tabular}{lccc}",
        "    \\toprule",
        "    Ontology & Success & Time limit & Memory limit \\\\",
        "    \\midrule",
    ]
    if summary_df.empty:
        lines.extend([
            "    No data \\\\",
            "    \\bottomrule",
            "  \\end{tabular}",
            "\\end{table}",
        ])
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    for ontology in ontologies:
        match = summary_df[summary_df["ontology"] == ontology]
        if match.empty:
            continue
        r = match.iloc[0]
        lines.append(
            "    "
            + latex_escape(ontology)
            + " & "
            + latex_escape(outcome_cell(int(r["success_count"]), float(r["success_rate"])))
            + " & "
            + latex_escape(outcome_cell(int(r["time_limit_exceeded_count"]), float(r["time_limit_exceeded_rate"])))
            + " & "
            + latex_escape(outcome_cell(int(r["memory_limit_exceeded_count"]), float(r["memory_limit_exceeded_rate"])))
            + " \\\\")

    lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])
    return lines


def build_per_repair_outcome_latex(summary_df: pd.DataFrame) -> list[str]:
    lines: list[str] = [
        "% Auto-generated per-repair outcome table",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Per-repair outcome rates (Succeeded / Failed / Not reached)}",
        "  \\begin{tabular}{lcccc}",
        "    \\toprule",
        "    Repair & Succeeded & Failed & Not reached & Success rate \\\\",
        "    \\midrule",
    ]
    if summary_df.empty:
        lines.extend([
            "    No data \\\\",
            "    \\bottomrule",
            "  \\end{tabular}",
            "\\end{table}",
        ])
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    for ontology in ontologies:
        subset = summary_df[summary_df["ontology"] == ontology]
        if subset.empty:
            continue
        lines.append(f"    \\multicolumn{{5}}{{l}}{{{latex_escape(ontology)}}} \\\\")
        for repair_id in REPAIR_IDS:
            match = subset[subset["repair_id"] == repair_id]
            if match.empty:
                continue
            r = match.iloc[0]
            rate_str = percent(float(r["success_rate"])) if not math.isnan(float(r["success_rate"])) else "-"
            label = latex_escape(REPAIR_LABELS.get(repair_id, repair_id))
            lines.append(
                f"    {label} & {int(r['succeeded'])} & "
                f"{int(r['failed'])} & {int(r['not_reached'])} & {latex_escape(rate_str)} \\\\"
            )
        lines.append("    \\midrule")

    lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])
    return lines


def run_analysis(
    data_dir: Path,
    out_dir: Path,
    inconsistent_dir: Path | None = None,
) -> dict[str, Any]:
    """Aggregate trial outputs and produce reports.

    Parameters
    ----------
    data_dir
        Directory containing ``iic-*.csv`` and ``runtime-*.csv`` files.
    out_dir
        Directory where summary CSVs and reports will be written.
    inconsistent_dir
        Optional path to the ``inconsistent/`` ontology directory, used
        for axiom count resolution in the complexity estimator.

    Returns
    -------
    dict
        Paths to all generated files and, if computed, the estimation result dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    iic_paths = discover_csvs(data_dir, "iic")
    runtime_paths = discover_csvs(data_dir, "runtime")
    if not iic_paths:
        raise SystemExit(f"No IIC CSVs found in {data_dir}")
    if not runtime_paths:
        raise SystemExit(f"No runtime CSVs found in {data_dir}")

    iic_df = load_iic_rows(iic_paths)
    runtime_df = load_runtime_rows(runtime_paths)

    iic_summary_df = build_iic_summary(iic_df)
    runtime_summary_df = build_runtime_summary(runtime_df)
    outcome_summary_df = build_outcome_summary(runtime_df)
    per_repair_outcome_df = build_per_repair_outcome_summary(runtime_df)

    iic_summary_csv = out_dir / "iic_summary.csv"
    runtime_summary_csv = out_dir / "runtime_summary.csv"
    outcome_summary_csv = out_dir / "outcome_summary.csv"
    per_repair_outcome_csv = out_dir / "per_repair_outcome_summary.csv"
    report_md = out_dir / "combined_report.md"
    report_tex = out_dir / "combined_report.tex"

    iic_summary_df.to_csv(iic_summary_csv, index=False)
    runtime_summary_df.to_csv(runtime_summary_csv, index=False)
    outcome_summary_df.to_csv(outcome_summary_csv, index=False)
    per_repair_outcome_df.to_csv(per_repair_outcome_csv, index=False)

    # ── Complexity estimation ───────────────────────────────────────
    estimation: dict[str, Any] | None = None
    if not runtime_df.empty:
        try:
            axiom_counts = load_axiom_counts(
                names=runtime_df["ontology"].unique().tolist(),
                inconsistent_dir=inconsistent_dir,
            )
            estimation = estimate_complexity(
                runtime_df, axiom_counts=axiom_counts,
                repair_ids=REPAIR_IDS,
            )
        except Exception as exc:
            print(f"Warning: complexity estimation failed: {exc}", file=sys.stderr)
            estimation = None

    # ── Markdown report ─────────────────────────────────────────────
    md_lines = ["# Combined Experiment Summary", ""]
    md_lines.extend(build_iic_overview_markdown(iic_summary_df))
    md_lines.extend(["", "---", ""])
    md_lines.extend(build_iic_markdown(iic_summary_df))
    md_lines.extend(["", "---", ""])
    md_lines.extend(build_runtime_markdown(runtime_summary_df))
    md_lines.extend(["", "---", ""])
    md_lines.extend(build_outcome_markdown(outcome_summary_df))
    md_lines.extend(["", "---", ""])
    md_lines.extend(build_per_repair_outcome_markdown(per_repair_outcome_df))
    if estimation is not None:
        md_lines.extend(["", "---", ""])
        md_lines.extend(build_estimation_markdown(estimation))
    report_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # ── LaTeX report ────────────────────────────────────────────────
    tex_lines = [
        "% Auto-generated combined summary",
        "\\documentclass{article}",
        "\\usepackage{booktabs}",
        "\\begin{document}",
    ]
    tex_lines.extend(build_iic_overview_latex(iic_summary_df))
    tex_lines.extend(build_iic_latex(iic_summary_df))
    tex_lines.extend(build_runtime_latex(runtime_summary_df))
    tex_lines.extend(build_outcome_latex(outcome_summary_df))
    tex_lines.extend(build_per_repair_outcome_latex(per_repair_outcome_df))
    if estimation is not None:
        tex_lines.extend(build_estimation_latex(estimation))
    tex_lines.append("\\end{document}")
    report_tex.write_text("\n".join(tex_lines) + "\n", encoding="utf-8")

    result: dict[str, Any] = {
        "iic_summary_csv": iic_summary_csv,
        "runtime_summary_csv": runtime_summary_csv,
        "outcome_summary_csv": outcome_summary_csv,
        "per_repair_outcome_csv": per_repair_outcome_csv,
        "report_md": report_md,
        "report_tex": report_tex,
    }
    if estimation is not None:
        result["estimation"] = estimation
    return result
