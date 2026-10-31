"""WATSON command-line interface: run, replay, models, db, doctor."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from watson import __version__

_STUB_COMMANDS = ("run", "replay", "models", "db", "doctor")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watson",
        description="Local-first work memory. Subcommands land in later milestones.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"watson {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name in _STUB_COMMANDS:
        subparsers.add_parser(name, help="not implemented")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 1

    print("not implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
