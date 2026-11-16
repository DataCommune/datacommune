#!/usr/bin/env python3
"""Logical row bytes per departement of every published table (PERF-00, 02-18).

One method for every table, partitioned ones included:
``sum(pg_column_size(t.*))`` over the rows of the departement -- row data,
indexes and out-of-line TOAST excluded. The departement of a row is decided
exactly as the import receipts count it (``choose_method`` of
``extract_fidelity_reference.py``): the partition of the departement, a
departement column, or the departement derived from an INSEE code. A
partition size (``measure_db_sizes.py`` ``by_departement_bytes``) includes
indexes and TOAST: it is a physical size, never written here.

Two attribution gaps of ``choose_method`` are closed here, the byte measure
staying the same: a zero-padded departement code (``044`` of
``carte_scolaire``) is read as ``44``; rows without any departement column or
code (``arrets_transport``; ``gares``, whose ``code_insee`` is empty) are
attributed by their point geometry intersecting the departement polygon of
``geo_departements`` (``attribution: spatiale``). A point on a boundary would
count in both departements.

Read-only (run it with the work base tool, ``wb-tool.sh --read-only``); the DSN is read
from ``DATABASE_URL`` only.

    python scripts/measure_logical_bytes.py --out /imports/sizes/logical.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.export_import_manifest import DATASET_TABLES
from scripts.extract_fidelity_reference import (
    DEPARTEMENTS_CIBLES,
    Catalog,
    _insee_departement_sql,
    choose_method,
)

METHOD = "sum(pg_column_size(t.*)) par departement (donnees de ligne, hors index)"
GEOMETRY_COLUMNS = ("position", "geom", "geometry")


def _normalize(dep: str) -> str:
    """``044`` -> ``44``: zero-padded departement codes of some sources."""
    return dep[1:] if len(dep) == 3 and dep.startswith("0") else dep


def _spatial(
    conn: psycopg.Connection, table: str, geometry: str, where: sql.Composable
) -> dict[str, tuple[int, int]]:
    """(rows, row bytes) per target departement, by point-in-polygon."""
    grouped = conn.execute(
        sql.SQL(
            "SELECT d.code, count(*), coalesce(sum(pg_column_size(t.*)), 0) "
            "FROM {t} t JOIN public.geo_departements d "
            "ON d.code = ANY(%s) AND ST_Intersects(d.geometry, t.{g}) "
            "WHERE {w} GROUP BY 1"
        ).format(
            t=sql.Identifier("public", table), g=sql.Identifier(geometry), w=where
        ),
        (list(DEPARTEMENTS_CIBLES),),
    ).fetchall()
    return {code: (int(count), int(size)) for code, count, size in grouped}


def _table_bytes(conn: psycopg.Connection, catalog: Catalog, table: str) -> dict:
    if catalog.status(table) != "ok":
        return {"status": catalog.status(table)}
    method = choose_method(catalog, table)
    rows: dict[str, int] = {}
    logical: dict[str, int] = {}
    columns = catalog.describe(table)[2]
    geometry = next((c for c in GEOMETRY_COLUMNS if c in columns), None)
    spatial_rows = 0
    if method["method"] == "partition":
        for dep in DEPARTEMENTS_CIBLES:
            count, size = conn.execute(
                sql.SQL(
                    "SELECT count(*), coalesce(sum(pg_column_size(t.*)), 0) FROM {} t"
                ).format(sql.Identifier("public", f"{table}_{dep.lower()}"))
            ).fetchone()
            rows[dep], logical[dep] = int(count), int(size)
    elif method["method"] in {"code_departement", "departement_from_insee"}:
        column = sql.Identifier(method["column"])
        expr = (
            column
            if method["method"] == "code_departement"
            else _insee_departement_sql(column)
        )
        grouped = conn.execute(
            sql.SQL(
                "SELECT coalesce({e}::text, ''), count(*), "
                "coalesce(sum(pg_column_size(t.*)), 0) FROM {t} t GROUP BY 1"
            ).format(e=expr, t=sql.Identifier("public", table))
        ).fetchall()
        for dep, count, size in grouped:
            key = _normalize(dep) if dep else "sans_departement"
            rows[key] = rows.get(key, 0) + int(count)
            logical[key] = logical.get(key, 0) + int(size)
        if rows.get("sans_departement") and geometry:
            where = sql.SQL("{e} IS NULL OR {e}::text = ''").format(e=expr)
            for dep, (count, size) in _spatial(conn, table, geometry, where).items():
                rows[dep] = rows.get(dep, 0) + count
                logical[dep] = logical.get(dep, 0) + size
                spatial_rows += count
    elif geometry:
        for dep, (count, size) in _spatial(
            conn, table, geometry, sql.SQL("TRUE")
        ).items():
            rows[dep], logical[dep] = count, size
            spatial_rows += count
    else:
        return {"method": method, "logical_bytes_by_departement": None}
    return {
        "method": method,
        "attribution": "spatiale" if spatial_rows else "colonne",
        "spatial_rows": spatial_rows,
        "rows_by_departement": dict(sorted(rows.items())),
        "logical_bytes_by_departement": dict(sorted(logical.items())),
    }


def measure(conn: psycopg.Connection) -> dict[str, Any]:
    catalog = Catalog(conn)
    datasets: dict[str, Any] = {}
    for dataset, spec in sorted(DATASET_TABLES.items()):
        tables = {name: _table_bytes(conn, catalog, name) for name in spec["tables"]}
        totals: dict[str, int] | None = {}
        for info in tables.values():
            values = info.get("logical_bytes_by_departement")
            if values is None:
                totals = None
                break
            for dep in DEPARTEMENTS_CIBLES:
                totals[dep] = totals.get(dep, 0) + int(values.get(dep, 0))
        datasets[dataset] = {"tables": tables, "total_by_departement": totals}
    return datasets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL", "").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    if not dsn:
        print("DATABASE_URL is empty", file=sys.stderr)
        return 2
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        datasets = measure(conn)
    payload = {
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workbase_sha": os.environ.get("WORKBASE_SHA"),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "method": METHOD,
        "departements": list(DEPARTEMENTS_CIBLES),
        "datasets": datasets,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"LOGICAL_BYTES {args.out} datasets={len(datasets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
