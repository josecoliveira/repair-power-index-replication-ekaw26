#!/usr/bin/env python3
"""Classify a folder of (raw) ontologies and write a JSON manifest.

This is the tool used to find the ALC-suitable ontologies in
``ontologies/original/`` *without* running the cleanup stage first. For every
``*.owl`` file it runs ``ClassifyOntology`` and records the axiom count, the
number of concept names and the DL expressivity, plus whether the ontology is
ALC-suitable according to the same rule used by the preprocessing pipeline
(see :func:`analysis.preprocessor.is_alc_suitable`).

Two details matter when the input is a raw download rather than a cleaned
ontology:

* ``owl:imports`` cannot always be resolved (the IRIs are often unreachable).
  Such files are retried with ``-Dontologyutils.ignoreImports=true`` and the
  entry is flagged ``imports_missing``: the reported numbers then only cover
  the axioms contained in the file itself. The cleanup stage used to drop these
  ontologies, so they are reported separately rather than silently mixed in.
* Ontologies are processed biggest-first in two phases, so that the few very
  large ones get a generous heap one at a time instead of competing for memory
  with the many small ones.

The manifest is written incrementally (every ``--checkpoint-every`` ontologies)
and existing entries are skipped on restart, so an interrupted run can simply
be started again.

Usage:
    python -m cli.classify_original_corpus
    python -m cli.classify_original_corpus --ontologies-dir ontologies/alc
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path
from typing import Any

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

# Allow running as ``python cli/classify_original_corpus.py`` (without ``-m``).
if __package__ in (None, ""):
    _proj_root = str(Path(__file__).resolve().parent.parent)
    if _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)

from analysis.ontologyutils_service import classify_ontology
from analysis.preprocessor import is_alc_suitable

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

IGNORE_IMPORTS_FLAG = "-Dontologyutils.ignoreImports=true"

DEFAULT_INPUT = PACKAGE_ROOT / "ontologies" / "original"
DEFAULT_OUTPUT = PACKAGE_ROOT / "original_classification.json"
DEFAULT_JAVA_MEM = "-Xms1g -Xmx6g -Xss8m"
DEFAULT_BIG_JAVA_MEM = "-Xms2g -Xmx24g -Xss8m"
DEFAULT_BIG_THRESHOLD_MB = 40
DEFAULT_WORKERS = 4
DEFAULT_TIMEOUT = 900


def classify_one(
    path: Path, java_mem: str, timeout: int,
) -> dict[str, Any]:
    """Classify one ontology, retrying with imports ignored if it fails."""
    started = time.monotonic()
    entry: dict[str, Any] = {
        "axioms": None,
        "classes": None,
        "dl_languages": "",
        "alc_suitable": False,
        "imports_missing": False,
        "seconds": 0.0,
        "error": "",
    }

    result = classify_ontology(owl_path=path, java_mem=java_mem, timeout=timeout)
    if result is None:
        retry = classify_ontology(
            owl_path=path,
            java_mem=f"{java_mem} {IGNORE_IMPORTS_FLAG}",
            timeout=timeout,
        )
        if retry is None:
            entry["error"] = "classification failed"
            entry["seconds"] = round(time.monotonic() - started, 1)
            return entry
        entry["imports_missing"] = True
        result = retry

    entry["axioms"] = result.axioms
    entry["classes"] = result.classes
    entry["dl_languages"] = result.dl_languages
    entry["alc_suitable"] = is_alc_suitable(result.dl_languages)
    entry["seconds"] = round(time.monotonic() - started, 1)
    return entry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify every ontology in a folder into a JSON manifest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s --workers 6 --timeout 1800\n"
            "  %(prog)s --ontologies-dir ontologies/alc -o alc.json\n"
        ),
    )
    parser.add_argument(
        "--ontologies-dir", type=Path, default=DEFAULT_INPUT,
        help="Folder with the ontologies to classify (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT,
        help="JSON manifest to write (default: %(default)s)",
    )
    parser.add_argument(
        "--java-mem", type=str, default=DEFAULT_JAVA_MEM,
        help="JVM options for the small ontologies (default: '%(default)s')",
    )
    parser.add_argument(
        "--big-java-mem", type=str, default=DEFAULT_BIG_JAVA_MEM,
        help="JVM options for the big ones, run one at a time "
             f"(default: '{DEFAULT_BIG_JAVA_MEM}')",
    )
    parser.add_argument(
        "--big-threshold-mb", type=int, default=DEFAULT_BIG_THRESHOLD_MB,
        help="Files at least this large are classified sequentially "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "-j", "--workers", type=int, default=DEFAULT_WORKERS,
        help="Parallel workers for the small ontologies (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help="Timeout per ontology, in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--checkpoint-every", type=int, default=25,
        help="Write the manifest every N classified ontologies "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-classify ontologies that are already in the manifest.",
    )
    return parser.parse_args(argv)


def _load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        print(f"warning: ignoring unreadable manifest {path}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _save_manifest(path: Path, manifest: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {name: manifest[name] for name in sorted(manifest)}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ordered, indent=2) + "\n")
    tmp.replace(path)


def _needs_work(manifest: dict[str, dict[str, Any]], name: str) -> bool:
    """Whether *name* still has to be classified.

    Entries that failed are retried on the next run, so a bigger heap or a
    longer timeout can be used for the few ontologies that did not fit.
    """
    entry = manifest.get(name)
    return entry is None or bool(entry.get("error"))


def _print_summary(manifest: dict[str, dict[str, Any]]) -> None:
    ok = {n: e for n, e in manifest.items() if not e["error"]}
    failed = sorted(n for n, e in manifest.items() if e["error"])
    strict = sorted(n for n, e in ok.items() if e["alc_suitable"] and not e["imports_missing"])
    truncated = sorted(n for n, e in ok.items() if e["alc_suitable"] and e["imports_missing"])

    print()
    print(f"classified        : {len(ok)} / {len(manifest)}")
    print(f"ALC-suitable      : {len(strict)}")
    print(f"ALC-suitable but truncated by unresolvable imports: {len(truncated)}")
    print(f"rejected by filter: {len(ok) - len(strict) - len(truncated)}")
    print(f"failed            : {len(failed)}")
    if truncated:
        print(f"  truncated: {', '.join(truncated)}")
    if failed:
        print(f"  failed  : {', '.join(failed)}")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    files = sorted(args.ontologies_dir.glob("*.owl"))
    if not files:
        print(f"no *.owl files in {args.ontologies_dir}", file=sys.stderr)
        raise SystemExit(1)

    manifest = {} if args.force else _load_manifest(args.output)
    todo = [f for f in files if _needs_work(manifest, f.stem)]

    big_threshold = args.big_threshold_mb * 1024 * 1024
    todo.sort(key=lambda f: f.stat().st_size, reverse=True)
    big = [f for f in todo if f.stat().st_size >= big_threshold]
    small = [f for f in todo if f.stat().st_size < big_threshold]

    print(f"{len(files)} ontologies in {args.ontologies_dir}")
    if len(todo) != len(files):
        print(f"{len(files) - len(todo)} already in {args.output}")
    print(f"to classify: {len(big)} big (sequential) + {len(small)} small "
          f"({args.workers} workers)")

    pending = 0

    def record(path: Path, entry: dict[str, Any]) -> None:
        nonlocal pending
        manifest[path.stem] = entry
        if entry["error"]:
            print(f"  FAILED {path.stem}: {entry['error']}", file=sys.stderr)
        pending += 1
        if pending >= args.checkpoint_every:
            _save_manifest(args.output, manifest)
            pending = 0

    with Progress(
        TextColumn("  "),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("", total=len(todo))

        if big:
            print("big ontologies (one at a time, "
                  f"'{args.big_java_mem}'):")
            for path in big:
                progress.update(task, description=f"  {path.stem:<28}")
                record(path, classify_one(path, args.big_java_mem, args.timeout))
                progress.advance(task)

        if small:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {
                    pool.submit(classify_one, path, args.java_mem, args.timeout): path
                    for path in small
                }
                for future in concurrent.futures.as_completed(futures):
                    path = futures[future]
                    progress.update(task, description=f"  {path.stem:<28}")
                    record(path, future.result())
                    progress.advance(task)

    _save_manifest(args.output, manifest)
    print(f"manifest written to {args.output}")
    _print_summary(manifest)


if __name__ == "__main__":
    main()
