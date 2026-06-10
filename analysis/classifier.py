"""Classify all ontologies across original/cleanup/inconsistent folders.

Scans the three ontology folders dynamically, runs
``ClassifyOntology`` from the shaded JAR on each
ontology file, and produces a single markdown table with axioms count,
class (concept) count, and DL language per folder.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from analysis.ontologyutils_service import classify_ontology

# ── Paths ──
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGIES_DIR = PACKAGE_ROOT / "ontologies"

FOLDER_NAMES = ["original", "cleanup", "inconsistent"]
FOLDER_DISPLAY = ["Original", "Cleanup", "Inconsistent"]

DEFAULT_JAVA_MEM = "-Xms1g -Xmx8g -Xss8m"
DEFAULT_TIMEOUT = 120  # seconds per ontology


# ── Helpers ──────────────────────────────────────────────────────────────


def discover_ontology_names() -> set[str]:
    """Return the union of all ontology stems across the three folders."""
    names: set[str] = set()
    for folder_name in FOLDER_NAMES:
        folder = ONTOLOGIES_DIR / folder_name
        if folder.is_dir():
            for owl in folder.glob("*.owl"):
                names.add(owl.stem)
    return names


def classify_all(java_mem: str, timeout: int,
                 verbose: bool) -> dict[str, dict[str, dict | None]]:
    """Classify every ontology in every folder.

    Returns a nested dict::
        result[ontology_name][folder_name] = data | None

    where ``data`` is a dict with keys ``axioms``, ``classes``, ``dl_languages``
    or ``None`` if the file did not exist or Java failed.
    """
    names = sorted(discover_ontology_names())
    result: dict[str, dict[str, dict | None]] = {
        name: {} for name in names
    }

    for folder_name in FOLDER_NAMES:
        folder = ONTOLOGIES_DIR / folder_name
        if not folder.is_dir():
            continue

        for name in names:
            owl_path = folder / f"{name}.owl"
            if not owl_path.exists():
                result[name][folder_name] = None
                continue

            if verbose:
                print(f"  [{folder_name}] {name} ...", file=sys.stderr,
                      flush=True)

            cls_result = classify_ontology(
                owl_path=owl_path,
                java_mem=java_mem,
                timeout=timeout,
            )
            if cls_result is not None:
                result[name][folder_name] = {
                    "axioms": cls_result.axioms,
                    "classes": cls_result.classes,
                    "dl_languages": cls_result.dl_languages,
                }
            else:
                result[name][folder_name] = None
                if verbose:
                    print(f"    -> FAILED (marked as -1)", file=sys.stderr,
                          flush=True)

    return result


def cell_value(data: dict | None, key: str) -> str:
    """Return the table cell string for a given data dict and key.

    - ``None`` (file not present) → blank
    - data present but parse failed → ``-1``
    - data present with value → string representation
    """
    if data is None:
        return ""
    val = data.get(key)
    if val is None:
        return "-1"
    return str(val)


def build_table(all_data: dict[str, dict[str, dict | None]]) -> str:
    """Assemble the markdown table from classified data."""
    names = sorted(all_data.keys())

    # Header
    cols = ["Ontology"]
    for display in FOLDER_DISPLAY:
        cols.append(f"{display} Axioms")
        cols.append(f"{display} Classes")
        cols.append(f"{display} DL")

    header = "| " + " | ".join(cols) + " |"
    separator = "| " + " | ".join("-" * len(c) for c in cols) + " |"

    lines = [header, separator]

    # Rows
    for name in names:
        row_data = all_data[name]
        row = [name]
        for folder_name in FOLDER_NAMES:
            d = row_data.get(folder_name)
            row.append(cell_value(d, "axioms"))
            row.append(cell_value(d, "classes"))
            row.append(cell_value(d, "dl_languages"))
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines) + "\n"


# ── Pipeline ─────────────────────────────────────────────────────────────


def run_classification(args: argparse.Namespace) -> None:
    """Run the classification pipeline with the given parsed arguments."""
    if args.verbose:
        print(f"Output: {args.output}", file=sys.stderr)
        print(f"Memory: {args.java_mem}", file=sys.stderr)
        print(f"Timeout: {args.timeout}s", file=sys.stderr)
        print(file=sys.stderr)

    # Classify all ontologies
    t0 = time.perf_counter()
    all_data = classify_all(args.java_mem, args.timeout, args.verbose)
    elapsed = time.perf_counter() - t0

    # Build and write table
    table = build_table(all_data)
    args.output.write_text(table, encoding="utf-8")

    # Summary
    names = sorted(all_data.keys())
    total_cells = len(names) * len(FOLDER_NAMES)
    present = sum(
        1 for row in all_data.values()
        for d in row.values()
        if d is not None
    )
    java_ok = sum(
        1 for row in all_data.values()
        for d in row.values()
        if d is not None and "axioms" in d
    )

    if args.verbose:
        print(file=sys.stderr)
        absent = total_cells - present
        java_failed = present - java_ok
        print(f"Ontologies:           {len(names)}", file=sys.stderr)
        print(f"Folders:              {len(FOLDER_NAMES)}", file=sys.stderr)
        print(f"  Files present:      {present}", file=sys.stderr)
        print(f"  Files absent:       {absent}", file=sys.stderr)
        print(f"  Java OK:            {java_ok}", file=sys.stderr)
        print(f"  Java failed (-1):   {java_failed}", file=sys.stderr)
        print(f"Elapsed:              {elapsed:.1f}s", file=sys.stderr)
        print(file=sys.stderr)

    print(f"Written: {args.output}", file=sys.stderr)
