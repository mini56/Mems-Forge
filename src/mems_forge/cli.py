from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .database import initialize_database, validate_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mems-forge")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init-db", help="Create an empty MEMS Forge database")
    init_cmd.add_argument("path", type=Path)

    check_cmd = sub.add_parser("check-db", help="Validate a MEMS Forge database")
    check_cmd.add_argument("path", type=Path)

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "init-db":
        path = initialize_database(args.path, __version__)
        print(path)
        return 0

    if args.command == "check-db":
        result = validate_database(args.path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["integrity_ok"] and result["foreign_key_errors"] == 0 else 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
