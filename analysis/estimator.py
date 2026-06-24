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

# ── Repair IDs for per-repair estimation (B1 excluded, matching analyzer) ─
_A_REPAIRS = ["A1", "A2", "A3"]
_B_REPAIRS = [f"B{i}" for i in range(2, 10)]
_REPAIR_IDS = _A_REPAIRS + _B_REPAIRS

# ── Package layout ──────────────────────────────────────────────────────

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = PACKAGE_ROOT / "data"
CLASSIFICATION_FILE = PACKAGE_ROOT / "ontology_classification.md"
AXIOM_CACHE_FILE = ANALYSIS_DIR / "axiom_counts.json"


# ── Public estimator API ────────────────────────────────────────────────


def _fit_model(N: np.ndarray, T: np.ndarray, label: str = "") -> dict[str, Any]:
    """Fit polynomial and exponential models to runtime-vs-axiom-count data.

    Parameters
    ----------
    N
        1-D array of axiom counts.
    T
        1-D array of mean runtimes (in the same unit, e.g. ms).
    label
        Optional label for the data (e.g. repair ID), used in error messages.

    Returns
    -------
    dict with keys: best_fit, r2_poly, r2_exp, k, b, data_points,
    n_ontologies, poly_equation, exp_equation, conclusion.
    """
    n = len(N)
    data_points: list[dict[str, Any]] = []
    for i in range(n):
        data_points.append({
            "axioms": int(N[i]),
            "mean_runtime": float(T[i]),
        })

    log_N = np.log(N)
    log_T = np.log(T)

    # Polynomial fit (log-log): T = a * N^k
    slope_poly, intercept_poly, r_poly, _, _ = linregress(log_N, log_T)
    r2_poly = float(r_poly ** 2)
    k = float(slope_poly)

    # Exponential fit (semi-log): T = a * b^N
    slope_exp, intercept_exp, r_exp, _, _ = linregress(N, log_T)
    r2_exp = float(r_exp ** 2)
    b = float(np.exp(slope_exp))

    best_fit = "polynomial" if r2_poly >= r2_exp else "exponential"

    return {
        "best_fit": best_fit,
        "r2_poly": r2_poly,
        "r2_exp": r2_exp,
        "k": k,
        "b": b,
        "data_points": data_points,
        "n_ontologies": n,
        "poly_equation": f"O(N^{k:.2f})",
        "exp_equation": f"O({b:.2f}^N)",
        "conclusion": (
            f"Evidence favors Polynomial: N^{k:.2f}"
            if best_fit == "polynomial"
            else f"Evidence favors Exponential: {b:.2f}^N"
        ),
    }


def estimate_complexity(
    runtime_df: pd.DataFrame,
    axiom_counts: dict[str, int] | None = None,
    inconsistent_dir: Path | None = None,
    repair_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Estimate computational complexity per repair from experimental runtime data.

    Parameters
    ----------
    runtime_df
        DataFrame from ``load_runtime_rows()``.  Must have columns
        ``ontology``, ``trial_status`` and ``{repair_id}_ms`` for each
        repair in *repair_ids*.
    axiom_counts
        Mapping ``{ontology_name: axiom_count}``.  If ``None`` or empty,
        the function tries to resolve axiom counts.
    inconsistent_dir
        Path to the ``inconsistent/`` ontology directory.  Used as a
        fallback when *axiom_counts* is not provided.
    repair_ids
        List of repair IDs to estimate (e.g. ``["A1", "A2", "B2"]``).
        Defaults to ``[A1, A2, A3, B2, ..., B9]``.

    Returns
    -------
    dict
        ``{repair_id: model_result, ...}`` where each model_result has
        keys: best_fit, r2_poly, r2_exp, k, b, data_points, n_ontologies,
        poly_equation, exp_equation, conclusion.
        An empty dict is returned if no data is available.
    """
    if repair_ids is None:
        repair_ids = _REPAIR_IDS

    if runtime_df.empty:
        return {}

    success_df = runtime_df[runtime_df["trial_status"].str.strip().str.lower() == "success"].copy()
    if success_df.empty:
        return {}

    # Resolve axiom counts once, shared across all repairs
    names: list[str] = sorted(success_df["ontology"].unique().tolist())
    if not axiom_counts:
        axiom_counts = load_axiom_counts(names, inconsistent_dir=inconsistent_dir)

    results: dict[str, Any] = {}
    for repair_id in repair_ids:
        col = f"{repair_id}_ms"
        if col not in success_df.columns:
            continue

        repair_df = success_df[success_df[col].notna()].copy()
        if repair_df.empty:
            continue

        # Group by ontology → mean runtime for this repair
        grouped = (
            repair_df.groupby("ontology")[col]
            .mean()
            .reset_index()
            .rename(columns={col: "mean_runtime"})
        )

        # Merge axiom counts
        grouped["axioms"] = grouped["ontology"].map(axiom_counts)
        valid = grouped.dropna(subset=["axioms"]).copy()
        if valid.empty:
            continue
        valid["axioms"] = valid["axioms"].astype(int)

        if len(valid) < 2:
            continue

        N: np.ndarray = valid["axioms"].to_numpy(dtype=float)
        T: np.ndarray = valid["mean_runtime"].to_numpy(dtype=float)

        results[repair_id] = _fit_model(N, T, label=repair_id)

    return results


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


def _repair_label(rid: str) -> str:
    """Return a human-readable label for a repair ID."""
    labels = {
        "A1": "Random removal",
        "A2": "Not-in-largest-MCS removal",
        "A3": "Default weakening",
        "B2": "in-MUS + Shapley",
        "B3": "in-MUS + Banzhaf",
        "B4": "Shapley + random",
        "B5": "Shapley + Shapley",
        "B6": "Shapley + Banzhaf",
        "B7": "Banzhaf + random",
        "B8": "Banzhaf + Shapley",
        "B9": "Banzhaf + Banzhaf",
    }
    return labels.get(rid, rid)


def build_estimation_markdown(estimation: dict[str, Any]) -> list[str]:
    """Build the Complexity Estimation section for the Markdown report.

    Parameters
    ----------
    estimation
        Dict keyed by repair_id, each value is a model result from
        ``_fit_model()``.
    """
    lines = [
        "# Complexity Estimation",
        "",
        "Per-repair polynomial and exponential fits of runtime vs axiom count.",
        "",
    ]

    if not estimation:
        lines.append("No estimation data available (need ≥2 ontologies with successful trials).")
        return lines

    # Sort repairs: A1, A2, A3, B2, ..., B9
    repair_order = {rid: i for i, rid in enumerate(_REPAIR_IDS)}
    sorted_repairs = sorted(estimation.keys(), key=lambda r: repair_order.get(r, 999))

    lines.append("| Repair | Best Fit | R² Poly | R² Exp | Equation |")
    lines.append("| --- | --- | --- | --- | --- |")
    for rid in sorted_repairs:
        m = estimation[rid]
        eq = m["poly_equation"] if m["best_fit"] == "polynomial" else m["exp_equation"]
        lines.append(
            f"| {_repair_label(rid)} | {m['best_fit']} "
            f"| {m['r2_poly']:.4f} | {m['r2_exp']:.4f} "
            f"| {eq} |"
        )
    lines.append("")
    lines.append(
        "*Polynomial: O(N^k) — Exponential: O(b^N). "
        "Best fit selected by higher R².*"
    )
    lines.append("")

    return lines


def build_estimation_latex(estimation: dict[str, Any]) -> list[str]:
    """Build the Complexity Estimation section for the LaTeX report."""
    lines: list[str] = []

    if not estimation:
        return lines

    repair_order = {rid: i for i, rid in enumerate(_REPAIR_IDS)}
    sorted_repairs = sorted(estimation.keys(), key=lambda r: repair_order.get(r, 999))

    lines.extend([
        "% Auto-generated complexity estimation",
        "\\begin{table}[ht]",
        "  \\centering",
        "  \\caption{Per-repair complexity estimation}",
        "  \\begin{tabular}{lcccc}",
        "    \\toprule",
        "    Repair & Best Fit & R² Poly & R² Exp & Equation \\\\",
        "    \\midrule",
    ])
    for rid in sorted_repairs:
        m = estimation[rid]
        eq = m["poly_equation"] if m["best_fit"] == "polynomial" else m["exp_equation"]
        lines.append(
            f"    {_repair_label(rid)} & {m['best_fit']} "
            f"& {m['r2_poly']:.4f} & {m['r2_exp']:.4f} "
            f"& ${eq}$ \\\\"
        )
    lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
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
