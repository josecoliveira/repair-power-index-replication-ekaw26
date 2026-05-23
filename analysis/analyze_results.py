#!/usr/bin/env python3
"""Unified analysis for IIC quality and runtime/outcome results.

This script reads the per-run CSVs produced by `run_trials.py`:

- iic-<ontology>-<RUN_ID>.csv
- runtime-<ontology>-<RUN_ID>.csv

It generates:

- the existing IIC quality report/table layout
- a runtime summary table for successful trials
- an outcome-rate table for the power-index trials

Outputs are written under `analysis/output/shapley-shapley`.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats


BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data" / "shapley-shapley"
OUT_DIR = BASE_DIR / "output" / "shapley-shapley"

IIC_SUMMARY_CSV = OUT_DIR / "iic_summary.csv"
RUNTIME_SUMMARY_CSV = OUT_DIR / "runtime_summary.csv"
OUTCOME_SUMMARY_CSV = OUT_DIR / "outcome_summary.csv"
REPORT_MD = OUT_DIR / "combined_report.md"
REPORT_TEX = OUT_DIR / "combined_report.tex"

COMPARISON_ORDER = [
	("iic_power_vs_random", "vs random"),
	("iic_power_vs_not_in_largest_mcs", "vs non_in_largest_mcs"),
	("iic_power_vs_weakening", "vs weakening"),
]

REPAIR_ORDER = [
	("random_removal_ms", "Random removal"),
	("not_in_largest_mcs_removal_ms", "Not-in-largest-MCS removal"),
	("weakening_ms", "Weakening"),
	("power_index_ms", "Power index"),
]

OUTCOME_ORDER = ["success", "time_limit_exceeded", "memory_limit_exceeded"]

CSV_NAME_RE = re.compile(r"^(?P<kind>iic|runtime)-(?P<ontology>.+?)-(?P<runid>\d+)(?:-reconstructed)?\.csv$")


def strip_trailing_run_id(name: str) -> str:
    return re.sub(r"-\d+(?:-reconstructed)?$", "", name)


def mean_std_n_ci(arr: np.ndarray, conf: float = 0.95):
    arr = np.asarray(arr, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    n = arr.size
    if n == 0:
        return float("nan"), float("nan"), 0, float("nan"), float("nan")

    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    if n == 1:
        return mean, std, 1, mean, mean

    se = std / math.sqrt(n)
    alpha = 1.0 - conf
    tcrit = stats.t.ppf(1.0 - alpha / 2.0, n - 1)
    ci_low = mean - tcrit * se
    ci_high = mean + tcrit * se
    return mean, std, n, ci_low, ci_high


def compute_runtime_stats(arr: np.ndarray):
    arr = np.asarray(arr, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    n = arr.size
    if n == 0:
        return {
            "mean": float("nan"),
            "sd": float("nan"),
            "median": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
            "n": 0,
        }
    return {
        "mean": float(np.mean(arr)),
        "sd": float(np.std(arr, ddof=1)) if n > 1 else 0.0,
        "median": float(np.median(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "n": int(n),
    }


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


def discover_csvs(kind: str) -> list[Path]:
    paths = []
    for path in sorted(DATA_DIR.glob(f"{kind}-*.csv")):
        match = CSV_NAME_RE.match(path.name)
        if match and match.group("kind") == kind:
            paths.append(path)
    return paths


def load_iic_rows(paths: Iterable[Path]) -> pd.DataFrame:
    rows: list[dict] = []
    for path in paths:
        match = CSV_NAME_RE.match(path.name)
        if not match:
            continue
        ontology = match.group("ontology")
        run_id = match.group("runid")
        df = pd.read_csv(path, keep_default_na=False)
        if df.empty:
            continue
        for _, row in df.iterrows():
            for comparison_key, _ in COMPARISON_ORDER:
                value = row.get(comparison_key)
                if pd.isna(value) or value == "":
                    continue
                rows.append(
                    {
                        "ontology": ontology,
                        "comparison": comparison_key,
                        "value": float(value),
                        "run_id": str(row.get("run_id", run_id)),
                    }
                )
    if not rows:
        return pd.DataFrame(columns=["ontology", "comparison", "value", "run_id"])
    return pd.DataFrame(rows)


def load_runtime_frames(paths: Iterable[Path]) -> pd.DataFrame:
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
        for col, _ in REPAIR_ORDER:
            frame[col] = pd.to_numeric(frame.get(col), errors="coerce")
        frame["trial_elapsed_seconds"] = pd.to_numeric(frame.get("trial_elapsed_seconds"), errors="coerce")
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["ontology", "trial_status"] + [c for c, _ in REPAIR_ORDER])
    return pd.concat(frames, ignore_index=True)


def build_iic_summary(iic_df: pd.DataFrame) -> pd.DataFrame:
    summary_rows: list[dict] = []
    if iic_df.empty:
        return pd.DataFrame(columns=["ontology", "comparison", "mean", "sd", "n", "ci_low", "ci_high", "reject_h0"])

    ontologies = sorted([o for o in iic_df["ontology"].unique() if o != "overall"])
    for comparison_key, _ in COMPARISON_ORDER:
        for ontology in ontologies:
            values = iic_df.loc[(iic_df["ontology"] == ontology) & (iic_df["comparison"] == comparison_key), "value"].to_numpy(dtype=float)
            mean, sd, n, ci_low, ci_high = mean_std_n_ci(values)
            summary_rows.append(
                {
                    "ontology": ontology,
                    "comparison": comparison_key,
                    "mean": mean,
                    "sd": sd,
                    "n": n,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                    "reject_h0": bool(ci_low > 0.5),
                }
            )

        overall_values = iic_df.loc[iic_df["comparison"] == comparison_key, "value"].to_numpy(dtype=float)
        mean, sd, n, ci_low, ci_high = mean_std_n_ci(overall_values)
        summary_rows.append(
            {
                "ontology": "overall",
                "comparison": comparison_key,
                "mean": mean,
                "sd": sd,
                "n": n,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "reject_h0": bool(ci_low > 0.5),
            }
        )

    return pd.DataFrame(summary_rows)


def build_runtime_summary(runtime_df: pd.DataFrame) -> pd.DataFrame:
    summary_rows: list[dict] = []
    if runtime_df.empty:
        return pd.DataFrame(columns=["ontology", "repair_method", "mean_ms", "sd_ms", "median_ms", "min_ms", "max_ms", "n", "ci_low_ms", "ci_high_ms"])

    ontologies = sorted([o for o in runtime_df["ontology"].unique() if o != "overall"])
    for ontology in ontologies + ["overall"]:
        subset = runtime_df if ontology == "overall" else runtime_df[runtime_df["ontology"] == ontology]
        success_subset = subset[subset["trial_status"] == "success"]
        for column, _label in REPAIR_ORDER:
            values = success_subset[column].to_numpy(dtype=float)
            # basic runtime stats (median/min/max)
            stats_map = compute_runtime_stats(values)
            # mean, sd, n and 95% t-based CI for the mean
            mean, sd, n, ci_low, ci_high = mean_std_n_ci(values)
            summary_rows.append(
                {
                    "ontology": ontology,
                    "repair_method": column,
                    "mean_ms": mean,
                    "sd_ms": sd,
                    "median_ms": stats_map["median"],
                    "min_ms": stats_map["min"],
                    "max_ms": stats_map["max"],
                    "n": int(n),
                    "ci_low_ms": ci_low,
                    "ci_high_ms": ci_high,
                }
            )
    return pd.DataFrame(summary_rows)


def build_outcome_summary(runtime_df: pd.DataFrame) -> pd.DataFrame:
    summary_rows: list[dict] = []
    if runtime_df.empty:
        return pd.DataFrame(columns=["ontology", "total_trials", "success_count", "success_rate", "time_limit_exceeded_count", "time_limit_exceeded_rate", "memory_limit_exceeded_count", "memory_limit_exceeded_rate"])

    ontologies = sorted([o for o in runtime_df["ontology"].unique() if o != "overall"])
    for ontology in ontologies + ["overall"]:
        subset = runtime_df if ontology == "overall" else runtime_df[runtime_df["ontology"] == ontology]
        counts = subset["trial_status"].value_counts(dropna=False).to_dict()
        total = int(len(subset))
        summary_rows.append(
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
    return pd.DataFrame(summary_rows)


def runtime_cell(mean_ms: float, sd_ms: float, n: int, ci_low_ms: float, ci_high_ms: float) -> str:
    # Show mean only (milliseconds). Exclude standard deviation and confidence interval.
    if n == 0 or math.isnan(mean_ms):
        return "-"
    return f"{mean_ms:.2f} ms"


def outcome_cell(count: int, rate: float) -> str:
    return f"{count} ({percent(rate)})"


def build_iic_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "# IIC Summary",
        "",
        "Decision rule: reject H0 when the lower bound of the 95% confidence interval is greater than 0.5.",
        "",
    ]
    for comparison_key, label in COMPARISON_ORDER:
        table_df = summary_df[summary_df["comparison"] == comparison_key].copy()
        if table_df.empty:
            continue
        table_df["comparison"] = label
        table_df.loc[table_df["ontology"] == "overall", "ontology"] = "overall"
        lines.append(f"## {label}")
        lines.append(table_df.to_markdown(index=False))
        lines.append("")
    return lines


def build_iic_latex(summary_df: pd.DataFrame) -> list[str]:
    comp_to_col = {
        "iic_power_vs_random": "Removal",
        "iic_power_vs_not_in_largest_mcs": "MCS",
        "iic_power_vs_weakening": "Weakening",
    }

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    tex_lines = [
        "% Auto-generated IIC summary table",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{IIC results: mean and 95\\% confidence intervals}",
        "  \\begin{tabular}{lccc}",
        "    \\toprule",
        "    Ontology name & Removal & MCS & Weakening " + "\\\\",
        "    \\midrule",
    ]

    def fmt_cell(row: pd.Series) -> str:
        try:
            mean = float(row["mean"])
            lo = float(row["ci_low"])
            hi = float(row["ci_high"])
            return f"{mean:.2f} [{lo:.2f}; {hi:.2f}]"
        except Exception:
            return ""

    for ont in ontologies:
        cells = []
        for comp_key, _label in COMPARISON_ORDER:
            match = summary_df[(summary_df["ontology"] == ont) & (summary_df["comparison"] == comp_key)]
            cells.append(fmt_cell(match.iloc[0]) if not match.empty else "")
        tex_lines.append(f"    {latex_escape(ont)} & {cells[0]} & {cells[1]} & {cells[2]} " + "\\\\")
    tex_lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])
    return tex_lines


def build_runtime_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "# Runtime Summary",
        "",
        "Average runtime of successful trials (mean, in milliseconds).",
        "",
    ]
    if summary_df.empty:
        lines.append("No runtime data found.")
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    headers = ["ontology"] + [label for _, label in REPAIR_ORDER]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for ont in ontologies:
        row = [ont]
        for column, _label in REPAIR_ORDER:
            match = summary_df[(summary_df["ontology"] == ont) & (summary_df["repair_method"] == column)]
            if match.empty:
                row.append("-")
            else:
                r = match.iloc[0]
                row.append(runtime_cell(
                    float(r["mean_ms"]),
                    float(r["sd_ms"]),
                    int(r["n"]),
                    float(r.get("ci_low_ms", float("nan"))),
                    float(r.get("ci_high_ms", float("nan"))),
                ))
        lines.append("| " + " | ".join(row) + " |")
    return lines


def build_outcome_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "# Power-index Outcome Rates",
        "",
        "Outcome rates for the full trial (all four repairs are run in sequence).",
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
    for ont in ontologies:
        match = summary_df[summary_df["ontology"] == ont]
        if match.empty:
            continue
        r = match.iloc[0]
        lines.append(
            "| " + " | ".join([
                r["ontology"],
                outcome_cell(int(r["success_count"]), float(r["success_rate"])),
                outcome_cell(int(r["time_limit_exceeded_count"]), float(r["time_limit_exceeded_rate"])),
                outcome_cell(int(r["memory_limit_exceeded_count"]), float(r["memory_limit_exceeded_rate"])),
            ]) + " |"
        )
    return lines


def build_runtime_latex(summary_df: pd.DataFrame) -> list[str]:
    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    tex_lines = [
        "% Auto-generated runtime summary table",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Average runtime of successful trials (milliseconds)}",
        "  \\begin{tabular}{lcccc}",
        "    \\toprule",
        "    Ontology name & Random removal & Not-in-largest-MCS removal & Weakening & Power index " + "\\\\",
        "    \\midrule",
    ]
    for ont in ontologies:
        cells = []
        for column, _label in REPAIR_ORDER:
            match = summary_df[(summary_df["ontology"] == ont) & (summary_df["repair_method"] == column)]
            if match.empty:
                cells.append("-")
            else:
                r = match.iloc[0]
                cells.append(runtime_cell(
                    float(r["mean_ms"]),
                    float(r["sd_ms"]),
                    int(r["n"]),
                    float(r.get("ci_low_ms", float("nan"))),
                    float(r.get("ci_high_ms", float("nan"))),
                ))
        tex_lines.append(f"    {latex_escape(ont)} & {latex_escape(cells[0])} & {latex_escape(cells[1])} & {latex_escape(cells[2])} & {latex_escape(cells[3])} " + "\\\\")
    tex_lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])
    return tex_lines


def build_outcome_latex(summary_df: pd.DataFrame) -> list[str]:
    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    tex_lines = [
        "% Auto-generated outcome summary table",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Power-index trial outcome rates}",
        "  \\begin{tabular}{lccc}",
        "    \\toprule",
        "    Ontology name & Success & Time limit & Memory limit " + "\\\\",
        "    \\midrule",
    ]

    for ont in ontologies:
        match = summary_df[summary_df["ontology"] == ont]
        if match.empty:
            continue
        r = match.iloc[0]
        tex_lines.append(
            f"    {latex_escape(ont)} & {latex_escape(outcome_cell(int(r['success_count']), float(r['success_rate'])))} & "
            f"{latex_escape(outcome_cell(int(r['time_limit_exceeded_count']), float(r['time_limit_exceeded_rate'])))} & "
            f"{latex_escape(outcome_cell(int(r['memory_limit_exceeded_count']), float(r['memory_limit_exceeded_rate'])))} " + "\\\\"
        )

    tex_lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])
    return tex_lines


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    iic_paths = discover_csvs("iic")
    runtime_paths = discover_csvs("runtime")
    if not iic_paths:
        raise SystemExit(f"No IIC CSVs found in {DATA_DIR}")
    if not runtime_paths:
        raise SystemExit(f"No runtime CSVs found in {DATA_DIR}")

    iic_df = load_iic_rows(iic_paths)
    runtime_df = load_runtime_frames(runtime_paths)

    iic_summary_df = build_iic_summary(iic_df)
    runtime_summary_df = build_runtime_summary(runtime_df)
    outcome_summary_df = build_outcome_summary(runtime_df)

    iic_summary_df.to_csv(IIC_SUMMARY_CSV, index=False)
    runtime_summary_df.to_csv(RUNTIME_SUMMARY_CSV, index=False)
    outcome_summary_df.to_csv(OUTCOME_SUMMARY_CSV, index=False)

    md_lines = ["# Combined Experiment Summary", ""]
    md_lines.extend(build_iic_markdown(iic_summary_df))
    md_lines.extend(build_runtime_markdown(runtime_summary_df))
    md_lines.extend(build_outcome_markdown(outcome_summary_df))
    REPORT_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    tex_lines = [
        "% Auto-generated combined summary",
        "\\documentclass{article}",
        "\\usepackage{booktabs}",
        "\\begin{document}",
    ]
    tex_lines.extend(build_iic_latex(iic_summary_df))
    tex_lines.extend(build_runtime_latex(runtime_summary_df))
    tex_lines.extend(build_outcome_latex(outcome_summary_df))
    tex_lines.append("\\end{document}")
    REPORT_TEX.write_text("\n".join(tex_lines) + "\n", encoding="utf-8")

    print("Wrote:")
    print(f"  {IIC_SUMMARY_CSV}")
    print(f"  {RUNTIME_SUMMARY_CSV}")
    print(f"  {OUTCOME_SUMMARY_CSV}")
    print(f"  {REPORT_MD}")
    print(f"  {REPORT_TEX}")


if __name__ == "__main__":
    main()
