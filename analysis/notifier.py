"""Best-effort Telegram notifications for the experiment runner.

Configuration
-------------
Credentials are read from an optional ``.env`` file at the package root
(``repair-power-index-replication-ekaw26/.env``)::

    TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
    TELEGRAM_CHAT_ID=123456789

The file is parsed by hand (no third-party dependency) and its values
**override** anything already present in the process environment.

Contract
--------
Notifications are strictly best-effort. A missing token, an unreachable
network or a Telegram error must never abort or noticeably slow down an
experiment: every public entry point swallows exceptions and returns a
bool. A single warning is written to stderr the first time credentials are
missing, and the first time a send fails; after that the notifier stays
silent.

Only the experiment runner (``cli/run_trials.py``) uses this module; the
batch CLIs (preprocess / classify) intentionally do not notify.

Use ``cli/telegram_notify.py`` to discover the chat ID and to send a test
message.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "API_BASE",
    "ENV_PATH",
    "configure",
    "fetch_chat_ids",
    "format_duration",
    "get_credentials",
    "is_enabled",
    "load_dotenv",
    "notify",
    "notify_abort",
    "notify_exception",
    "notify_ontology_end",
    "notify_ontology_start",
    "notify_run_end",
    "notify_run_start",
    "send",
]

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PACKAGE_ROOT / ".env"
API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 5
MAX_MESSAGE_LENGTH = 4096

TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV_VAR = "TELEGRAM_CHAT_ID"

# ── Session state ──────────────────────────────────────────────────────

_ENABLED: bool | None = None
_CREDENTIALS_WARNED = False
_SEND_WARNED = False


# ── Configuration ──────────────────────────────────────────────────────


def load_dotenv(path: Path | None = None, override: bool = True) -> dict[str, str]:
    """Parse a ``.env`` file into ``os.environ``.

    Hand-rolled so the notifier needs no third-party dependency. Supported
    syntax: one ``KEY=VALUE`` per line, optional ``export`` prefix, ``#``
    comments, blank lines and single/double quoted values.

    When *override* is true (the default) values from the file win over
    variables already present in the environment. Returns the parsed
    mapping (empty when the file is absent).
    """
    env_path = Path(path) if path is not None else ENV_PATH
    if not env_path.is_file():
        return {}

    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()

        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value

    return parsed


def get_credentials() -> tuple[str, str] | None:
    """Return ``(token, chat_id)`` from the environment, or ``None``."""
    token = (os.environ.get(TOKEN_ENV_VAR) or "").strip()
    chat_id = (os.environ.get(CHAT_ID_ENV_VAR) or "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def configure(explicit: bool | None = None) -> bool:
    """Load ``.env`` and resolve the notification gate for this process.

    *explicit* mirrors the CLI ``--notify`` / ``--no-notify`` flag: when it
    is not ``None`` it wins over auto-detection. Otherwise notifications
    are enabled only when both credentials resolve. The resolved value is
    cached for later :func:`is_enabled` / :func:`notify` calls.
    """
    global _ENABLED
    load_dotenv()
    _ENABLED = bool(explicit) if explicit is not None else get_credentials() is not None
    return _ENABLED


def is_enabled() -> bool:
    """Return whether notifications are currently enabled (lazy-configured)."""
    if _ENABLED is None:
        return configure()
    return bool(_ENABLED)


def _warn_once(kind: str, message: str) -> None:
    """Write *message* to stderr the first time this *kind* fires."""
    global _CREDENTIALS_WARNED, _SEND_WARNED
    if kind == "credentials":
        if _CREDENTIALS_WARNED:
            return
        _CREDENTIALS_WARNED = True
    else:
        if _SEND_WARNED:
            return
        _SEND_WARNED = True
    print(f"[notifier] {message}", file=sys.stderr, flush=True)


# ── Transport ──────────────────────────────────────────────────────────


def send(text: str) -> bool:
    """POST *text* to the configured chat. Never raises; returns success."""
    credentials = get_credentials()
    if credentials is None:
        _warn_once(
            "credentials",
            f"{TOKEN_ENV_VAR} / {CHAT_ID_ENV_VAR} are not set "
            "(environment or .env); Telegram notifications are disabled.",
        )
        return False

    token, chat_id = credentials
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[: MAX_MESSAGE_LENGTH - 1] + "…"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    # Imported lazily so a missing dependency can never break an experiment.
    try:
        import requests
    except Exception as exc:  # pragma: no cover - depends on the environment
        _warn_once("send", f"the 'requests' package is unavailable ({exc}).")
        return False

    try:
        response = requests.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        _warn_once(
            "send",
            f"could not reach Telegram ({exc}); further send failures will be silent.",
        )
        return False

    if response.status_code != 200:
        _warn_once(
            "send",
            f"Telegram rejected the message (HTTP {response.status_code}): "
            f"{response.text[:200]}",
        )
        return False

    return True


def notify(text: str, *, force: bool = False) -> bool:
    """Send *text* when notifications are enabled. Never raises."""
    if not force and not is_enabled():
        return False
    return send(text)


def fetch_chat_ids() -> list[dict[str, Any]]:
    """Return chat IDs seen in recent bot updates (``getUpdates``).

    Raises ``RuntimeError`` on a missing token or a Telegram error, so the
    helper CLI can surface a useful message. Requires the user to have sent
    at least one message to the bot since the last ``getUpdates`` call.
    """
    load_dotenv()
    token = (os.environ.get(TOKEN_ENV_VAR) or "").strip()
    if not token:
        raise RuntimeError(
            f"{TOKEN_ENV_VAR} is not set (checked the environment and {ENV_PATH})."
        )

    import requests

    try:
        response = requests.get(f"{API_BASE}/bot{token}/getUpdates", timeout=TIMEOUT_SECONDS)
    except Exception as exc:
        raise RuntimeError(f"could not reach Telegram: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Telegram getUpdates failed (HTTP {response.status_code}): {response.text[:300]}"
        )

    try:
        updates = response.json().get("result", [])
    except ValueError as exc:
        raise RuntimeError(f"Telegram returned a non-JSON response: {exc}") from exc

    chats: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for update in updates:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if not chat:
                continue
            chat_id = chat.get("id")
            if chat_id in seen:
                continue
            seen.add(chat_id)
            chats.append(
                {
                    "chat_id": chat_id,
                    "type": chat.get("type", ""),
                    "title": (
                        chat.get("title")
                        or chat.get("username")
                        or chat.get("first_name")
                        or ""
                    ),
                }
            )
    return chats


# ── Formatting helpers ─────────────────────────────────────────────────


def format_duration(seconds: float | None) -> str:
    """Render a duration as a compact ``2h 5m 3s`` string."""
    if seconds is None:
        return "unknown"
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if math.isnan(value) or math.isinf(value) or value < 0:
        return "unknown"

    total_seconds = int(round(value))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# ── Milestone messages ─────────────────────────────────────────────────


def notify_run_start(
    run_id: str,
    n_ontologies: int,
    target_successes: int,
    base_seed: int,
    data_dir: Path,
    results_dir: Path,
) -> bool:
    """Announce that an experiment run has started."""
    return notify(
        "\n".join(
            [
                "▶️ run_trials started",
                f"run_id: {run_id}",
                f"ontologies: {n_ontologies}",
                f"target successes/ontology: {target_successes}",
                f"base seed: {base_seed}",
                f"data dir: {data_dir}",
                f"results dir: {results_dir}",
            ]
        )
    )


def notify_ontology_start(
    index: int,
    total: int,
    name: str,
    axioms: int | None = None,
) -> bool:
    """Announce that trials for one ontology have begun."""
    return notify(
        "\n".join(
            [
                f"▶️ [{index}/{total}] {name}",
                f"axioms: {axioms if axioms is not None else 'unknown'}",
            ]
        )
    )


def notify_ontology_end(
    index: int,
    total: int,
    name: str,
    successes: int,
    attempts: int,
    elapsed_seconds: float | None,
) -> bool:
    """Report the outcome of one ontology's trials."""
    return notify(
        "\n".join(
            [
                f"✅ [{index}/{total}] {name} complete",
                f"successes: {successes}",
                f"attempts: {attempts}",
                f"elapsed: {format_duration(elapsed_seconds)}",
            ]
        )
    )


def notify_abort(
    index: int,
    total: int,
    name: str,
    consecutive_failures: int,
    successes: int,
) -> bool:
    """Report that an ontology was aborted after repeated failures."""
    return notify(
        "\n".join(
            [
                f"⚠️ [{index}/{total}] {name} aborted",
                f"reason: {consecutive_failures} consecutive failures",
                f"successes before abort: {successes}",
            ]
        )
    )


def notify_run_end(
    run_id: str,
    n_ontologies: int,
    wall_clock_seconds: float | None,
    data_dir: Path,
    results_dir: Path,
    analysis_outputs: dict[str, Any] | None = None,
) -> bool:
    """Announce that the experiment run has finished."""
    lines = [
        "🏁 run_trials finished",
        f"run_id: {run_id}",
        f"ontologies: {n_ontologies}",
        f"wall clock: {format_duration(wall_clock_seconds)}",
        f"data dir: {data_dir}",
        f"results dir: {results_dir}",
    ]
    if analysis_outputs:
        report_lines = [
            f"  {key}={value}"
            for key, value in analysis_outputs.items()
            if isinstance(value, (str, Path))
        ]
        if report_lines:
            lines.append("reports:")
            lines.extend(report_lines)
    return notify("\n".join(lines))


def notify_exception(exc: BaseException) -> bool:
    """Report a fatal error, crash or interrupt of the experiment runner."""
    if isinstance(exc, KeyboardInterrupt):
        text = "🛑 run_trials interrupted (Ctrl-C)"
    elif isinstance(exc, SystemExit):
        text = f"🛑 run_trials exited early (SystemExit: {exc.code})"
    else:
        text = f"❌ run_trials crashed: {type(exc).__name__}: {exc}"
    return notify(text)
