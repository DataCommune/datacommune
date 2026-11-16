#!/usr/bin/env python3
"""Deterministic digest of a published table, rows streamed in key order.

    python scripts/table_digest.py --table vacance --order-by code_commune,annee \
        --exclude id --dsn-env DATABASE_URL

Prints ``DIGEST <table> rows=<n> sha256=<hex>``. Two publications of the same
input give the same digest when their rows are equal value for value in the
same key order (plan 02-17, identical replay). Each value is read as its
PostgreSQL text form (``col::text``): the digest does not depend on Python
type conversion. A row is its values joined by a tab, NULL written ``\\N``,
tab, newline and backslash escaped, then a newline.

Surrogate keys drawn from a sequence (``id``) change at every publication
without any change of content: ``--exclude`` names them. Order and excluded
columns are checked against ``information_schema.columns`` before any read;
identifiers only ever reach SQL through ``psycopg.sql.Identifier``. The DSN
is read from the environment variable named by ``--dsn-env``, never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Iterable, Sequence

NULL = "\\N"
_ESCAPES = str.maketrans({"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"})


def encode_row(values: Sequence[object]) -> bytes:
    """Text form of one row, unambiguous for NULL and separators."""
    return (
        "\t".join(NULL if v is None else str(v).translate(_ESCAPES) for v in values)
        + "\n"
    ).encode("utf-8")


def digest_rows(rows: Iterable[Sequence[object]]) -> tuple[int, str]:
    """(row count, SHA-256 of the concatenated encoded rows), in the given order."""
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(encode_row(row))
        count += 1
    return count, digest.hexdigest()


def select_columns(
    table_columns: Sequence[str], order_by: Sequence[str], exclude: Sequence[str]
) -> list[str]:
    """Columns digested, in table order; refuses unknown or excluded keys."""
    if not table_columns:
        raise ValueError("no column: table absent or unreadable")
    known = set(table_columns)
    unknown = [c for c in [*order_by, *exclude] if c not in known]
    if unknown:
        raise ValueError(f"unknown column(s): {unknown}")
    if not order_by:
        raise ValueError("an order is required")
    clash = sorted(set(order_by) & set(exclude))
    if clash:
        raise ValueError(f"order column(s) excluded: {clash}")
    return [c for c in table_columns if c not in set(exclude)]


def table_digest(
    conn, table: str, order_by: Sequence[str], exclude: Sequence[str] = ()
) -> tuple[int, str]:
    """Digest of *table* in the public schema, streamed by a server cursor."""
    from psycopg import sql

    columns = [
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()
    ]
    selected = select_columns(columns, list(order_by), list(exclude))
    query = sql.SQL("SELECT {cols} FROM {table} ORDER BY {order}").format(
        cols=sql.SQL(", ").join(
            sql.SQL("{}::text").format(sql.Identifier(c)) for c in selected
        ),
        table=sql.Identifier("public", table),
        order=sql.SQL(", ").join(sql.Identifier(c) for c in order_by),
    )
    with conn.cursor(name="table_digest") as cursor:
        cursor.itersize = 5000
        cursor.execute(query)
        return digest_rows(cursor)


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--table", required=True)
    parser.add_argument("--order-by", required=True, help="comma-separated key")
    parser.add_argument("--exclude", default="", help="comma-separated columns")
    parser.add_argument("--dsn-env", required=True)
    args = parser.parse_args(argv)

    dsn = os.environ.get(args.dsn_env, "")
    if not dsn:
        print(f"DIGEST_ERROR {args.dsn_env} empty", file=sys.stderr)
        return 2
    # The API services carry the SQLAlchemy form; psycopg reads the plain one.
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    import psycopg

    try:
        with psycopg.connect(dsn) as conn:
            conn.read_only = True
            rows, sha = table_digest(
                conn, args.table, _split(args.order_by), _split(args.exclude)
            )
    except ValueError as exc:
        print(f"DIGEST_ERROR {args.table} {exc}", file=sys.stderr)
        return 2
    print(f"DIGEST {args.table} rows={rows} sha256={sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
