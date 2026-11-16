#!/usr/bin/env python3
"""Measure on-disk sizes per relation and per partition (D-33, PERF-00).

Read-only instrument, replayable on any DataCommune database: the restored
legacy production dump (plan 02-03) first, then the new work base. No size may be
announced without this measurement (D-33).

Deliberately stdlib + ``psycopg`` 3 only, with no import from the ``app``
package: it must run inside the old API image, which does not carry this
repository's code.

Usage::

    DATABASE_URL=postgresql://... python measure_db_sizes.py --out sizes.json
    python measure_db_sizes.py --dsn-env OLD_DB_URL --out sizes.json --exact-counts
    python - --out - --flat-tables < measure_db_sizes.py   # script sent on stdin

The DSN is only ever read from an environment variable, never from argv.

Besides relation sizes, the output carries every index of ``public`` (size,
``idx_scan``, unique/primary, whether it backs a constraint), aggregated per
root index across partitions (``indexes``) and per index relation
(``index_relations``); the persistence of each relation (``relpersistence``:
``p`` logged, ``u`` unlogged, ``t`` temporary); and ``stats_reset`` of
``pg_stat_database``, which bounds the period ``idx_scan`` covers (04.1-02).
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

# Copy of app.etl.base.DEPARTEMENTS (102 codes), duplicated on purpose: this
# script must not import the application package (see module docstring).
DEPARTEMENTS_102: tuple[str, ...] = (
    *[f"{i:02d}" for i in range(1, 20)],
    "2A",
    "2B",
    *[str(i) for i in range(21, 96)],
    *[str(i) for i in range(971, 977)],
)
_DEPARTEMENTS_SET = frozenset(DEPARTEMENTS_102)

RELATIONS_SQL = """
SELECT c.oid::bigint AS oid,
       c.relname,
       c.relkind,
       parent.relname AS parent,
       pg_total_relation_size(c.oid) AS total_bytes,
       pg_relation_size(c.oid) AS table_bytes,
       pg_indexes_size(c.oid) AS indexes_bytes,
       CASE WHEN c.reltoastrelid <> 0
            THEN pg_total_relation_size(c.reltoastrelid) ELSE 0 END AS toast_bytes,
       c.reltuples::bigint AS reltuples,
       c.relispopulated AS populated,
       c.relpersistence::text AS relpersistence
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_inherits i ON i.inhrelid = c.oid
LEFT JOIN pg_class parent ON parent.oid = i.inhparent
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'm')
ORDER BY c.relname
"""

# Every index of a public relation; partitioned (parent) indexes included,
# with their parent index when attached (pg_inherits), so that partition
# indexes aggregate under their root index. idx_scan is NULL for partitioned
# indexes (no statistics of their own).
INDEXES_SQL = """
SELECT ic.relname AS index_name,
       t.relname AS table_name,
       ic.relkind::text AS index_kind,
       am.amname AS method,
       pg_relation_size(i.indexrelid) AS bytes,
       s.idx_scan,
       i.indisunique,
       i.indisprimary,
       EXISTS (SELECT 1 FROM pg_constraint con WHERE con.conindid = i.indexrelid)
           AS backs_constraint,
       parent_index.relname AS parent_index
FROM pg_index i
JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
LEFT JOIN pg_am am ON am.oid = ic.relam
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.indexrelid
LEFT JOIN pg_inherits inh ON inh.inhrelid = i.indexrelid
LEFT JOIN pg_class parent_index ON parent_index.oid = inh.inhparent
WHERE n.nspname = 'public'
ORDER BY ic.relname
"""

STATS_RESET_SQL = """
SELECT stats_reset, pg_postmaster_start_time()
FROM pg_stat_database
WHERE datname = current_database()
"""

_KIND_NAMES = {"r": "table", "p": "partitioned", "m": "matview"}
_FLAT_SUFFIXES = ("_staging_flat", "_staging")


def department_of_partition(relname: str) -> str | None:
    """Return the upper-case departement code of a ``<table>_<code>`` partition.

    Migration 001 names partitions ``<table>_<dept.lower()>``; any other
    suffix (``_default``, ordinary table names) yields ``None``.
    """
    if "_" not in relname:
        return None
    suffix = relname.rsplit("_", 1)[1].upper()
    return suffix if suffix in _DEPARTEMENTS_SET else None


def is_flat_or_staging(relname: str) -> bool:
    """Flat and staging tables of the ETL: ``_<name>_staging[_flat]``."""
    return relname.startswith("_") and relname.endswith(_FLAT_SUFFIXES)


def dumps_sorted(payload: Any) -> str:
    """Deterministic JSON: sorted keys, stable separators, trailing newline."""
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_dsn(env_name: str) -> str:
    dsn = os.environ.get(env_name)
    if not dsn:
        raise SystemExit(f"environment variable {env_name} is empty or unset")
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def open_read_only(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, connect_timeout=10)
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    conn.commit()
    return conn


def alembic_version(conn: psycopg.Connection) -> str | None:
    exists = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
    if exists is None:
        return None
    rows = conn.execute("SELECT version_num FROM alembic_version ORDER BY 1").fetchall()
    return ",".join(r[0] for r in rows) if rows else None


def _exact_count(
    conn: psycopg.Connection, relname: str
) -> tuple[int | None, str | None]:
    """count(*) inside a savepoint; an unreadable relation yields a reason."""
    try:
        with conn.transaction():
            value = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(
                    sql.Identifier("public", relname)
                )
            ).fetchone()[0]
        return int(value), None
    except psycopg.Error as exc:
        reason = getattr(exc, "sqlstate", None) or type(exc).__name__
        return None, f"{type(exc).__name__}:{reason}"


def collect_sizes(  # noqa: C901
    conn: psycopg.Connection, *, exact_counts: bool = False
) -> dict[str, Any]:
    """Collect sizes for every table, partitioned table and matview of ``public``."""
    rows = conn.execute(RELATIONS_SQL).fetchall()
    relations: list[dict[str, Any]] = []
    for (
        oid,
        relname,
        relkind,
        parent,
        total,
        table,
        indexes,
        toast,
        tuples,
        populated,
        persistence,
    ) in rows:
        del oid
        entry: dict[str, Any] = {
            "name": relname,
            "kind": _KIND_NAMES[relkind],
            "parent": parent,
            "departement": department_of_partition(relname) if parent else None,
            "pg_total_relation_size": int(total),
            "pg_relation_size": int(table),
            "pg_indexes_size": int(indexes),
            "toast_bytes": int(toast),
            "reltuples": int(tuples),
            "relpersistence": persistence,
        }
        if relkind == "m" and not populated:
            entry["status"] = "non_peuplee"
        if exact_counts and relkind != "p":
            if relkind == "m" and not populated:
                entry["exact_count"] = None
                entry["count_error"] = "non_peuplee"
            else:
                count, error = _exact_count(conn, relname)
                entry["exact_count"] = count
                if error:
                    entry["count_error"] = error
        relations.append(entry)

    # Aggregate each root table with all of its (possibly nested) partitions.
    parent_of = {r["name"]: r["parent"] for r in relations}

    def root_of(name: str) -> str:
        seen = set()
        while parent_of.get(name) and name not in seen:
            seen.add(name)
            name = parent_of[name]
        return name

    persistence_of = {r["name"]: r["relpersistence"] for r in relations}
    tables: dict[str, dict[str, Any]] = {}
    by_departement: dict[str, int] = {}
    for rel in relations:
        root = root_of(rel["name"])
        agg = tables.setdefault(
            root,
            {
                "total_bytes": 0,
                "table_bytes": 0,
                "indexes_bytes": 0,
                "toast_bytes": 0,
                "reltuples": 0,
                "partitions": 0,
                "relpersistence": persistence_of.get(root),
            },
        )
        agg["total_bytes"] += rel["pg_total_relation_size"]
        agg["table_bytes"] += rel["pg_relation_size"]
        agg["indexes_bytes"] += rel["pg_indexes_size"]
        agg["toast_bytes"] += rel["toast_bytes"]
        # reltuples is -1 on a never-analysed relation (and on parents).
        agg["reltuples"] += max(rel["reltuples"], 0)
        if "exact_count" in rel:
            if rel["exact_count"] is None:
                agg["exact_count"] = None
            elif agg.get("exact_count", 0) is not None:
                agg["exact_count"] = agg.get("exact_count", 0) + rel["exact_count"]
        # A partitioned parent has no storage and may carry another
        # persistence than its partitions: the storage members are listed.
        if rel["kind"] != "partitioned":
            members = set(agg.get("storage_relpersistence", []))
            members.add(rel["relpersistence"])
            agg["storage_relpersistence"] = sorted(members)
        if rel["parent"]:
            agg["partitions"] += 1
        if rel["departement"]:
            by_departement[rel["departement"]] = (
                by_departement.get(rel["departement"], 0)
                + rel["pg_total_relation_size"]
            )

    return {
        "relations": relations,
        "tables": tables,
        "by_departement_bytes": by_departement,
    }


def collect_indexes(conn: psycopg.Connection) -> dict[str, Any]:
    """Every index of ``public``, per relation and aggregated per root index.

    ``index_relations`` keeps one entry per index relation (partition indexes
    included). ``indexes`` sums them under their root index (the partitioned
    index they are attached to, or themselves): ``bytes`` and ``idx_scan``
    summed, ``idx_scan`` null when no member carries statistics.
    """
    rows = conn.execute(INDEXES_SQL).fetchall()
    index_relations: list[dict[str, Any]] = []
    for (
        name,
        table,
        kind,
        method,
        size,
        scans,
        unique,
        primary,
        backs,
        parent,
    ) in rows:
        index_relations.append(
            {
                "name": name,
                "table": table,
                "partitioned": kind == "I",
                "method": method,
                "bytes": int(size),
                "idx_scan": None if scans is None else int(scans),
                "is_unique": bool(unique),
                "is_primary": bool(primary),
                "backs_constraint": bool(backs),
                "parent_index": parent,
            }
        )

    parent_of = {i["name"]: i["parent_index"] for i in index_relations}

    def root_of(name: str) -> str:
        seen = set()
        while parent_of.get(name) and name not in seen:
            seen.add(name)
            name = parent_of[name]
        return name

    by_name = {i["name"]: i for i in index_relations}
    indexes: dict[str, dict[str, Any]] = {}
    for idx in index_relations:
        root = by_name.get(root_of(idx["name"]), idx)
        agg = indexes.setdefault(
            root["name"],
            {
                "name": root["name"],
                "table": root["table"],
                "method": root["method"],
                "bytes": 0,
                "idx_scan": None,
                "is_unique": root["is_unique"],
                "is_primary": root["is_primary"],
                "backs_constraint": False,
                "partitions": 0,
            },
        )
        agg["bytes"] += idx["bytes"]
        if idx["idx_scan"] is not None:
            agg["idx_scan"] = (agg["idx_scan"] or 0) + idx["idx_scan"]
        agg["backs_constraint"] = agg["backs_constraint"] or idx["backs_constraint"]
        if idx["parent_index"]:
            agg["partitions"] += 1
    return {
        "indexes": sorted(indexes.values(), key=lambda i: (i["table"], i["name"])),
        "index_relations": index_relations,
    }


def flat_tables(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inventory of the ETL flat and staging tables present, with persistence."""
    return [
        {
            "name": r["name"],
            "relpersistence": r["relpersistence"],
            "pg_total_relation_size": r["pg_total_relation_size"],
            "reltuples": r["reltuples"],
        }
        for r in relations
        if is_flat_or_staging(r["name"])
    ]


def _iso(value: Any) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def script_sha256() -> str | None:
    """SHA-256 of this file; None when run from stdin (``python -``)."""
    path = Path(__file__)
    return file_sha256(path) if path.is_file() else None


def measure(
    conn: psycopg.Connection, *, exact_counts: bool, with_flat_tables: bool = False
) -> dict[str, Any]:
    server_version = conn.execute("SELECT version()").fetchone()[0]
    database_bytes = conn.execute(
        "SELECT pg_database_size(current_database())"
    ).fetchone()[0]
    stats_reset, postmaster_start = conn.execute(STATS_RESET_SQL).fetchone()
    payload = {
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script_sha256": script_sha256(),
        "server_version": server_version,
        "alembic_version": alembic_version(conn),
        "pg_database_size": int(database_bytes),
        "exact_counts": exact_counts,
        # NULL stats_reset: the counters cover the life of the cluster.
        "stats_reset": _iso(stats_reset),
        "postmaster_start_time": _iso(postmaster_start),
    }
    payload.update(collect_sizes(conn, exact_counts=exact_counts))
    payload.update(collect_indexes(conn))
    if with_flat_tables:
        payload["flat_tables"] = flat_tables(payload["relations"])
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dsn-env",
        default="DATABASE_URL",
        help="environment variable holding the DSN (default DATABASE_URL)",
    )
    parser.add_argument(
        "--out", required=True, help="output JSON path, or - for stdout"
    )
    parser.add_argument(
        "--exact-counts",
        action="store_true",
        help="also run count(*) on every relation (slow on large bases)",
    )
    parser.add_argument(
        "--flat-tables",
        action="store_true",
        help="add the inventory of _*_staging_flat and _*_staging tables",
    )
    args = parser.parse_args(argv)

    with open_read_only(read_dsn(args.dsn_env)) as conn:
        payload = measure(
            conn, exact_counts=args.exact_counts, with_flat_tables=args.flat_tables
        )
    text = dumps_sorted(payload)
    summary = (
        f"SIZES {args.out} relations={len(payload['relations'])} "
        f"indexes={len(payload['indexes'])} "
        f"database_bytes={payload['pg_database_size']}"
    )
    if args.out == "-":
        # stdout carries the JSON only; the summary goes to stderr.
        sys.stdout.write(text)
        print(summary, file=sys.stderr)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
