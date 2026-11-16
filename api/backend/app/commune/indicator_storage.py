"""Connection-injected manifest registry; no transaction or lock ownership.

The caller supplies reviewed trust pins and immutable evidence files. Journal
OID/count identity is not a content checksum. Reads require the caller's guarded
snapshot. Appends additionally verify the existing exclusive publication lock
on THIS connection; the future runner candidate on another session is excluded.
There is no commit, DSN, cache, retry, qualification inference or pending bypass.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from app.commune.dependencies import PUBLICATION_LOCK_ID
from app.commune.indicator_manifest import (
    Dataset,
    ManifestRefused,
    QualificationContext,
    QualifiedManifest,
    StoredManifest,
    load_qualified_manifest,
)

_SOURCES = {
    "dpe": ("diagnostics_dpe", ("diagnostics_dpe",)),
    "dvf": ("mutations", ("mutations", "mutations_grain")),
}
_FIELDS = "id,dataset_name,source_generation,manifest,manifest_sha256,qualified_at"


@dataclass(frozen=True)
class PublishedSource:
    publication_id: int
    dataset_name: Dataset
    generation: UUID
    relation_oids: tuple[tuple[str, int], ...]
    recorded_row_count: int


@dataclass(frozen=True)
class LoadedDecision:
    registry_id: int
    manifest_sha256: str
    source: PublishedSource
    manifest: QualifiedManifest


@dataclass(frozen=True)
class AppendReceipt:
    """Write receipt only; callers must load the latest decision for consumption."""

    registry_id: int
    manifest_sha256: str
    inserted: bool


def _transaction_required(conn: psycopg.Connection) -> None:
    if conn.autocommit:
        raise ManifestRefused("caller_transaction_required")


def _maintenance_required(conn: psycopg.Connection) -> None:
    _transaction_required(conn)
    with conn.cursor(row_factory=tuple_row) as cursor:
        row = cursor.execute(
            """SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_locks
                WHERE locktype='advisory' AND pid=pg_backend_pid()
                  AND database=(SELECT oid FROM pg_database
                                WHERE datname=current_database())
                  AND mode='ExclusiveLock' AND granted
                  AND classid=%s::oid AND objid=%s::oid AND objsubid=1
            )""",
            (PUBLICATION_LOCK_ID >> 32, PUBLICATION_LOCK_ID & 0xFFFFFFFF),
        ).fetchone()
    if row is None or row[0] is not True:
        raise ManifestRefused("caller_maintenance_lock_required")


def read_published_source(
    conn: psycopg.Connection, dataset: Dataset
) -> PublishedSource:
    """Read the latest attempt, never the latest successful older publication."""
    _transaction_required(conn)
    if dataset not in _SOURCES:
        raise ManifestRefused("source_generation_unqualified")
    table, relations = _SOURCES[dataset]
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """SELECT p.*,
                (SELECT jsonb_object_agg(
                    name,to_regclass('public.' || name)::oid::bigint)
                 FROM unnest(%s::text[]) AS names(name)) AS current_oids
               FROM (SELECT id,generation,dataset_name,table_name,status,
                            relation_oids,row_count,completed_at
                     FROM public.etl_publications WHERE dataset_name=%s
                     ORDER BY id DESC LIMIT 1) p""",
            (list(relations), dataset),
        ).fetchone()
    if row is None:
        raise ManifestRefused("source_generation_unqualified")
    oids = row["relation_oids"]
    if (
        row["status"] != "published"
        or row["table_name"] != table
        or not isinstance(row["generation"], UUID)
        or row["completed_at"] is None
        or type(row["row_count"]) is not int
        or row["row_count"] < 0
        or not isinstance(oids, dict)
        or set(oids) != set(relations)
        or any(type(oid) is not int or oid <= 0 for oid in oids.values())
        or oids != row["current_oids"]
    ):
        raise ManifestRefused("source_publication_not_ready")
    return PublishedSource(
        row["id"],
        dataset,
        row["generation"],
        tuple(sorted(oids.items())),
        row["row_count"],
    )


def _current_source(
    conn: psycopg.Connection, context: QualificationContext
) -> PublishedSource:
    source = read_published_source(conn, context.dataset_name)
    if source.generation != context.source_generation:
        raise ManifestRefused("source_generation_unqualified")
    return source


def _stored(row: dict[str, Any]) -> StoredManifest:
    try:
        return StoredManifest.model_validate(
            {k: v for k, v in row.items() if k != "id"}
        )
    except (ValueError, TypeError):
        raise ManifestRefused() from None


def load_current_manifest(
    conn: psycopg.Connection, *, context: QualificationContext, evidence_root: Path
) -> LoadedDecision:
    """Select by id for the current source; no fallback after any later refusal."""
    source = _current_source(conn, context)
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT " + _FIELDS + " FROM public.commune_indicator_manifests "
            "WHERE dataset_name=%s AND source_generation=%s ORDER BY id DESC LIMIT 1",
            (source.dataset_name, source.generation),
        ).fetchone()
    if row is None:
        raise ManifestRefused("source_generation_unqualified")
    record = _stored(row)
    manifest = load_qualified_manifest(
        record, context=context, evidence_root=evidence_root
    )
    return LoadedDecision(row["id"], record.manifest_sha256, source, manifest)


def append_manifest(
    conn: psycopg.Connection,
    *,
    record: StoredManifest,
    context: QualificationContext,
    evidence_root: Path,
) -> AppendReceipt:
    """Append a validated decision under the caller's existing exclusion.

    Exact replay returns the historical row without becoming a newer decision.
    A concurrent unique conflict is compared in full; it never causes UPDATE.
    The caller owns rollback/commit and the immutable evidence lifecycle.
    """
    _maintenance_required(conn)
    _current_source(conn, context)
    load_qualified_manifest(record, context=context, evidence_root=evidence_root)
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "INSERT INTO public.commune_indicator_manifests "
            "(dataset_name,source_generation,manifest,manifest_sha256,qualified_at) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (manifest_sha256) DO NOTHING "
            "RETURNING " + _FIELDS,
            (
                record.dataset_name,
                record.source_generation,
                Jsonb(record.manifest),
                record.manifest_sha256,
                record.qualified_at,
            ),
        ).fetchone()
        inserted = row is not None
        if row is None:
            row = cursor.execute(
                "SELECT " + _FIELDS + " FROM public.commune_indicator_manifests "
                "WHERE manifest_sha256=%s",
                (record.manifest_sha256,),
            ).fetchone()
            if row is None or _stored(row) != record:
                raise ManifestRefused("manifest_collision")
    return AppendReceipt(row["id"], record.manifest_sha256, inserted)
