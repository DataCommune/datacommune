"""Operator command for the hosted instance scope (D-21).

Usage:
    python -m app.scope show [--json]
    python -m app.scope set --datasets dvf,loyers --departements 44,85
    python -m app.scope set --clear      # host nothing (explicit)

``set`` replaces the whole scope in one transaction; replaying it with the
same values leaves the same rows. Unknown keys or codes, an overseas
collectivity (975, 977, 978, 98x: D-123), or an empty list without
``--clear``, exit 2 before any database connection.

Schema locks (``SCOPE_LOCKS``) are reported, never refused here: ``set``
prints ``SCOPE warning: <key> limité à <codes> par <origin>`` for each lock
the requested departements exceed, ``show`` prints every lock and warns the
same way for the stored scope. The import refuses beyond a lock. Without any
lock (since migration 102), ``show`` says so: ``SCOPE lock: aucun verrou de
schéma``.
"""

import argparse
import json
import sys

import psycopg

from app.etl.base import BasePipeline
from app.scope.model import SCOPE_LOCKS, InstanceScope, refuse_collectivites
from app.scope.store import load_scope_sync, replace_scope, validate_scope


def _split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _summary(scope: InstanceScope) -> str:
    payload = scope.as_json()
    return (
        f"datasets={len(payload['datasets'])} "
        f"departements={','.join(payload['departements'])}"
    )


def _lock_line(label: str, key: str) -> str:
    allowed, origin = SCOPE_LOCKS[key]
    return f"SCOPE {label}: {key} limité à {','.join(allowed)} par {origin}"


def _print_locks() -> None:
    """Every schema lock, or the explicit statement that none is left."""
    for key in SCOPE_LOCKS:
        print(_lock_line("lock", key))
    if not SCOPE_LOCKS:
        print("SCOPE lock: aucun verrou de schéma")


def _exceeded_locks(departements: list[str]) -> list[str]:
    """Locks the departements exceed, whatever the datasets (report only)."""
    return [
        key
        for key, (allowed, _origin) in SCOPE_LOCKS.items()
        if any(code not in allowed for code in departements)
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.scope", description="Read or replace the hosted scope."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="print the scope stored in the database")
    show.add_argument("--json", action="store_true", help="print JSON")
    replace = commands.add_parser(
        "set", help="replace the whole scope (datasets x departements)"
    )
    replace.add_argument("--datasets", help="comma-separated catalogue keys")
    replace.add_argument("--departements", help="comma-separated departement codes")
    replace.add_argument(
        "--clear", action="store_true", help="empty the scope: nothing is hosted"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    if options.command == "set":
        raw_datasets = _split(options.datasets or "")
        raw_departements = [code.upper() for code in _split(options.departements or "")]
        if options.clear and (raw_datasets or raw_departements):
            print("SCOPE refused: --clear takes no list", file=sys.stderr)
            return 2
        if not options.clear and not (raw_datasets and raw_departements):
            # An empty list would silently stop every import (D-21).
            print(
                "SCOPE refused: --datasets and --departements must both be "
                "non-empty (use --clear to host nothing)",
                file=sys.stderr,
            )
            return 2
        try:
            # An overseas collectivity is never hosted (D-123).
            refuse_collectivites(raw_departements)
            datasets, departements = validate_scope(raw_datasets, raw_departements)
        except ValueError as exc:
            print(f"SCOPE refused: {exc}", file=sys.stderr)
            return 2
        with psycopg.connect(BasePipeline.get_sync_dsn(), connect_timeout=10) as conn:
            scope = replace_scope(conn, datasets, departements)
        print(f"SCOPE set {_summary(scope)}")
        for key in _exceeded_locks(departements):
            print(_lock_line("warning", key))
        return 0

    with psycopg.connect(BasePipeline.get_sync_dsn(), connect_timeout=10) as conn:
        scope = load_scope_sync(conn)
    if options.json:
        print(json.dumps(scope.as_json()))
        return 0
    print(f"SCOPE {_summary(scope)}")
    for key in sorted(scope.datasets):
        print(key)
    _print_locks()
    for key in _exceeded_locks(list(scope.departements)):
        print(_lock_line("warning", key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
