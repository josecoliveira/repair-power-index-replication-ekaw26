#!/usr/bin/env python3
"""Build the ALC corpus *without* the cleanup stage.

The paper's preprocessing pipeline cleans every ontology before filtering for
ALC. Cleanup is lossy — it drops annotations, data properties, declarations and
every axiom outside SROIQ — so this script builds an equivalent corpus straight
from the raw ontologies:

    ontologies/original/{name}.owl  ->  ontologies/alc-nocleanup/{name}.owl
                                    ->  ontologies/inconsistent-nocleanup/{name}.owl

* Stage 1 copies the source file verbatim, so labels and all other axioms of the
  original are preserved exactly (nothing is re-serialised at this point).
* Stage 2 runs ``MakeInconsistent`` with the same flags the paper uses.
* Stage 3 re-checks every result with ``CheckConsistency --basic`` and reports
  anything that is still consistent, plus a per-ontology report as JSON.

The set of ontologies is taken from a classification manifest produced by
:mod:`cli.classify_original_corpus`. Ontologies whose ``owl:imports`` could not
be resolved are skipped by default (the original pipeline dropped them too,
because cleanup failed on them); pass ``--include-truncated`` to keep them.

Usage:
    python -m cli.build_nocleanup_corpus --dry-run
    python -m cli.build_nocleanup_corpus
    python -m cli.build_nocleanup_corpus --only-names BCTT,HOM
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

# Allow running as ``python cli/build_nocleanup_corpus.py`` (without ``-m``).
if __package__ in (None, ""):
    _proj_root = str(Path(__file__).resolve().parent.parent)
    if _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)

from analysis.ontologyutils_service import check_consistency, run_make_inconsistent

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGIES_DIR = PACKAGE_ROOT / "ontologies"

DEFAULT_MANIFEST = PACKAGE_ROOT / "original_classification.json"
DEFAULT_SOURCE_DIR = ONTOLOGIES_DIR / "original"
DEFAULT_ALC_DIR = ONTOLOGIES_DIR / "alc-nocleanup"
DEFAULT_INCONSISTENT_DIR = ONTOLOGIES_DIR / "inconsistent-nocleanup"
DEFAULT_REPORT = PACKAGE_ROOT / "nocleanup_corpus_report.json"

DEFAULT_JAVA_MEM = "-Xms1g -Xmx8g -Xss8m"
DEFAULT_BIG_JAVA_MEM = "-Xms2g -Xmx24g -Xss8m"
DEFAULT_BIG_THRESHOLD_MB = 40
DEFAULT_WORKERS = 4
DEFAULT_TIMEOUT = 900


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build ontologies/alc-nocleanup and "
            "ontologies/inconsistent-nocleanup from the raw originals."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --dry-run\n"
            "  %(prog)s --only-names BCTT,HOM --force\n"
            "  %(prog)s --include-truncated\n"
        ),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="Classification manifest (default: %(default)s)")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR,
                        help="Folder with the raw ontologies (default: %(default)s)")
    parser.add_argument("--alc-dir", type=Path, default=DEFAULT_ALC_DIR,
                        help="Output folder for the ALC copies (default: %(default)s)")
    parser.add_argument("--inconsistent-dir", type=Path, default=DEFAULT_INCONSISTENT_DIR,
                        help="Output folder for the inconsistent variants "
                             "(default: %(default)s)")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                        help="JSON report to write (default: %(default)s)")
    parser.add_argument("--include-truncated", action="store_true",
                        help="Also include ontologies whose owl:imports could not "
                             "be resolved (their content is then partial).")
    parser.add_argument("--only-names", type=str, default="",
                        help="Comma-separated subset of names to build.")
    parser.add_argument("--min-axioms", type=int, default=1,
                        help="Skip ontologies with fewer logical axioms than this "
                             "(default: %(default)s)")
    parser.add_argument("--max-mb", type=int, default=0,
                        help="Skip ontologies whose source file is larger than this "
                             "many MB (default: 0, no limit)")
    parser.add_argument("--java-mem", type=str, default=DEFAULT_JAVA_MEM,
                        help="JVM options for the small ontologies (default: '%(default)s')")
    parser.add_argument("--big-java-mem", type=str, default=DEFAULT_BIG_JAVA_MEM,
                        help="JVM options for the big ones, run one at a time "
                             f"(default: '{DEFAULT_BIG_JAVA_MEM}')")
    parser.add_argument("--big-threshold-mb", type=int, default=DEFAULT_BIG_THRESHOLD_MB,
                        help="Files at least this large are processed sequentially "
                             "(default: %(default)s)")
    parser.add_argument("-j", "--workers", type=int, default=DEFAULT_WORKERS,
                        help="Parallel workers (default: %(default)s)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="Timeout per ontology, in seconds (default: %(default)s)")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Do not re-check the results with CheckConsistency.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild outputs that already exist.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print the selected ontologies.")
    return parser.parse_args(argv)


def select_names(
    manifest: dict[str, dict[str, Any]],
    include_truncated: bool,
    only: str,
    min_axioms: int = 1,
) -> list[str]:
    """Names of the ALC-suitable ontologies to build, sorted alphabetically."""
    names = [
        name for name, entry in manifest.items()
        if entry.get("alc_suitable")
        and not entry.get("error")
        and (include_truncated or not entry.get("imports_missing"))
    ]
    if only:
        wanted = {n.strip() for n in only.split(",") if n.strip()}
        known = set(names)
        unknown = sorted(wanted - known)
        if unknown:
            print(f"warning: requested but not selected: {', '.join(unknown)}",
                  file=sys.stderr)
        names = [n for n in names if n in wanted]

    empty = sorted(n for n in names if (manifest[n].get("axioms") or 0) < min_axioms)
    if empty:
        print(f"skipping {len(empty)} ontologies with fewer than {min_axioms} "
              f"logical axioms: {', '.join(empty)}")
        names = [n for n in names if n not in empty]
    return sorted(names)


def write_report(path: Path, report: dict[str, dict[str, Any]]) -> None:
    """Write the per-ontology report, sorted by name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {name: report[name] for name in sorted(report)}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ordered, indent=2) + "\n")
    tmp.replace(path)


def copy_origins(
    names: list[str], source_dir: Path, alc_dir: Path, force: bool,
) -> dict[str, str]:
    """Stage 1: copy the raw ontologies (byte for byte) into *alc_dir*."""
    alc_dir.mkdir(parents=True, exist_ok=True)
    errors: dict[str, str] = {}
    for name in names:
        src = source_dir / f"{name}.owl"
        dst = alc_dir / f"{name}.owl"
        if not src.is_file():
            errors[name] = f"missing source file {src}"
            continue
        if dst.exists() and not force:
            continue
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            errors[name] = str(e)
    return errors


def _for_each(
    paths: list[Path],
    *,
    mem_small: str,
    mem_big: str,
    big_threshold: int,
    workers: int,
    progress: Progress,
    task: Any,
    work: Callable[[Path, str], None],
) -> None:
    """Run *work* on every path; big files one at a time, the rest in parallel."""
    big = [p for p in paths if p.stat().st_size >= big_threshold]
    small = [p for p in paths if p.stat().st_size < big_threshold]

    for path in big:
        progress.update(task, description=f"  {path.stem:<28}")
        work(path, mem_big)
        progress.advance(task)

    if small:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, p, mem_small): p for p in small}
            for future in concurrent.futures.as_completed(futures):
                path = futures[future]
                progress.update(task, description=f"  {path.stem:<28}")
                future.result()
                progress.advance(task)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if not args.manifest.is_file():
        print(f"no manifest at {args.manifest}; run "
              "`python -m cli.classify_original_corpus` first", file=sys.stderr)
        raise SystemExit(1)
    manifest = json.loads(args.manifest.read_text())
    names = select_names(manifest, args.include_truncated, args.only_names,
                         args.min_axioms)

    if args.max_mb > 0:
        limit = args.max_mb * 1024 * 1024
        oversized = sorted(
            n for n in names
            if (args.source_dir / f"{n}.owl").stat().st_size > limit
        )
        if oversized:
            print(f"skipping {len(oversized)} ontologies larger than {args.max_mb} MB: "
                  f"{', '.join(oversized)}", flush=True)
            names = [n for n in names if n not in oversized]

    if not names:
        print("no ALC-suitable ontologies selected", file=sys.stderr)
        raise SystemExit(1)

    truncated = [n for n in names if manifest[n].get("imports_missing")]
    print(f"{len(names)} ALC-suitable ontologies selected from {args.source_dir}", flush=True)
    if truncated:
        print(f"  {len(truncated)} of them only have their local axioms "
              f"(unresolved owl:imports): {', '.join(truncated)}")
    if args.dry_run:
        for name in names:
            entry = manifest[name]
            print(f"  {name:<24} axioms={entry['axioms']:<9} "
                  f"dl={entry['dl_languages'] or '-'}")
        print(f"would write {args.alc_dir} and {args.inconsistent_dir}")
        return

    report: dict[str, dict[str, Any]] = {
        name: {"consistency": "skipped", "seconds": 0.0, "error": ""}
        for name in names
    }

    # ── Stage 1: ALC copies ────────────────────────────────────────────
    print(f"\nstage 1/3: copying originals to {args.alc_dir.name}", flush=True)
    copy_errors = copy_origins(names, args.source_dir, args.alc_dir, args.force)
    for name, err in copy_errors.items():
        report[name]["error"] = err
        print(f"  FAILED {name}: {err}", file=sys.stderr, flush=True)
    copied = [n for n in names if n not in copy_errors]
    print(f"  {len(copied)} files in place", flush=True)

    # ── Stage 2: MakeInconsistent ────────────────────────────
    print(f"\nstage 2/3: MakeInconsistent -> {args.inconsistent_dir.name}", flush=True)
    args.inconsistent_dir.mkdir(parents=True, exist_ok=True)

    def make_inconsistent(path: Path, java_mem: str) -> None:
        output = args.inconsistent_dir / path.name
        if output.exists() and output.stat().st_size > 0 and not args.force:
            return
        started = time.monotonic()
        ok = run_make_inconsistent(
            input_path=path,
            output_path=output,
            java_mem=java_mem,
            timeout=args.timeout,
            ontology_name=path.stem,
        )
        report[path.stem]["seconds"] = round(time.monotonic() - started, 1)
        if not ok:
            report[path.stem]["error"] = "MakeInconsistent failed"
            print(f"  FAILED {path.stem} after {report[path.stem]['seconds']}s",
                  file=sys.stderr, flush=True)

    with Progress(TextColumn("  "), BarColumn(bar_width=None),
                  MofNCompleteColumn(), TimeElapsedColumn()) as progress:
        task = progress.add_task("", total=len(copied))
        _for_each([args.alc_dir / f"{n}.owl" for n in copied],
                  mem_small=args.java_mem, mem_big=args.big_java_mem,
                  big_threshold=args.big_threshold_mb * 1024 * 1024,
                  workers=args.workers, progress=progress, task=task,
                  work=make_inconsistent)

    built = [
        n for n in copied
        if not report[n]["error"]
        and (args.inconsistent_dir / f"{n}.owl").is_file()
    ]
    print(f"  {len(built)} inconsistent ontologies written", flush=True)
    # Checkpoint before the (long) verification stage so the build itself is
    # never lost if the run is interrupted.
    write_report(args.report, report)

    # ── Stage 3: verify ────────────────────────────────────────────────
    if args.skip_verify:
        print("\nstage 3/3: skipped", flush=True)
    else:
        print("\nstage 3/3: verifying with CheckConsistency --basic", flush=True)

        def verify(path: Path, java_mem: str) -> None:
            consistent = check_consistency(path, java_mem=java_mem,
                                           timeout=args.timeout)
            if consistent is None:
                report[path.stem]["consistency"] = "unknown"
            elif consistent:
                report[path.stem]["consistency"] = "consistent"
            else:
                report[path.stem]["consistency"] = "inconsistent"

        with Progress(TextColumn("  "), BarColumn(bar_width=None),
                      MofNCompleteColumn(), TimeElapsedColumn()) as progress:
            task = progress.add_task("", total=len(built))
            _for_each([args.inconsistent_dir / f"{n}.owl" for n in built],
                      mem_small=args.java_mem, mem_big=args.big_java_mem,
                      big_threshold=args.big_threshold_mb * 1024 * 1024,
                      workers=args.workers, progress=progress, task=task,
                      work=verify)

    # ── Report ─────────────────────────────────────────────────────────
    write_report(args.report, report)

    still_consistent = sorted(n for n in built if report[n]["consistency"] == "consistent")
    unknown = sorted(n for n in built if report[n]["consistency"] == "unknown")
    failed = sorted(n for n in names if report[n]["error"])

    print()
    print(f"alc-nocleanup          : {len(copied)} files")
    print(f"inconsistent-nocleanup : {len(built)} files")
    print(f"  inconsistent (ok)    : {len([n for n in built if report[n]['consistency'] == 'inconsistent'])}")
    print(f"  still consistent     : {len(still_consistent)}")
    print(f"  check inconclusive   : {len(unknown)}")
    print(f"failed (MakeInconsistent): {len(failed)}")
    if still_consistent:
        print(f"  still consistent: {', '.join(still_consistent)}")
    if unknown:
        print(f"  inconclusive    : {', '.join(unknown)}")
    if failed:
        print(f"  failed          : {', '.join(failed)}")
    print(f"\nreport written to {args.report}")


if __name__ == "__main__":
    main()
