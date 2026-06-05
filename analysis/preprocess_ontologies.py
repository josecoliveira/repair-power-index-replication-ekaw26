"""Pre-process ontologies from the ekaw26 test resources.

Pipeline for each ontology name:
  1. CleanupOntology:  original/{name}.owl  →  cleanup/{name}.owl
  2. MakeInconsistent:  cleanup/{name}.owl  →  inconsistent/{name}.owl

Usage:
    python preprocess_ontologies.py [options] [ontology_name ...]

If no ontology names are given, all .owl files found in the original/
directory are processed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ──
# Script lives at: ontologyutils/repair-power-index-replication-ekaw26/analysis/preprocess_ontologies.py
# The repo root is ontologyutils/ (three levels up from the script).
SCRIPT_DIR = Path(__file__).resolve().parent
ANALYSIS_DIR = SCRIPT_DIR  # repair-power-index-replication-ekaw26/analysis/
REPLICATION_DIR = ANALYSIS_DIR.parent  # repair-power-index-replication-ekaw26/
REPO_ROOT = REPLICATION_DIR.parent  # ontologyutils/

LIB_DIR = REPO_ROOT / "lib"
EKAW26_BASE = REPO_ROOT / "src" / "test" / "resources" / "ekaw26"
ORIGINAL_DIR = EKAW26_BASE / "original"
CLEANUP_DIR = EKAW26_BASE / "cleanup"
INCONSISTENT_DIR = EKAW26_BASE / "inconsistent"

DEFAULT_JAVA_MEM = "-Xms1g -Xmx8g -Xss8m"


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


def log(msg: str, verbose: bool = False) -> None:
    """Print a message to stderr."""
    print(msg, file=sys.stderr, flush=True)


def run_java(
    main_class: str,
    args: list[str],
    java_mem: str,
    verbose: bool,
    description: str,
) -> tuple[bool, str]:
    """Run a Java main class via the shaded JAR and return (success, stderr_output)."""
    jar = find_shaded_jar()
    cmd = (
        ["java"]
        + java_mem.split()
        + ["-cp", str(jar), main_class]
        + args
    )
    if verbose:
        log(f"  [cmd] {' '.join(cmd)}")

    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - start

    # The Java apps print progress/debug to stderr; stdout is usually empty.
    stderr_out = result.stderr.strip()
    if verbose and stderr_out:
        for line in stderr_out.splitlines():
            log(f"    {line}")

    if result.returncode != 0:
        log(f"  [ERROR] {description} failed (exit {result.returncode}, {elapsed:.1f}s)")
        if result.stderr:
            log(f"    stderr: {result.stderr.strip()}")
        if result.stdout:
            log(f"    stdout: {result.stdout.strip()}")
        return False, stderr_out

    if verbose:
        log(f"  [OK] {description} completed in {elapsed:.1f}s")
    return True, stderr_out


def list_original_ontologies() -> list[str]:
    """Return ontology names (without .owl) from the original/ directory."""
    names = sorted(p.stem for p in ORIGINAL_DIR.glob("*.owl"))
    if not names:
        log(f"[WARN] No .owl files found in {ORIGINAL_DIR}")
    return names


# ── Main ─────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-process ontologies: clean up then make inconsistent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s bctt elig\n"
            "  %(prog)s --verbose co-wheat\n"
            "  %(prog)s --dry-run\n"
            "  %(prog)s --force\n"
        ),
    )
    parser.add_argument(
        "ontologies",
        nargs="*",
        metavar="NAME",
        help="Ontology name(s) without .owl extension (e.g. bctt co-wheat). "
             "If omitted, all ontologies from original/ are processed.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed progress and Java subprocess output.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the operations that would be performed without executing them.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process ontologies even if the output files already exist.",
    )
    parser.add_argument(
        "--java-mem",
        default=DEFAULT_JAVA_MEM,
        help=f"JVM memory options (default: '{DEFAULT_JAVA_MEM}').",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    # ── Resolve ontology names ──
    if args.ontologies:
        names = args.ontologies
    else:
        names = list_original_ontologies()
        if not names:
            log("[ERROR] No ontology names given and no .owl files found in original/.")
            sys.exit(1)

    log(f"Ontologies to process ({len(names)}): {', '.join(names)}")
    log(f"  Original dir:      {ORIGINAL_DIR}")
    log(f"  Cleanup dir:       {CLEANUP_DIR}")
    log(f"  Inconsistent dir:  {INCONSISTENT_DIR}")

    # Ensure output directories exist
    CLEANUP_DIR.mkdir(parents=True, exist_ok=True)
    INCONSISTENT_DIR.mkdir(parents=True, exist_ok=True)

    # Validate the shaded JAR exists (fail fast)
    try:
        jar = find_shaded_jar()
        log(f"  Shaded JAR:        {jar}")
    except FileNotFoundError as e:
        log(f"[ERROR] {e}")
        sys.exit(1)

    if args.dry_run:
        log("\n[Dry run mode — no commands will be executed]")
    log("")

    # ── Pipeline counters ──
    total = len(names)
    ok_cleanup = 0
    ok_inconsistent = 0
    skipped_cleanup = 0
    skipped_inconsistent = 0
    failed = []

    for idx, name in enumerate(names, start=1):
        log(f"[{idx}/{total}] Processing '{name}' ...")

        original_file = ORIGINAL_DIR / f"{name}.owl"
        cleanup_file = CLEANUP_DIR / f"{name}.owl"
        inconsistent_file = INCONSISTENT_DIR / f"{name}.owl"

        if not original_file.exists():
            log(f"  [SKIP] Original file not found: {original_file}")
            failed.append(name)
            continue

        # ── Step 1: Cleanup ──
        if cleanup_file.exists() and not args.force:
            log(f"  [SKIP] Cleanup output already exists: {cleanup_file.name} (use --force to redo)")
            skipped_cleanup += 1
        else:
            if args.dry_run:
                log(f"  [DRY-RUN] Would run: CleanupOntology -n -o {cleanup_file} {original_file}")
                ok_cleanup += 1
            else:
                success, _ = run_java(
                    main_class="www.ontologyutils.apps.CleanupOntology",
                    args=["-n", "-o", str(cleanup_file), str(original_file)],
                    java_mem=args.java_mem,
                    verbose=args.verbose,
                    description=f"CleanupOntology({name})",
                )
                if success:
                    ok_cleanup += 1
                else:
                    failed.append(name)
                    continue

        # ── Step 2: Make inconsistent ──
        if inconsistent_file.exists() and not args.force:
            log(f"  [SKIP] Inconsistent output already exists: {inconsistent_file.name} (use --force to redo)")
            skipped_inconsistent += 1
        else:
            makeinc_flags = [
                "--normalize", "--basic-cache", "--strict-sroiq",
                "--strict-simple-roles", "--simple-ria-weakening", "--strict-owl2",
            ]
            if args.dry_run:
                log(f"  [DRY-RUN] Would run: MakeInconsistent {' '.join(makeinc_flags)} -o {inconsistent_file} {cleanup_file}")
                ok_inconsistent += 1
            else:
                success, _ = run_java(
                    main_class="www.ontologyutils.apps.MakeInconsistent",
                    args=makeinc_flags + ["-o", str(inconsistent_file), str(cleanup_file)],
                    java_mem=args.java_mem,
                    verbose=args.verbose,
                    description=f"MakeInconsistent({name})",
                )
                if success:
                    ok_inconsistent += 1
                else:
                    failed.append(name)
                    continue

        log("")

    # ── Summary ──
    log("=" * 50)
    log("SUMMARY")
    log("=" * 50)
    if args.dry_run:
        log(f"  Would process:        {total} ontologies")
        log(f"  Would clean up:       {ok_cleanup}")
        log(f"  Would make inconsistent: {ok_inconsistent}")
    else:
        log(f"  Total ontologies:     {total}")
        log(f"  Cleanup succeeded:    {ok_cleanup}")
        log(f"  Cleanup skipped:      {skipped_cleanup}")
        log(f"  Inconsistent succeeded: {ok_inconsistent}")
        log(f"  Inconsistent skipped:   {skipped_inconsistent}")
        if failed:
            log(f"  Failed ontologies:    {len(failed)} -> {', '.join(failed)}")
        else:
            log(f"  All ontologies processed successfully!")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
