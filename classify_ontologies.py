#!/usr/bin/env python3
"""Classify all ontologies across original/cleanup/inconsistent folders.

Scans the three ontology folders dynamically, runs
``ClassifyOntology`` from the shaded JAR on each
ontology file, and produces a single markdown table with axioms count,
class (concept) count, and DL language per folder.

Usage:
    python classify_ontologies.py [--output TABLE.md]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ──
SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
ONTOLOGIES_DIR = SCRIPT_DIR / "ontologies"

FOLDER_NAMES = ["original", "cleanup", "inconsistent"]
FOLDER_DISPLAY = ["Original", "Cleanup", "Inconsistent"]

DEFAULT_JAVA_MEM = "-Xms1g -Xmx8g -Xss8m"
DEFAULT_TIMEOUT = 120  # seconds per ontology


# ── Helpers ──────────────────────────────────────────────────────────────


def find_shaded_jar() -> Path:
    """Return the latest shaded-ontologyutils-*.jar from the lib directory."""
    candidates = sorted(LIB_DIR.glob("shaded-ontologyutils-*.jar"))
    if not candidates:
        raise FileNotFoundError(
            f"Could not find shaded-ontologyutils-*.jar under {LIB_DIR}. "
            "Run 'mvn package' first to build the shaded JAR."
        )
    return candidates[-1]


def discover_ontology_names() -> set[str]:
    """Return the union of all ontology stems across the three folders."""
    names: set[str] = set()
    for folder_name in FOLDER_NAMES:
        folder = ONTOLOGIES_DIR / folder_name
        if folder.is_dir():
            for owl in folder.glob("*.owl"):
                names.add(owl.stem)
    return names


def run_classify(jar: Path, owl_path: Path, java_mem: str,
                 timeout: int) -> dict | None:
    """Run ``ClassifyOntology`` on one ontology and return parsed data.

    Returns a dict with keys ``axioms``, ``classes``, ``dl_languages``
    on success, or ``None`` if the Java process fails or the output
    cannot be parsed.
    """
    cmd = (
        ["java"]
        + java_mem.split()
        + ["-cp", str(jar), "www.ontologyutils.apps.ClassifyOntology",
           str(owl_path)]
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None

    if proc.returncode != 0:
        return None

    return parse_output(proc.stdout)


def parse_output(stdout: str) -> dict | None:
    """Extract axioms, class count, and DL languages from Java stdout.

    Uses regex so the parse order is independent of any warning/problem
    lines that may be interspersed.
    """
    axioms_match = re.search(r"Axioms:\s*(\d+)", stdout)
    classes_match = re.search(r"Concept names:\s*(\d+)", stdout)
    dl_match = re.search(r"DL languages:\s*(.*?)(?:\n|$)", stdout)

    if not (axioms_match and classes_match and dl_match):
        return None

    dl_str = dl_match.group(1).strip().rstrip(";").strip()
    return {
        "axioms": int(axioms_match.group(1)),
        "classes": int(classes_match.group(1)),
        "dl_languages": dl_str,
    }


def classify_all(jar: Path, java_mem: str, timeout: int,
                 verbose: bool) -> dict[str, dict[str, dict | None]]:
    """Classify every ontology in every folder.

    Returns a nested dict::
        result[ontology_name][folder_name] = data | None

    where ``data`` is the dict from ``run_classify`` and ``None`` means
    the file did not exist (or Java failed; distinguish via a separate
    check below).
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
                # File not present → store None (rendered as blank)
                result[name][folder_name] = None
                continue

            if verbose:
                print(f"  [{folder_name}] {name} ...", file=sys.stderr,
                      flush=True)

            data = run_classify(jar, owl_path, java_mem, timeout)
            result[name][folder_name] = data

            if data is None and verbose:
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


# ── CLI ──────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify all ontologies across original/cleanup/inconsistent "
            "folders and produce a markdown table."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s -o my_table.md\n"
            "  %(prog)s --verbose --java-mem '-Xms2g -Xmx8g'\n"
        ),
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=SCRIPT_DIR / "ontology_classification.md",
        help="Output markdown file path (default: %(default)s)",
    )
    parser.add_argument(
        "--java-mem",
        type=str,
        default=DEFAULT_JAVA_MEM,
        help=(
            "JVM memory and stack options "
            "(default: '%(default)s')"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            "Timeout per ontology in seconds "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print progress to stderr.",
    )
    return parser.parse_args(argv)


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()

    # Validate shaded JAR
    try:
        jar = find_shaded_jar()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"JAR:    {jar}", file=sys.stderr)
        print(f"Output: {args.output}", file=sys.stderr)
        print(f"Memory: {args.java_mem}", file=sys.stderr)
        print(f"Timeout: {args.timeout}s", file=sys.stderr)
        print(file=sys.stderr)

    # Classify all ontologies
    t0 = time.perf_counter()
    all_data = classify_all(jar, args.java_mem, args.timeout, args.verbose)
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
    failed = sum(
        1 for row in all_data.values()
        for d in row.values()
        if d is None
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


if __name__ == "__main__":
    main()
