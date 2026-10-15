#!/usr/bin/env python3
"""CLI helper for the Telegram progress notifier.

Subcommands:
  chat-id   Discover chat IDs from recent bot updates (``getUpdates``).
  test      Send a test message to the configured chat.

Usage:
    python -m cli.telegram_notify chat-id
    python -m cli.telegram_notify test
    python cli/telegram_notify.py --env-file .env test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python cli/telegram_notify.py`` (without ``-m``)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis import notifier


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Utilities for the Telegram progress notifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s chat-id\n"
            "  %(prog)s test\n"
            "  %(prog)s test --message 'hello from run_trials'\n"
            "  %(prog)s test --dry-run\n"
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=f"Path to a .env file (default: {notifier.ENV_PATH}).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_id = subparsers.add_parser(
        "chat-id",
        help="Discover chat IDs from recent bot updates.",
        description=(
            "Call getUpdates and print the chat IDs it contains. Send any "
            "message to your bot in Telegram first, otherwise the list is empty."
        ),
    )
    chat_id.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request that would be made without calling Telegram.",
    )

    test = subparsers.add_parser(
        "test",
        help="Send a test message to the configured chat.",
    )
    test.add_argument(
        "--message",
        default="ontologyutils run_trials notifier test",
        help="Message text to send (default: %(default)s).",
    )
    test.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request that would be made without sending it.",
    )

    return parser


def cmd_chat_id(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"[dry-run] GET {notifier.API_BASE}/bot<token>/getUpdates")
        return 0

    try:
        chats = notifier.fetch_chat_ids()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not chats:
        print(
            "No chat IDs found. Open Telegram, send any message to your bot, "
            "then run this command again.",
            file=sys.stderr,
        )
        return 1

    print("Discovered chat IDs:")
    for chat in chats:
        label = f"  ({chat['title']})" if chat["title"] else ""
        print(f"  {notifier.CHAT_ID_ENV_VAR}={chat['chat_id']}  [{chat['type']}]{label}")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    credentials = notifier.get_credentials()

    if args.dry_run:
        if credentials is None:
            print(
                f"[dry-run] {notifier.TOKEN_ENV_VAR} / {notifier.CHAT_ID_ENV_VAR} "
                "are not set; a real run would skip this message."
            )
        else:
            print(f"[dry-run] POST {notifier.API_BASE}/bot<token>/sendMessage")
            print(f"[dry-run] chat_id={credentials[1]}")
            print(f"[dry-run] text={args.message!r}")
        return 0

    if credentials is None:
        print(
            f"error: {notifier.TOKEN_ENV_VAR} and {notifier.CHAT_ID_ENV_VAR} must be "
            f"set (environment or {args.env_file or notifier.ENV_PATH}).",
            file=sys.stderr,
        )
        return 1

    if notifier.notify(args.message, force=True):
        print(f"Sent test message to chat {credentials[1]}.")
        return 0

    print("error: failed to send the test message (see warning above).", file=sys.stderr)
    return 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # The .env file is the supported configuration source; loading it here
    # means both subcommands see the same credentials the runner would use.
    notifier.load_dotenv(args.env_file)

    if args.command == "chat-id":
        raise SystemExit(cmd_chat_id(args))
    if args.command == "test":
        raise SystemExit(cmd_test(args))

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
