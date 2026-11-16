#!/usr/bin/env python3
"""Copy reviewed public rows from production into an empty preproduction schema.

Credentials are accepted through libpq DSN files. The script never prints a DSN,
and subprocesses receive connection fields through PG* environment variables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


class SeedRefusedError(RuntimeError):
    pass


MIGRATION_ADDRESS_FIXTURE = {
    (
        "75",
        "1",
        None,
        "Place de la Concorde",
        "75008",
        "75108",
        "Paris",
        2.3211,
        48.8656,
        None,
    ),
    (
        "75",
        "10",
        None,
        "Rue de Rivoli",
        "75001",
        "75101",
        "Paris",
        2.3522,
        48.8566,
        None,
    ),
    (
        "75",
        "5",
        None,
        "Avenue des Champs-Elysees",
        "75008",
        "75108",
        "Paris",
        2.3065,
        48.8698,
        None,
    ),
    (
        "13",
        "1",
        None,
        "Quai du Port",
        "13002",
        "13202",
        "Marseille",
        5.3698,
        43.2965,
        None,
    ),
    (
        "13",
        "80",
        None,
        "La Canebiere",
        "13001",
        "13201",
        "Marseille",
        5.3757,
        43.2977,
        None,
    ),
    (
        "2A",
        "1",
        None,
        "Cours Napoleon",
        "20000",
        "2A004",
        "Ajaccio",
        8.7386,
        41.9192,
        None,
    ),
    (
        "2A",
        "18",
        None,
        "Boulevard du Roi Jerome",
        "20000",
        "2A004",
        "Ajaccio",
        8.735,
        41.921,
        None,
    ),
    (
        "971",
        "1",
        None,
        "Rue de la Liberte",
        "97200",
        "97209",
        "Fort-de-France",
        -61.0588,
        14.6161,
        None,
    ),
    (
        "971",
        "25",
        None,
        "Boulevard du General de Gaulle",
        "97200",
        "97209",
        "Fort-de-France",
        -61.0614,
        14.6037,
        None,
    ),
}

CANONICAL_MATVIEWS = {
    "communes_lookup",
    "v_commune_synthese",
    "v_commune_dvf",
    "v_commune_dpe",
    "v_commune_equipements",
    "v_commune_scolaire",
    "v_commune_monuments",
    "v_commune_foncier_pm",
    "v_commune_connectivite",
}


@dataclass(frozen=True)
class DatabaseIdentity:
    system_identifier: str
    database: str
    address: str
    port: int


def load_manifest(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("classification") != "public-data-only" or not value.get("tables"):
        raise SeedRefusedError("seed manifest is not a public-data-only allowlist")
    return value


def validate_isolation(
    source: DatabaseIdentity, target: DatabaseIdentity, target_marker: str
) -> None:
    if source == target or (source.system_identifier, source.database) == (
        target.system_identifier,
        target.database,
    ):
        raise SeedRefusedError(
            "source and target resolve to the same PostgreSQL database"
        )
    if target_marker != "preproduction" or "preprod" not in target.database.lower():
        raise SeedRefusedError("target lacks the required preproduction marker")


def validate_repair_target(target: DatabaseIdentity, target_marker: str) -> None:
    if target_marker != "preproduction" or target.database != "datacommune_preprod":
        raise SeedRefusedError("repair target lacks the required preproduction marker")


def validate_requested_tables(
    requested: list[str], manifest: dict, existing: set[str]
) -> list[str]:
    allowed = set(manifest["tables"])
    denied = [
        re.compile(pattern, re.IGNORECASE) for pattern in manifest["deny_patterns"]
    ]
    for table in requested:
        if any(pattern.fullmatch(table) for pattern in denied):
            raise SeedRefusedError(f"private table refused: {table}")
        if table not in allowed:
            raise SeedRefusedError(
                f"table is not classified in the public allowlist: {table}"
            )
        if table not in existing:
            raise SeedRefusedError(f"allowlisted source table does not exist: {table}")
    return sorted(set(requested))


def validate_target_empty(
    counts: dict[str, int], mode: str, migration_baseline_tables: set[str] | None = None
) -> None:
    baseline = migration_baseline_tables or set()
    populated = {
        name: count for name, count in counts.items() if count and name not in baseline
    }
    if populated and mode != "verify-existing":
        raise SeedRefusedError(
            "target is not empty; use verify-existing for a non-writing "
            "idempotence check"
        )


def _address_rows(conn) -> set[tuple]:
    return {
        (*row[:7], float(row[7]), float(row[8]), row[9])
        for row in conn.execute(
            "SELECT code_departement,numero,rep,nom_voie,code_postal,"
            "code_commune,nom_commune,ST_X(position),ST_Y(position),ban_id "
            "FROM adresses"
        )
    }


def validate_migration_address_fixture(rows: set[tuple]) -> None:
    if rows != MIGRATION_ADDRESS_FIXTURE:
        raise SeedRefusedError(
            "target adresses differ from the exact migration fixture"
        )


def _delete_migration_address_fixture(target) -> None:
    target.execute("LOCK TABLE adresses IN ACCESS EXCLUSIVE MODE")
    validate_migration_address_fixture(_address_rows(target))
    deleted = target.execute("DELETE FROM adresses RETURNING 1").fetchall()
    if len(deleted) != len(MIGRATION_ADDRESS_FIXTURE):
        raise SeedRefusedError("migration address fixture cleanup count changed")
    target.commit()


def publication_oid_rebind_sql() -> str:
    return """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM etl_publications AS p
        CROSS JOIN LATERAL jsonb_object_keys(p.relation_oids) AS relation(name)
        WHERE p.status = 'published'
          AND to_regclass(format('public.%I', relation.name)) IS NULL
    ) THEN
        RAISE EXCEPTION
            'published relation metadata references a missing target relation';
    END IF;
END
$$;
UPDATE etl_publications p
SET relation_oids = (
    SELECT jsonb_object_agg(
        relation.name,
        (to_regclass(format('public.%I', relation.name)))::oid::bigint
    )
    FROM jsonb_object_keys(p.relation_oids) AS relation(name)
)
WHERE jsonb_typeof(p.relation_oids) = 'object'
  AND p.relation_oids <> '{}'::jsonb;
"""


def _dsn_from_file(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SeedRefusedError(f"empty DSN file: {path}")
    return value


def _pg_env(dsn: str, *, readonly: bool = False) -> dict[str, str]:
    parsed = urlparse(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not parsed.path
    ):
        raise SeedRefusedError("DSN file does not contain a supported PostgreSQL URL")
    env = os.environ.copy()
    env.update(
        PGHOST=parsed.hostname,
        PGPORT=str(parsed.port or 5432),
        PGDATABASE=unquote(parsed.path.lstrip("/")),
        PGUSER=unquote(parsed.username or ""),
        PGPASSWORD=unquote(parsed.password or ""),
    )
    if readonly:
        env["PGOPTIONS"] = "-c default_transaction_read_only=on"
    return env


def _connect(dsn: str, *, readonly: bool):
    import psycopg

    conn = psycopg.connect(
        dsn.replace("postgresql+asyncpg://", "postgresql://", 1), connect_timeout=10
    )
    if readonly:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    return conn


def _identity(conn) -> tuple[DatabaseIdentity, str]:
    row = conn.execute(
        "SELECT (pg_control_system()).system_identifier::text, current_database(), "
        "coalesce(inet_server_addr()::text, 'local'), inet_server_port(), "
        "coalesce(current_setting('datacommune.environment', true), '')"
    ).fetchone()
    return DatabaseIdentity(str(row[0]), row[1], row[2], int(row[3])), row[4]


def _tables(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'"
        )
    }


def _counts(conn, tables: list[str]) -> dict[str, int]:
    from psycopg import sql

    return {
        table: int(
            conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
        )
        for table in tables
    }


def _schema_revision(conn) -> str:
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else ""


def _read_dataset_metadata(source) -> tuple[list[str], list[tuple]]:
    from psycopg import sql

    columns = [
        row[0]
        for row in source.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='dataset_metadata' "
            "ORDER BY ordinal_position"
        )
    ]
    rows = list(
        source.execute(
            sql.SQL("SELECT {} FROM dataset_metadata ORDER BY dataset_name").format(
                sql.SQL(", ").join(map(sql.Identifier, columns))
            )
        ).fetchall()
    )
    return columns, rows


def _merge_dataset_metadata(target, columns: list[str], rows: list[tuple]) -> None:
    """Replace the migration baseline with reviewed public metadata."""
    from psycopg import sql

    assignments = sql.SQL(", ").join(
        sql.SQL("{} = EXCLUDED.{}").format(
            sql.Identifier(column), sql.Identifier(column)
        )
        for column in columns
        if column != "dataset_name"
    )
    statement = sql.SQL(
        "INSERT INTO dataset_metadata ({}) VALUES ({}) "
        "ON CONFLICT (dataset_name) DO UPDATE SET {}"
    ).format(
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        assignments,
    )
    target.execute("DELETE FROM dataset_metadata")
    with target.cursor() as cursor:
        cursor.executemany(statement, rows)


def _run_database_tool(command: list[str], env: dict[str, str], operation: str) -> None:
    result = subprocess.run(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        # Database tool diagnostics can contain row values. Keep them out of CI logs.
        raise SeedRefusedError(f"{operation} failed; inspect protected operator logs")


def build_dump_command(dump: Path, tables: list[str], snapshot: str) -> list[str]:
    if not tables:
        raise SeedRefusedError(
            "seed-empty requires at least one non-baseline public table"
        )
    command = [
        "pg_dump",
        "--format=custom",
        "--data-only",
        "--no-owner",
        "--no-privileges",
        "--snapshot",
        snapshot,
        "--file",
        str(dump),
    ]
    for table in tables:
        command.extend(["--table-and-children", f"public.{table}"])
    return command


def filter_restore_toc(lines: list[str]) -> list[str]:
    """Drop source sequence values; target-owned sequences are reset post-restore."""
    return [line for line in lines if " SEQUENCE SET " not in line]


def _write_restore_toc(dump: Path, toc: Path) -> None:
    result = subprocess.run(
        ["pg_restore", "--list", str(dump)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode:
        raise SeedRefusedError("could not inspect the protected dump archive")
    toc.write_text("\n".join(filter_restore_toc(result.stdout.splitlines())) + "\n")


def _post_restore(conn, views: list[str], *, rebind_publications: bool = True) -> None:
    from psycopg import sql

    if rebind_publications:
        conn.execute(publication_oid_rebind_sql())
    sequence_rows = conn.execute(
        "SELECT schemaname, sequencename FROM pg_catalog.pg_sequences "
        "WHERE schemaname='public'"
    ).fetchall()
    for schema, sequence in sequence_rows:
        owned = conn.execute(
            "SELECT tbl.relname, col.attname FROM pg_class seq "
            "JOIN pg_depend dep ON dep.objid=seq.oid AND dep.deptype IN ('a','i') "
            "JOIN pg_class tbl ON tbl.oid=dep.refobjid "
            "JOIN pg_attribute col ON col.attrelid=tbl.oid "
            "AND col.attnum=dep.refobjsubid "
            "WHERE seq.relkind='S' AND seq.relname=%s",
            (sequence,),
        ).fetchone()
        if owned:
            maximum = conn.execute(
                sql.SQL("SELECT max({}) FROM {}").format(
                    sql.Identifier(owned[1]), sql.Identifier(owned[0])
                )
            ).fetchone()[0]
            conn.execute(
                "SELECT setval(%s::regclass, %s, %s)",
                (f"{schema}.{sequence}", maximum or 1, maximum is not None),
            )
    for view in views:
        if conn.execute("SELECT to_regclass(%s)", (f"public.{view}",)).fetchone()[0]:
            conn.execute(
                sql.SQL("REFRESH MATERIALIZED VIEW {}").format(sql.Identifier(view))
            )


def _repair_canonical_views(target) -> tuple[dict[str, str], float]:
    backend = Path(__file__).resolve().parents[1] / "api" / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.common import communes_lookup_ddl
    from app.commune import matview_ddl
    from app.commune.dependencies import PUBLICATION_LOCK_ID

    synthese_builder = Path(matview_ddl.__file__).resolve()
    lookup_builder = Path(communes_lookup_ddl.__file__).resolve()
    builder_hashes = {
        "communes_lookup": hashlib.sha256(lookup_builder.read_bytes()).hexdigest(),
        "v_commune_synthese": hashlib.sha256(synthese_builder.read_bytes()).hexdigest(),
    }
    started = time.monotonic()
    target.execute("SELECT pg_advisory_xact_lock(%s)", (PUBLICATION_LOCK_ID,))
    communes_lookup_ddl.recreate_communes_lookup(target)
    matview_ddl.recreate_commune_synthese(target)
    duration = time.monotonic() - started
    target.execute(publication_oid_rebind_sql())
    rebuilt = ["communes_lookup"]
    rebuilt.extend(name for name, _ in matview_ddl.INTERMEDIATE_MATVIEWS)
    rebuilt.append("v_commune_synthese")
    for name in rebuilt:
        target.execute(
            """
            INSERT INTO matview_metadata (
                matview_name, last_refresh_at, last_refresh_duration_seconds,
                last_refresh_status, last_error
            ) VALUES (%s, NOW(), %s, 'success', NULL)
            ON CONFLICT (matview_name) DO UPDATE SET
                last_refresh_at = EXCLUDED.last_refresh_at,
                last_refresh_duration_seconds = EXCLUDED.last_refresh_duration_seconds,
                last_refresh_status = 'success', last_error = NULL
            """,
            (name, duration),
        )
    return builder_hashes, duration


def should_repair_canonical_views(tables: list[str], manifest: dict) -> bool:
    return set(tables) == set(manifest["tables"])


def _finalize_restored_target(
    target,
    *,
    views: list[str],
    tables: list[str],
    source_counts: dict[str, int],
    full_seed: bool,
) -> tuple[dict[str, int], dict[str, str] | None]:
    refresh_views = (
        [view for view in views if view not in CANONICAL_MATVIEWS]
        if full_seed
        else views
    )
    _post_restore(
        target,
        refresh_views,
        rebind_publications=not full_seed,
    )
    target_counts = _counts(target, tables)
    if source_counts != target_counts:
        raise SeedRefusedError(
            "restored target row counts differ from the exported snapshot"
        )
    builder_hashes = None
    if full_seed:
        builder_hashes, _ = _repair_canonical_views(target)
    target.commit()
    return target_counts, builder_hashes


def _repair_guard_counts(target, manifest: dict) -> dict[str, int]:
    existing = _tables(target)
    denied = [
        re.compile(pattern, re.IGNORECASE) for pattern in manifest["deny_patterns"]
    ]
    guarded = sorted(
        (set(manifest["tables"]) & existing)
        | {
            table
            for table in existing
            if any(pattern.fullmatch(table) for pattern in denied)
        }
    )
    return _counts(target, guarded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dsn-file", type=Path)
    parser.add_argument("--target-dsn-file", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=Path("infra/preproduction-seed-tables.json")
    )
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--tables", nargs="*")
    parser.add_argument(
        "--mode",
        choices=("seed-empty", "verify-existing", "repair-views"),
        default="seed-empty",
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    requested = args.tables or manifest["tables"]
    target_dsn = _dsn_from_file(args.target_dsn_file)

    if args.mode == "repair-views":
        with _connect(target_dsn, readonly=False) as target:
            target_identity, marker = _identity(target)
            validate_repair_target(target_identity, marker)
            if _schema_revision(target) != args.expected_revision:
                raise SeedRefusedError(
                    "repair target must be at the exact expected Alembic revision"
                )
            counts_before = _repair_guard_counts(target, manifest)
            builder_hashes, duration = _repair_canonical_views(target)
            counts_after = _repair_guard_counts(target, manifest)
            if counts_after != counts_before:
                raise SeedRefusedError("view repair changed guarded table row counts")
            target.commit()
        args.report.write_text(
            json.dumps(
                {
                    "mode": "repair-views",
                    "schema_revision": args.expected_revision,
                    "canonical_builders_sha256": builder_hashes,
                    "duration_seconds": round(duration, 3),
                    "target_identity_hash": hashlib.sha256(
                        repr(target_identity).encode()
                    ).hexdigest(),
                    "guarded_table_counts_before": counts_before,
                    "guarded_table_counts_after": counts_after,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0

    if args.source_dsn_file is None:
        raise SeedRefusedError("source DSN file is required for data seed modes")
    source_dsn = _dsn_from_file(args.source_dsn_file)

    baseline_tables = set(manifest.get("migration_baseline_tables", []))
    canonical_builder_hashes = None
    with tempfile.TemporaryDirectory(prefix="datacommune-preprod-seed-") as directory:
        dump = Path(directory) / "public-data.dump"
        restore_toc = Path(directory) / "restore.list"
        # Both probes are read-only until every guard passes. The exported source
        # snapshot binds pg_dump and source counts to the same database state.
        with (
            _connect(source_dsn, readonly=True) as source,
            _connect(target_dsn, readonly=True) as target_probe,
        ):
            source_identity, _ = _identity(source)
            target_identity, marker = _identity(target_probe)
            validate_isolation(source_identity, target_identity, marker)
            tables = validate_requested_tables(requested, manifest, _tables(source))
            if (
                _schema_revision(source) != args.expected_revision
                or _schema_revision(target_probe) != args.expected_revision
            ):
                raise SeedRefusedError(
                    "source and target must both be at the exact expected "
                    "Alembic revision"
                )
            target_counts = _counts(
                target_probe, [t for t in tables if t in _tables(target_probe)]
            )
            cleanup_address_fixture = False
            if args.mode == "seed-empty" and target_counts.get("adresses"):
                validate_migration_address_fixture(_address_rows(target_probe))
                cleanup_address_fixture = True
                target_counts["adresses"] = 0
            validate_target_empty(target_counts, args.mode, baseline_tables)
            metadata_snapshot = (
                _read_dataset_metadata(source)
                if args.mode == "seed-empty" and "dataset_metadata" in baseline_tables
                else ([], [])
            )
            dump_tables = [table for table in tables if table not in baseline_tables]
            if args.mode == "seed-empty":
                snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
                dump_cmd = build_dump_command(dump, dump_tables, snapshot)
                _run_database_tool(
                    dump_cmd, _pg_env(source_dsn, readonly=True), "public pg_dump"
                )
                _write_restore_toc(dump, restore_toc)
            source_counts = _counts(source, tables)

        if args.mode == "verify-existing":
            if source_counts != target_counts:
                raise SeedRefusedError(
                    "existing target row counts differ from the reviewed source"
                )
        else:
            if cleanup_address_fixture:
                with _connect(target_dsn, readonly=False) as target:
                    _delete_migration_address_fixture(target)
            _run_database_tool(
                [
                    "pg_restore",
                    "--data-only",
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "--single-transaction",
                    "--disable-triggers",
                    "--use-list",
                    str(restore_toc),
                    "--dbname",
                    target_identity.database,
                    str(dump),
                ],
                _pg_env(target_dsn),
                "preproduction pg_restore",
            )
            with _connect(target_dsn, readonly=False) as target:
                if "dataset_metadata" in baseline_tables:
                    _merge_dataset_metadata(target, *metadata_snapshot)
                full_seed = should_repair_canonical_views(tables, manifest)
                target_counts, canonical_builder_hashes = _finalize_restored_target(
                    target,
                    views=manifest["materialized_views"],
                    tables=tables,
                    source_counts=source_counts,
                    full_seed=full_seed,
                )

    manifest_hash_input = json.dumps(
        {"revision": args.expected_revision, "tables": target_counts}, sort_keys=True
    ).encode()
    report = {
        "classification": manifest["classification"],
        "schema_revision": args.expected_revision,
        "source_identity_hash": hashlib.sha256(
            repr(source_identity).encode()
        ).hexdigest(),
        "target_identity_hash": hashlib.sha256(
            repr(target_identity).encode()
        ).hexdigest(),
        "data_manifest_sha256": hashlib.sha256(manifest_hash_input).hexdigest(),
        "tables": {name: {"rows": target_counts[name]} for name in tables},
    }
    if canonical_builder_hashes is not None:
        report["canonical_builders_sha256"] = canonical_builder_hashes
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedRefusedError as exc:
        raise SystemExit(f"REFUSED: {exc}") from None
