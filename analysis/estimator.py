"""Complexity estimation for the ontology repair runtime data.

Fits polynomial and exponential models to runtime-vs-axiom-count data
and reports the better-fitting model with R².

Two modes of operation:
  1. **Programmatic** — call ``estimate_complexity(runtime_df, axiom_counts)``
     with data already loaded by the caller (e.g. from the analyzer).
  2. **Standalone** — call ``run_estimation()`` or use the CLI entry point
     in ``cli/estimator.py`` which resolves data from a results folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import linregress

from analysis.ontologyutils_service import classify_ontology

# ── Package layout ──────────────────────────────────────────────────────

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = PACKAGE_ROOT / "data"
CLASSIFICATION_FILE = PACKAGE_ROOT / "ontology_classification.md"
AXIOM_CACHE_FILE = ANALYSIS_DIR / "axiom_counts.json"


# ── Public estimator API ────────────────────────────────────────────────


def estimate_complexity(
    runtime_df: pd.DataFrame,
    axiom_counts: dict[str, int] | None = None,
    inconsistent_dir: Path | None = None,
) -> dict[str, Any]:
    """Estimate computational complexity from experimental runtime data.

    Parameters
    ----------
    runtime_df
        DataFrame from ``load_runtime_rows()``.  Must have columns
        ``ontology``, ``trial_status``, ``trial_elapsed_seconds``.
    axiom_counts
        Mapping ``{ontology_name: axiom_count}``.  If ``None`` or empty,
        the function tries to resolve axiom counts from
        ``ontology_classification.md`` and, as a last resort, by calling
        ``classify_ontology`` on the ontology files in *inconsistent_dir*.
    inconsistent_dir
        Path to the ``inconsistent/`` ontology directory.  Used as a
        fallback when *axiom_counts* is not provided.

    Returns
    -------
    dict with keys:
        - ``best_fit``: ``"polynomial"`` or ``"exponential"``
        - ``r2_poly``: R² of the polynomial (log-log) fit
        - ``r2_exp``: R² of the exponential (semi-log) fit
        - ``k``: estimated exponent of the polynomial model
        - ``b``: estimated base of the exponential model
        - ``data_points``: list of ``{ontology, axioms, mean_runtime_s}``
        - ``n_ontologies``: number of data points used
        - ``poly_equation``: human-readable string, e.g. ``"O(N^2.34)"``
        - ``exp_equation``: human-readable string, e.g. ``"O(1.02^N)"``
        - ``conclusion``: short string stating which model is favored
    """
    # ── Prepare data ────────────────────────────────────────────────
    if runtime_df.empty:
        return _empty_result("No runtime data available.")

    success_df = runtime_df[runtime_df["trial_status"].str.strip().str.lower() == "success"]
    if success_df.empty:
        return _empty_result("No successful trials to estimate runtime from.")

    # Group by ontology → mean trial_elapsed_seconds
    grouped = (
        success_df.groupby("ontology")["trial_elapsed_seconds"]
        .mean()
        .reset_index()
        .rename(columns={"trial_elapsed_seconds": "mean_runtime_s"})
    )

    # ── Resolve axiom counts ────────────────────────────────────────
    names: list[str] = sorted(grouped["ontology"].unique().tolist())
    if not axiom_counts:
        axiom_counts = load_axiom_counts(names, inconsistent_dir=inconsistent_dir)

    # Merge axiom counts into the grouped data
    grouped["axioms"] = grouped["ontology"].map(axiom_counts)
    valid = grouped.dropna(subset=["axioms"]).copy()
    valid["axioms"] = valid["axioms"].astype(int)

    if valid.empty:
        return _empty_result(
            "Could not resolve axiom counts for any ontology."
        )

    # Build data-point list for the report
    data_points: list[dict[str, Any]] = []
    for _, row in valid.iterrows():
        data_points.append({
            "ontology": str(row["ontology"]),
            "axioms": int(row["axioms"]),
            "mean_runtime_s": float(row["mean_runtime_s"]),
        })

    N: np.ndarray = valid["axioms"].to_numpy(dtype=float)
    T: np.ndarray = valid["mean_runtime_s"].to_numpy(dtype=float)

    # ── Polynomial fit (log-log): T = a * N^k ──────────────────────
    log_N = np.log(N)
    log_T = np.log(T)
    slope_poly, intercept_poly, r_poly, _, _ = linregress(log_N, log_T)
    r2_poly = float(r_poly ** 2)
    k = float(slope_poly)

    # ── Exponential fit (semi-log): T = a * b^N ────────────────────
    slope_exp, intercept_exp, r_exp, _, _ = linregress(N, log_T)
    r2_exp = float(r_exp ** 2)
    b = float(np.exp(slope_exp))

    # ── Determine best fit ──────────────────────────────────────────
    best_fit: str
    if r2_poly >= r2_exp:
        best_fit = "polynomial"
    else:
        best_fit = "exponential"

    poly_eq = f"O(N^{k:.2f})"
    exp_eq = f"O({b:.2f}^N)"

    return {
        "best_fit": best_fit,
        "r2_poly": r2_poly,
        "r2_exp": r2_exp,
        "k": k,
        "b": b,
        "data_points": data_points,
        "n_ontologies": len(valid),
        "poly_equation": poly_eq,
        "exp_equation": exp_eq,
        "conclusion": (
            f"Evidence favors Polynomial: N^{k:.2f}"
            if best_fit == "polynomial"
            else f"Evidence favors Exponential: {b:.2f}^N"
        ),
    }


# ── Axiom count resolution ──────────────────────────────────────────────


# Regex to parse ontology_classification.md table rows.
# Columns: Ontology | Original Axioms | Original Classes | Original DL
#           | Cleanup Axioms | Cleanup Classes | Cleanup DL
#           | Inconsistent Axioms | Inconsistent Classes | Inconsistent DL
_CLASSIFICATION_RE = re.compile(
    r"^\|\s*(?P<name>\S+)\s*"
    r"\|\s*(?P<orig_axioms>\d*)\s*"
    r"\|\s*(?P<orig_classes>\d*)\s*"
    r"\|\s*[^|]*\s*"           # Original DL
    r"\|\s*(?P<clean_axioms>\d*)\s*"
    r"\|\s*(?P<clean_classes>\d*)\s*"
    r"\|\s*[^|]*\s*"           # Cleanup DL
    r"\|\s*(?P<incon_axioms>\d*)\s*"
    r"\|\s*(?P<incon_classes>\d*)\s*"
    r"\|\s*[^|]*\s*"           # Inconsistent DL
    r"\|",
)


def _parse_classification_md(path: Path) -> dict[str, int]:
    """Parse ``ontology_classification.md`` and return ``{name: inconsistent_axioms}``.

    Returns only entries that have a non-empty ``inconsistent_axioms`` value.
    """
    result: dict[str, int] = {}
    if not path.exists():
        return result

    text = path.read_text(encoding="utf-8")
    for match in _CLASSIFICATION_RE.finditer(text):
        name = match.group("name")
        incon_axioms_str = match.group("incon_axioms")
        if incon_axioms_str and incon_axioms_str.strip().isdigit():
            result[name] = int(incon_axioms_str.strip())
    return result


def load_axiom_counts(
    names: list[str] | None = None,
    inconsistent_dir: Path | None = None,
    cache_path: Path | None = None,
) -> dict[str, int]:
    """Load axiom counts for the given ontology names.

    The counts come from the actual inconsistent ontology files
    (via ``classify_ontology``), with caching to avoid re-running
    the expensive Java call on every invocation.

    Resolution order:
      1. **Cache file** — ``data/axiom_counts.json`` (persisted Java results)
      2. **Java classification** — calls ``ClassifyOntology`` on each
         ontology file in *inconsistent_dir* (expensive; results cached)
      3. **Classification markdown** — ``ontology_classification.md``
         (historical snapshot, lowest priority)

    Parameters
    ----------
    names
        List of ontology names to resolve.  If ``None``, returns all counts
        that can be resolved.
    inconsistent_dir
        Path to the ``inconsistent/`` ontology directory.  Required for
        the Java fallback (method 2).
    cache_path
        Path for the JSON cache file.  Defaults to ``data/axiom_counts.json``.

    Returns
    -------
    dict
        ``{ontology_name: axiom_count}`` for every name that could be resolved.
    """
    if cache_path is None:
        cache_path = AXIOM_CACHE_FILE

    # Ensure cache directory exists
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Try to get names to resolve
    if names is None:
        names_set: set[str] = set()
        if CLASSIFICATION_FILE.exists():
            names_set.update(_parse_classification_md(CLASSIFICATION_FILE).keys())
        if cache_path and cache_path.exists():
            try:
                names_set.update(json.loads(cache_path.read_text()).keys())
            except (json.JSONDecodeError, ValueError):
                pass
        names = sorted(names_set)

    result: dict[str, int] = {}

    # 1. Cache file (fast path — previously persisted Java results)
    cached: dict[str, int] = {}
    if cache_path and cache_path.exists():
        try:
            cached = {
                k: int(v)
                for k, v in json.loads(cache_path.read_text()).items()
            }
        except (json.JSONDecodeError, ValueError):
            cached = {}

    for name in names:
        if name in cached:
            result[name] = cached[name]

    # 2. Java classify_ontology on the actual inconsistent files
    #    (authoritative — runs on the ontology files as they are on disk)
    remaining = [n for n in names if n not in result]
    if remaining and inconsistent_dir is not None:
        for name in remaining:
            owl_path = inconsistent_dir / f"{name}.owl"
            if owl_path.exists():
                cls_result = classify_ontology(owl_path=owl_path)
                if cls_result is not None:
                    result[name] = cls_result.axioms
                    cached[name] = cls_result.axioms

        # Persist updated cache
        if cache_path:
            cache_path.write_text(
                json.dumps(cached, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    # 3. Classification markdown (historical fallback — lowest priority)
    remaining = [n for n in names if n not in result]
    if remaining and CLASSIFICATION_FILE.exists():
        md_counts = _parse_classification_md(CLASSIFICATION_FILE)
        for name in remaining:
            if name in md_counts:
                result[name] = md_counts[name]

    return result


# ── Report builders (Markdown / LaTeX) ──────────────────────────────────


def build_estimation_markdown(estimation: dict[str, Any]) -> list[str]:
    """Build the Complexity Estimation section for the Markdown report."""
    lines = [
        "# Complexity Estimation",
        "",
        "Estimating computational cost of the repair algorithm with respect "
        "to ontology size (axiom count).",
        "",
    ]

    data_points = estimation.get("data_points", [])
    if not data_points:
        lines.append("No estimation data available.")
        return lines

    lines.append("| Ontology | Axioms | Mean Runtime (s) |")
    lines.append("| --- | --- | --- |")
    for dp in data_points:
        lines.append(
            f"| {dp['ontology']} | {dp['axioms']} | {dp['mean_runtime_s']:.2f} |"
        )
    lines.append("")

    lines.append(f"**Polynomial Fit (log-log):** "
                 f"R² = {estimation['r2_poly']:.4f}, "
                 f"Estimated exponent k = {estimation['k']:.2f} "
                 f"→ {estimation['poly_equation']}")
    lines.append("")
    lines.append(f"**Exponential Fit (semi-log):** "
                 f"R² = {estimation['r2_exp']:.4f}, "
                 f"Estimated base b = {estimation['b']:.2f} "
                 f"→ {estimation['exp_equation']}")
    lines.append("")
    lines.append(f"**Conclusion:** {estimation['conclusion']}")
    lines.append("")

    return lines


def build_estimation_latex(estimation: dict[str, Any]) -> list[str]:
    """Build the Complexity Estimation section for the LaTeX report."""
    lines: list[str] = []
    data_points = estimation.get("data_points", [])
    if not data_points:
        return lines

    lines.extend([
        "% Auto-generated complexity estimation",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Runtime complexity estimation}",
        "  \\begin{tabular}{lcr}",
        "    \\toprule",
        "    Ontology & Axioms & Mean Runtime (s) \\\\",
        "    \\midrule",
    ])
    for dp in data_points:
        lines.append(
            f"    {dp['ontology']} & {dp['axioms']} & "
            f"{dp['mean_runtime_s']:.2f} \\\\"
        )
    lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
        "",
        f"Polynomial fit: R² = {estimation['r2_poly']:.4f}, "
        f"k = {estimation['k']:.2f} "
        f"→ ${estimation['poly_equation']}$",
        "",
        f"Exponential fit: R² = {estimation['r2_exp']:.4f}, "
        f"b = {estimation['b']:.2f} "
        f"→ ${estimation['exp_equation']}$",
        "",
        f"Conclusion: {estimation['conclusion']}",
        "",
    ])
    return lines


# ── Helpers ─────────────────────────────────────────────────────────────


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "best_fit": "none",
        "r2_poly": float("nan"),
        "r2_exp": float("nan"),
        "k": float("nan"),
        "b": float("nan"),
        "data_points": [],
        "n_ontologies": 0,
        "poly_equation": "N/A",
        "exp_equation": "N/A",
        "conclusion": reason,
    }


# ── Legacy entry point (backward compatible) ────────────────────────────


def run_estimation() -> None:
    """Legacy standalone estimation using hardcoded data points.

    Prints results to stdout.  Kept for backward compatibility.
    """
    # Ignoring bctt and co-wheat as outliers.
    N = np.array([82, 91, 109, 127, 176, 242])
    T = np.array([1656.16, 2391.02, 2834.72, 6624.72, 15546.08, 57622.5])

    log_N = np.log(N)
    log_T = np.log(T)

    # 1. Test Polynomial: Log-Log fit
    slope_poly, intercept_poly, r_poly, _, _ = linregress(log_N, log_T)
    r2_poly = r_poly ** 2

    # 2. Test Exponential: Semi-Log fit
    slope_exp, intercept_exp, r_exp, _, _ = linregress(N, log_T)
    r2_exp = r_exp ** 2

    print(f"Polynomial Fit (Log-Log): R² = {r2_poly:.4f}, Estimated Exponent (k) = {slope_poly:.2f}")
    print(f"Exponential Fit (Semi-Log): R² = {r2_exp:.4f}, Estimated Base (b) = {np.exp(slope_exp):.2f}")

    if r2_poly > r2_exp:
        print(f"Evidence favors Polynomial: O(N^{round(slope_poly)})")
    else:
        print(f"Evidence favors Exponential: O({np.exp(slope_exp):.2f}^N)")
