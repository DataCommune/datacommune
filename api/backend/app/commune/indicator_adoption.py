"""Explicit, guarded adoption of the existing preproduction DVF stock."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from app.commune.indicator_schemas import hosted_departements
from app.config import settings
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.publication import Publication, dataset_execution_lock
from app.etl.runner import PipelineRunner
from app.etl.schemas import EtlResult

# A fresh administrative process does not import scheduler registrations.
from app.etl_dvf import pipeline as _dvf_pipeline_registration  # noqa: F401
from app.etl_dvf.reconstruction import reconstruit_mutations_grain

_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "096_commune_indicator_build_activation"
_DVF_PROJECTION_SQL = """
SELECT code_departement AS department, code_commune AS commune, classe_mutation,
       date_mutation, est_vente, hors_stats_vefa, nb_communes,
       surface_bati_totale, valeur_fonciere
FROM public.mutations_grain WHERE code_departement = ANY(%(departments)s)
ORDER BY code_departement, code_commune, classe_mutation, date_mutation
"""


class AdoptionRefused(RuntimeError):  # noqa: N818 - domain refusal, not fault
    """A precondition or immutable-stock assertion failed."""


@dataclass(frozen=True)
class SourceColumn:
    name: str
    formatted_type: str
    type_oid: int
    type_modifier: int
    is_geometry: bool


@dataclass(frozen=True)
class StockFingerprint:
    source_rows: int
    mutation_count: int
    raw_sha256: str
    all_years_projection_sha256: str
    received_projection_sha256: str
    received_projection_count: int
    source_oid: int


@dataclass(frozen=True)
class AdoptionRequest:
    scope: str
    expected_candidate_sha: str
    expected_runtime_sha256: str
    expected_schema_revision: str
    expected_system_identifier: str
    expected_database: str
    expected_server_address: str
    expected_server_port: int
    backup_receipt: Path
    expected_backup_sha256: str
    expected_source_rows: int
    expected_mutation_count: int
    expected_all_years_projection_sha256: str
    expected_projection_count: int
    expected_projection_sha256: str
    expected_raw_sha256: str
    # Received departements of the lot: projection and reconstruction scope.
    departments: tuple[str, ...]


def _validate_request(request: AdoptionRequest) -> None:
    if request.scope != "preproduction_ct277":
        raise AdoptionRefused("scope_must_be_preproduction_ct277")
    try:
        hosted_departements(request.departments)
    except ValueError:
        raise AdoptionRefused("invalid_departments") from None
    if not _SHA.fullmatch(request.expected_candidate_sha):
        raise AdoptionRefused("invalid_expected_candidate_sha")
    if request.expected_candidate_sha != settings.deployment_sha:
        raise AdoptionRefused("runtime_candidate_sha_mismatch")
    if request.expected_runtime_sha256 != runtime_source_sha256():
        raise AdoptionRefused("runtime_source_sha256_mismatch")
    if request.expected_schema_revision != _SCHEMA:
        raise AdoptionRefused("schema_096_required")
    if request.expected_database != "datacommune_preprod":
        raise AdoptionRefused("database_must_be_datacommune_preprod")
    if not all(
        _DIGEST.fullmatch(value)
        for value in (
            request.expected_all_years_projection_sha256,
            request.expected_projection_sha256,
            request.expected_raw_sha256,
        )
    ):
        raise AdoptionRefused("invalid_expected_digest")
    _validate_backup_receipt(request)


def runtime_source_sha256() -> str:
    """Observe the deployed command sources independently of release settings."""
    paths = (
        Path(__file__),
        Path(__file__).parents[2] / "scripts" / "adopt_commune_indicator_stock.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        payload = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _validate_backup_receipt(request: AdoptionRequest) -> None:
    try:
        receipt = json.loads(request.backup_receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdoptionRefused("invalid_backup_receipt") from exc
    required = ("backup_id", "captured_at", "capture_mode", "restore_path")
    if not all(isinstance(receipt.get(key), str) and receipt[key] for key in required):
        raise AdoptionRefused("incomplete_backup_receipt")
    if receipt["capture_mode"] not in {
        "ct277_stopped",
        "database_quiescent",
        "pg_dump_consistent_snapshot",
    }:
        raise AdoptionRefused("backup_not_consistent")
    if (
        receipt.get("catalog_verified") is not True
        or receipt.get("restore_tested") is not True
    ):
        raise AdoptionRefused("backup_restore_not_attested")
    binding = (
        receipt.get("environment"),
        receipt.get("vmid"),
        receipt.get("database"),
        receipt.get("sha256"),
        receipt.get("restore_exit_code"),
        receipt.get("restore_target_removed"),
    )
    expected = (
        "preproduction",
        277,
        "datacommune_preprod",
        request.expected_backup_sha256,
        0,
        True,
    )
    if binding != expected or not _DIGEST.fullmatch(request.expected_backup_sha256):
        raise AdoptionRefused("backup_binding_mismatch")
    if receipt.get("restored_counts") != {
        "mutations": request.expected_source_rows,
        "mutations_grain": request.expected_mutation_count,
        "diagnostics_dpe": 540717,
        "schema": "094_publication_generations",
    }:
        raise AdoptionRefused("backup_restored_counts_mismatch")


def source_columns(conn: Any) -> tuple[SourceColumn, ...]:
    rows = conn.execute(
        """SELECT attname,format_type(atttypid,atttypmod),atttypid,atttypmod,
                  atttypid='geometry'::regtype
             FROM pg_attribute WHERE attrelid='mutations'::regclass
              AND attnum>0 AND NOT attisdropped ORDER BY attnum"""
    ).fetchall()
    if not rows:
        raise AdoptionRefused("mutations_schema_missing")
    return tuple(SourceColumn(*row) for row in rows)


def raw_multiset_query(
    columns: tuple[SourceColumn, ...], table: str = "mutations"
) -> sql.Composed:
    """Hash every typed field, then the sorted multiset of row hashes."""
    fields = []
    for column in columns:
        identifier = sql.Identifier(column.name)
        value = (
            sql.SQL("encode(ST_AsEWKB({}), 'hex')").format(identifier)
            if column.is_geometry
            else sql.SQL("encode(convert_to({}::text,'UTF8'),'hex')").format(identifier)
        )
        fields.append(
            sql.SQL(
                "{} || ':' || {} || ':' || CASE WHEN {} IS NULL "
                "THEN '<NULL>' ELSE {} END"
            ).format(
                sql.Literal(column.name),
                sql.Literal(column.formatted_type),
                identifier,
                value,
            )
        )
    payload = sql.SQL(" || '|' || ").join(fields)
    schema = "|".join(
        f"{c.name}:{c.formatted_type}:{c.type_oid}:{c.type_modifier}" for c in columns
    )
    return sql.SQL(
        "WITH rows AS (SELECT encode(sha256(convert_to({},'UTF8')),'hex') "
        "row_sha FROM {}), "
        "ordered AS (SELECT row_sha FROM rows ORDER BY row_sha) "
        "SELECT count(*),encode(sha256(convert_to({} || E'\\n' || "
        "string_agg(row_sha,E'\\n'),'UTF8')),'hex') FROM ordered"
    ).format(payload, sql.Identifier(table), sql.Literal(schema))


def validate_database_identity(dsn: str, request: AdoptionRequest) -> None:
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        identity = conn.execute(
            "SELECT (pg_control_system()).system_identifier::text,current_database(),"
            "coalesce(inet_server_addr()::text,'local'),inet_server_port()"
        ).fetchone()
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    expected = (
        request.expected_system_identifier,
        request.expected_database,
        request.expected_server_address,
        request.expected_server_port,
    )
    if identity != expected:
        raise AdoptionRefused("database_identity_mismatch")
    if revision != (request.expected_schema_revision,):
        raise AdoptionRefused("schema_revision_mismatch")


def measure_stock(dsn: str, departments: tuple[str, ...]) -> StockFingerprint:
    try:
        scope = list(hosted_departements(departments))
    except ValueError:
        raise AdoptionRefused("invalid_departments") from None
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        conn.execute("SET LOCAL DateStyle = 'ISO, YMD'")
        conn.execute("SET LOCAL extra_float_digits = 3")
        conn.execute("SET LOCAL bytea_output = 'hex'")
        columns = source_columns(conn)
        source_rows, raw_digest = conn.execute(raw_multiset_query(columns)).fetchone()
        oid = conn.execute("SELECT 'mutations'::regclass::oid").fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cursor:
            projection = list(
                cursor.execute(_DVF_PROJECTION_SQL, {"departments": scope})
            )
        window = [
            row
            for row in projection
            if "2024-01-01" <= str(row["date_mutation"]) < "2026-01-01"
        ]
    return StockFingerprint(
        source_rows,
        len(projection),
        raw_digest,
        _projection_digest(projection),
        _projection_digest(window),
        len(window),
        oid,
    )


def _projection_digest(rows: list[dict[str, Any]]) -> str:
    hashes = [
        hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).digest()
        for row in rows
    ]
    return hashlib.sha256(b"".join(sorted(hashes))).hexdigest()


def source_columns_for(conn: Any, table: str) -> tuple[SourceColumn, ...]:
    rows = conn.execute(
        """SELECT attname,format_type(atttypid,atttypmod),atttypid,atttypmod,
                  atttypid='geometry'::regtype FROM pg_attribute
             WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped
             ORDER BY attnum""",
        (table,),
    ).fetchall()
    return tuple(SourceColumn(*row) for row in rows)


def _assert_expected(stock: StockFingerprint, request: AdoptionRequest) -> None:
    expected = (
        request.expected_source_rows,
        request.expected_mutation_count,
        request.expected_raw_sha256,
        request.expected_all_years_projection_sha256,
        request.expected_projection_sha256,
        request.expected_projection_count,
    )
    actual = (
        stock.source_rows,
        stock.mutation_count,
        stock.raw_sha256,
        stock.all_years_projection_sha256,
        stock.received_projection_sha256,
        stock.received_projection_count,
    )
    if actual != expected:
        raise AdoptionRefused("stock_fingerprint_mismatch")


def _assert_unchanged(before: StockFingerprint, after: StockFingerprint) -> None:
    if before != after:
        raise AdoptionRefused("source_stock_changed")


def update_dataset_metadata(
    conn: Any, dsn: str, row_count: int, duration: float
) -> None:
    PipelineRunner._update_dataset_metadata(
        dsn,
        "dvf",
        row_count,
        duration,
        __import__("logging").getLogger(__name__),
        connection=conn,
    )


def read_published_receipt(
    dsn: str,
    generation: UUID,
    stock: StockFingerprint,
    expected_oids: dict[str, int],
) -> dict[str, Any]:
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(
            "SELECT generation,status,row_count,relation_oids,"
            "'mutations'::regclass::oid,'mutations_grain'::regclass::oid "
            "FROM etl_publications WHERE dataset_name='dvf' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    expected = (
        generation,
        "published",
        stock.source_rows,
        expected_oids,
        stock.source_oid,
        expected_oids["mutations_grain"],
    )
    if row != expected:
        raise AdoptionRefused("published_receipt_missing")
    return {"status": row[1], "row_count": row[2], "relation_oids": row[3]}


def _revoke_failed_adoption(publication: Publication, cause: BaseException) -> None:
    """Close readers while the publication owner still holds exclusion."""
    changed = publication.connection.execute(
        "UPDATE etl_publications SET status='failed',completed_at=NOW(),"
        "failure_type=%s WHERE id=%s AND generation=%s AND status='published'",
        (type(cause).__name__, publication.id, publication.generation),
    )
    if changed.rowcount != 1:
        raise RuntimeError("Published adoption could not be revoked") from cause


def adopt_existing_dvf_stock(dsn: str, request: AdoptionRequest) -> dict[str, Any]:
    _validate_request(request)
    validate_database_identity(dsn, request)
    started = time.monotonic()
    pipeline = SimpleNamespace(dataset_name="dvf", production_table_name="mutations")
    with dataset_execution_lock(dsn, "dvf"):
        validate_database_identity(dsn, request)
        before = measure_stock(dsn, request.departments)
        _assert_expected(before, request)
        locked_before: StockFingerprint | None = None

        def pre_pending_check(_owner: Any) -> None:
            nonlocal locked_before
            validate_database_identity(dsn, request)
            candidate = measure_stock(dsn, request.departments)
            _assert_unchanged(before, candidate)
            locked_before = candidate

        with Publication(dsn, pipeline, pre_pending_check) as publication:
            if locked_before is None:
                raise RuntimeError("Global pre-pending stock check was not executed")
            try:
                reconstruction = reconstruit_mutations_grain(dsn, request.departments)
                _assert_unchanged(
                    locked_before, measure_stock(dsn, request.departments)
                )
                if publication.observe_stock() != request.expected_source_rows:
                    raise AdoptionRefused("publication_row_count_mismatch")
                result = EtlResult(
                    dataset_name="dvf",
                    stages=[],
                    total_rows_loaded=before.source_rows,
                    total_duration_seconds=time.monotonic() - started,
                    success=True,
                    previous_row_count=before.source_rows,
                )
                result._publication_row_count = publication.row_count
                with publication.required_work() as work:
                    result._publication_work = work
                    ValidationHook().on_run_complete("dvf", result)
                    DependentMatviewHook().on_run_complete("dvf", result)
                    publication.publish(
                        lambda conn: update_dataset_metadata(
                            conn,
                            dsn,
                            publication.row_count,
                            time.monotonic() - started,
                        )
                    )
                result._publication_work = None
                after = measure_stock(dsn, request.departments)
                _assert_unchanged(locked_before, after)
                journal = read_published_receipt(
                    dsn, publication.generation, after, publication.relation_oids
                )
            except BaseException as exc:
                status = publication.connection.execute(
                    "SELECT status FROM etl_publications WHERE id=%s",
                    (publication.id,),
                ).fetchone()
                if status == ("published",):
                    _revoke_failed_adoption(publication, exc)
                raise
            generation = publication.generation
    return {
        "generation": str(generation),
        "generation_kind": "datacommune_observed_stock_adoption",
        "supplier_generation": None,
        "historical_import_identity": "unknown",
        "scope": request.scope,
        "source_stock_preserved": True,
        "source": asdict(after),
        "reconstruction": reconstruction,
        "publication": journal,
    }
