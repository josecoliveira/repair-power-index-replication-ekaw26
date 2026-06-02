#!/usr/bin/env python3
"""Aggregate A/B trial outputs into CSV, Markdown, and LaTeX reports."""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats

A_REPAIRS = ["A1", "A2", "A3"]
B_REPAIRS = [f"B{i}" for i in range(1, 10)]
REPAIR_IDS = A_REPAIRS + B_REPAIRS
IIC_KEYS = [f"{b}_vs_{a}" for b in B_REPAIRS for a in A_REPAIRS]
OUTCOME_ORDER = ["success", "time_limit_exceeded", "memory_limit_exceeded"]
CSV_NAME_RE = re.compile(r"^(?P<kind>iic|runtime)-(?P<ontology>.+)\.csv$")


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


def build_iic_markdown(summary_df: pd.DataFrame) -> list[str]:
    lines = [
        "# IIC Summary (B repairs vs A repairs)",
        "",
        "Each table reports mean IIC and 95% confidence interval for one B repair across A1/A2/A3 baselines.",
        "",
    ]
    if summary_df.empty:
        lines.append("No IIC data found.")
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    for b_repair in B_REPAIRS:
        lines.append(f"## {b_repair}")
        lines.append("| ontology | A1 | A2 | A3 |")
        lines.append("| --- | --- | --- | --- |")
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

    headers = ["ontology"] + REPAIR_IDS
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
        "Outcome rates at the trial level (all A and B repairs executed in sequence).",
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


def build_iic_latex(summary_df: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    if summary_df.empty:
        return lines

    ontologies = sorted([o for o in summary_df["ontology"].unique() if o != "overall"])
    if "overall" in summary_df["ontology"].unique():
        ontologies.append("overall")

    for b_repair in B_REPAIRS:
        lines.extend(
            [
                "% Auto-generated IIC table",
                "\\begin{table}[ht]",
                "  \\centering",
                f"  \\caption{{IIC results for {latex_escape(b_repair)} (mean and 95\\% CI)}}",
                "  \\begin{tabular}{lccc}",
                "    \\toprule",
                "    Ontology & A1 & A2 & A3 \\\\",
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


def build_runtime_latex(summary_df: pd.DataFrame) -> list[str]:
    lines: list[str] = [
        "% Auto-generated runtime table",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Average runtime of successful trials (milliseconds)}",
        "  \\begin{tabular}{l" + "c" * len(REPAIR_IDS) + "}",
        "    \\toprule",
        "    Ontology & " + " & ".join(REPAIR_IDS) + " \\\\",
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


def run_analysis(data_dir: Path, out_dir: Path) -> dict[str, Path]:
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

    iic_summary_csv = out_dir / "iic_summary.csv"
    runtime_summary_csv = out_dir / "runtime_summary.csv"
    outcome_summary_csv = out_dir / "outcome_summary.csv"
    report_md = out_dir / "combined_report.md"
    report_tex = out_dir / "combined_report.tex"

    iic_summary_df.to_csv(iic_summary_csv, index=False)
    runtime_summary_df.to_csv(runtime_summary_csv, index=False)
    outcome_summary_df.to_csv(outcome_summary_csv, index=False)

    md_lines = ["# Combined Experiment Summary", ""]
    md_lines.extend(build_iic_markdown(iic_summary_df))
    md_lines.extend(["", "---", ""])
    md_lines.extend(build_runtime_markdown(runtime_summary_df))
    md_lines.extend(["", "---", ""])
    md_lines.extend(build_outcome_markdown(outcome_summary_df))
    report_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

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
    report_tex.write_text("\n".join(tex_lines) + "\n", encoding="utf-8")

    return {
        "iic_summary_csv": iic_summary_csv,
        "runtime_summary_csv": runtime_summary_csv,
        "outcome_summary_csv": outcome_summary_csv,
        "report_md": report_md,
        "report_tex": report_tex,
    }


def main() -> None:
    if len(sys.argv) >= 2:
        data_dir = Path(sys.argv[1]).resolve()
    else:
        data_candidates = sorted(Path(__file__).parent.glob("data-*"))
        if not data_candidates:
            raise SystemExit("No data-* directory found. Pass a data directory explicitly.")
        data_dir = data_candidates[-1]

    if len(sys.argv) >= 3:
        out_dir = Path(sys.argv[2]).resolve()
    else:
        stamp = data_dir.name.replace("data-", "")
        out_dir = (Path(__file__).parent / f"results-{stamp}").resolve()

    outputs = run_analysis(data_dir, out_dir)
    print("Wrote:")
    for key, value in outputs.items():
        print(f"  {key}={value}")


if __name__ == "__main__":
    main()
