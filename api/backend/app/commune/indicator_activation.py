"""Owner-bound durable selection of one exact internal synthetic build."""

from dataclasses import dataclass

import psycopg
from psycopg.rows import tuple_row

from app.commune.indicator_context import (
    IndicatorBuildContext,
    IndicatorTrustInputs,
    assert_indicator_owner,
    build_indicator_context,
)
from app.commune.indicator_results import (
    PublicResultBuild,
    ResultStoreRefused,
    read_build,
)
from app.commune.indicator_storage import PublishedSource


class ActiveBuildRefused(ResultStoreRefused):
    """No exact current activation can be safely returned."""


@dataclass(frozen=True)
class ActivationReceipt:
    event_id: int
    event_type: str
    build_id: int | None


def _published(context: IndicatorBuildContext) -> None:
    if (
        context.scope not in {"synthetic", "preproduction"}
        or len(context.datasets) != 2
    ):
        raise ActiveBuildRefused("active_build_stale")
    if {entry.dataset_name for entry in context.datasets} != {"dvf", "dpe"}:
        raise ActiveBuildRefused("active_build_stale")
    if not all(isinstance(entry.source, PublishedSource) for entry in context.datasets):
        raise ActiveBuildRefused("active_build_source_not_published")


def _append(conn: psycopg.Connection, event_type: str, build_id: int | None):
    with conn.cursor(row_factory=tuple_row) as cursor:
        row = cursor.execute(
            "INSERT INTO commune_indicator_build_activations(event_type,build_id) "
            "VALUES (%s,%s) RETURNING id",
            (event_type, build_id),
        ).fetchone()
    if row is None:
        raise ActiveBuildRefused("active_build_write_failed")
    return ActivationReceipt(row[0], event_type, build_id)


def activate_build(
    conn: psycopg.Connection, *, build_id: int, trust: IndicatorTrustInputs
) -> ActivationReceipt:
    context = build_indicator_context(conn, trust=trust)
    _published(context)
    read_build(conn, build_id=build_id, context=context)
    context.assert_current(conn)
    with conn.transaction():
        receipt = _append(conn, "activate", build_id)
        context.assert_current(conn)
        read_build(conn, build_id=build_id, context=context)
    return receipt


def revoke_active_build(conn: psycopg.Connection) -> ActivationReceipt:
    assert_indicator_owner(conn)
    return _append(conn, "revoke", None)


def read_active_build(
    conn: psycopg.Connection, *, trust: IndicatorTrustInputs
) -> PublicResultBuild:
    assert_indicator_owner(conn)
    if trust.scope not in {"synthetic", "preproduction"}:
        raise ActiveBuildRefused("active_build_stale")
    with conn.cursor(row_factory=tuple_row) as cursor:
        event = cursor.execute(
            "SELECT event_type,build_id FROM commune_indicator_build_activations "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if event is None:
        raise ActiveBuildRefused("active_build_missing")
    if event[0] == "revoke":
        raise ActiveBuildRefused("active_build_revoked")
    context = build_indicator_context(conn, trust=trust)
    _published(context)
    return read_build(conn, build_id=event[1], context=context)
