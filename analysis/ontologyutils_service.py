"""Service layer for running ontologyutils Java CLI apps.

This module is the **sole bridge** between Python and the shaded JAR.
It handles:
- JAR discovery and resolution
- Subprocess invocation (both captured and streaming)
- CLI argument construction for each ontologyutils Java app
- Output parsing and structured result types

If the JAR changes (class names, flags, output format), only this
module needs to be updated. The rest of ``analysis/`` is Java-agnostic.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
import sys

# ── JAR discovery ──────────────────────────────────────────────────────

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = PACKAGE_ROOT / "lib"


def find_shaded_jar(lib_dir: Path | None = None) -> Path:
    """Return the latest shaded-ontologyutils-*.jar from the lib directory."""
    lib = lib_dir or LIB_DIR
    candidates = sorted(lib.glob("shaded-ontologyutils-*.jar"))
    if not candidates:
        raise FileNotFoundError(
            f"Could not find shaded-ontologyutils-*.jar under {lib}. "
            "Run 'mvn package' first to build the shaded JAR."
        )
    return candidates[-1]


# ── Low-level execution helpers ────────────────────────────────────────


def _build_java_cmd(main_class: str, args: list[str],
                    java_mem: str, jar: Path) -> list[str]:
    return ["java"] + java_mem.split() + ["-cp", str(jar), main_class] + args


def _run_captured(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run a command with captured stdout/stderr and optional timeout."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # Return a placeholder so callers can check returncode == -1
        result = subprocess.CompletedProcess(cmd, -1, "", "")
        result.timed_out = True  # type: ignore[attr-defined]
        return result


def _run_streaming(cmd: list[str], verbose: bool = False,
                   ontology_name: str = "",
                   timeout: int | None = None) -> tuple[bool, str]:
    """Run a command with streaming stderr output.

    When *timeout* is set (seconds), the subprocess is killed if it
    exceeds that limit.  Returns ``(success, full_stderr)``.
    """
    if verbose:
        prefix = f"  [{ontology_name}] " if ontology_name else "  "
        print(f"{prefix}$ {' '.join(cmd)}", file=sys.stderr, flush=True)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []

    def _read_stderr() -> None:
        with process.stderr:
            for line in iter(process.stderr.readline, ""):
                line = line.rstrip("\n")
                if line:
                    if verbose:
                        prefix = f"  [{ontology_name}] " if ontology_name else "  "
                        print(f"{prefix}{line}", file=sys.stderr, flush=True)
                    stderr_lines.append(line)

    reader = threading.Thread(target=_read_stderr, daemon=True)
    reader.start()

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=2)
        full_stderr = "\n".join(stderr_lines)
        if full_stderr:
            full_stderr += "\n"
        full_stderr += "[TIMEOUT]"
        return False, full_stderr

    reader.join()
    full_stderr = "\n".join(stderr_lines)

    if verbose and process.returncode != 0:
        prefix = f"  [{ontology_name}] " if ontology_name else "  "
        print(f"{prefix}exit code {process.returncode}", file=sys.stderr, flush=True)

    return process.returncode == 0, full_stderr


# ── SingleTrialExperiment ──────────────────────────────────────────────


@dataclass
class TrialResult:
    """Result from running ``SingleTrialExperiment``."""
    returncode: int
    stdout: str   # raw stdout text (for logging)
    payload: dict  # parsed JSON from stdout
    stderr: str


def run_single_trial_experiment(
    ontology_path: Path,
    seed: int,
    run_id: str,
    java_mem: str = "-Xms1g -Xmx8g -Xss8m",
    lib_dir: Path | None = None,
    removal_timeout: int = 300,
    weakening_timeout: int = 300,
    power_index_timeout: int = 300,
    make_inconsistent_timeout: int = 300,
) -> TrialResult:
    """Run ``SingleTrialExperiment`` and return the parsed JSON result."""
    jar = find_shaded_jar(lib_dir)
    args = [
        "--ontology", str(ontology_path),
        "--seed", str(seed),
        "--run-id", str(run_id),
        "--removal-timeout-secs", str(removal_timeout),
        "--weakening-timeout-secs", str(weakening_timeout),
        "--power-index-timeout-secs", str(power_index_timeout),
        "--make-inconsistent-timeout-secs", str(make_inconsistent_timeout),
    ]
    cmd = _build_java_cmd(
        "www.ontologyutils.apps.SingleTrialExperiment",
        args, java_mem, jar,
    )
    proc = _run_captured(cmd)
    raw_stdout = proc.stdout or ""

    payload: dict = {}
    if proc.returncode >= 0 and raw_stdout:
        try:
            payload = json.loads(raw_stdout.strip() or "{}")
        except json.JSONDecodeError:
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    return TrialResult(
        returncode=proc.returncode,
        stdout=raw_stdout,
        payload=payload,
        stderr=proc.stderr,
    )


# ── CleanupOntology ────────────────────────────────────────────────────


def run_cleanup_ontology(
    input_path: Path,
    output_path: Path,
    java_mem: str = "-Xms1g -Xmx8g -Xss8m",
    lib_dir: Path | None = None,
    verbose: bool = False,
    ontology_name: str = "",
) -> bool:
    """Run ``CleanupOntology``, return True on success."""
    jar = find_shaded_jar(lib_dir)
    args = ["-n", "-o", str(output_path), str(input_path)]
    cmd = _build_java_cmd(
        "www.ontologyutils.apps.CleanupOntology",
        args, java_mem, jar,
    )
    success, _ = _run_streaming(cmd, verbose=verbose, ontology_name=ontology_name)
    return success


# ── MakeInconsistent ───────────────────────────────────────────────────


def run_make_inconsistent(
    input_path: Path,
    output_path: Path,
    java_mem: str = "-Xms1g -Xmx8g -Xss8m",
    lib_dir: Path | None = None,
    verbose: bool = False,
    ontology_name: str = "",
    timeout: int | None = None,
) -> bool:
    """Run ``MakeInconsistent`` with the paper's flags, return True on success.

    When *timeout* is set (seconds), the Java subprocess is killed if it
    exceeds the limit.
    """
    jar = find_shaded_jar(lib_dir)
    args = [
        "--normalize",
        "--basic-cache",
        "--strict-sroiq",
        "--strict-simple-roles",
        "--simple-ria-weakening",
        "--strict-owl2",
        "--verbose",
        "-o", str(output_path),
        str(input_path),
    ]
    cmd = _build_java_cmd(
        "www.ontologyutils.apps.MakeInconsistent",
        args, java_mem, jar,
    )
    success, _ = _run_streaming(cmd, verbose=verbose, ontology_name=ontology_name,
                                timeout=timeout)
    return success


# ── ClassifyOntology ───────────────────────────────────────────────────


@dataclass
class ClassificationResult:
    """Result from running ``ClassifyOntology``."""
    axioms: int
    classes: int
    dl_languages: str


_CLASSIFY_RE_AXIOMS = re.compile(r"Axioms:\s*(\d+)")
_CLASSIFY_RE_CLASSES = re.compile(r"Concept names:\s*(\d+)")
_CLASSIFY_RE_DL = re.compile(r"DL languages:\s*(.*?)(?:\n|$)")


def _parse_classify_output(stdout: str) -> ClassificationResult | None:
    """Parse ``ClassifyOntology`` stdout into a ``ClassificationResult``."""
    axioms_match = _CLASSIFY_RE_AXIOMS.search(stdout)
    classes_match = _CLASSIFY_RE_CLASSES.search(stdout)
    dl_match = _CLASSIFY_RE_DL.search(stdout)

    if not (axioms_match and classes_match and dl_match):
        return None

    dl_str = dl_match.group(1).strip().rstrip(";").strip()
    return ClassificationResult(
        axioms=int(axioms_match.group(1)),
        classes=int(classes_match.group(1)),
        dl_languages=dl_str,
    )


def classify_ontology(
    owl_path: Path,
    java_mem: str = "-Xms1g -Xmx8g -Xss8m",
    lib_dir: Path | None = None,
    timeout: int = 120,
) -> ClassificationResult | None:
    """Run ``ClassifyOntology``, return parsed result or ``None`` on failure."""
    jar = find_shaded_jar(lib_dir)
    args = [str(owl_path)]
    cmd = _build_java_cmd(
        "www.ontologyutils.apps.ClassifyOntology",
        args, java_mem, jar,
    )
    proc = _run_captured(cmd, timeout=timeout)

    if getattr(proc, "timed_out", False):
        return None
    if proc.returncode != 0:
        return None

    return _parse_classify_output(proc.stdout)
