"""Explicit owner-bound wiring for synthetic and preproduction indicator builds."""

from dataclasses import dataclass

import psycopg

from app.commune.indicator_activation import activate_build, read_active_build
from app.commune.indicator_context import (
    IndicatorBuildContext,
    IndicatorTrustInputs,
    assert_indicator_owner,
    build_indicator_context,
)
from app.commune.indicator_projector import ProjectionRequest
from app.commune.indicator_results import (
    PublicResultBuild,
    build_and_store,
    read_build,
)
from app.etl.publication import PublicationWork
from app.etl.schemas import EtlResult


@dataclass(frozen=True)
class BoundIndicatorBuild:
    build_id: int
    build_sha256: str
    inserted: bool
    public_rows: tuple[dict, ...]


def read_bound_build(
    conn: psycopg.Connection,
    *,
    build_id: int,
    context: IndicatorBuildContext,
) -> PublicResultBuild:
    """Read one exact build through the store's full freshness checks."""
    return read_build(conn, build_id=build_id, context=context)


def rebuild_indicator_results(
    conn: psycopg.Connection,
    *,
    trust: IndicatorTrustInputs,
    request: ProjectionRequest,
    publication_work: PublicationWork | None = None,
) -> BoundIndicatorBuild:
    """Build, persist and verify results in the caller-owned transaction."""
    with conn.transaction():
        context = build_indicator_context(
            conn, trust=trust, publication_work=publication_work
        )
        receipt = build_and_store(conn, context=context, request=request)
        loaded = read_bound_build(conn, build_id=receipt.build_id, context=context)
    return BoundIndicatorBuild(
        build_id=receipt.build_id,
        build_sha256=receipt.build_sha256,
        inserted=receipt.inserted,
        public_rows=tuple(loaded.public_dump()),
    )


def rebuild_and_activate_indicator_results(
    conn: psycopg.Connection,
    *,
    trust: IndicatorTrustInputs,
    request: ProjectionRequest,
) -> BoundIndicatorBuild:
    """Build, validate and activate atomically inside one caller savepoint."""
    assert_indicator_owner(conn)
    with conn.transaction():
        built = rebuild_indicator_results(conn, trust=trust, request=request)
        activate_build(conn, build_id=built.build_id, trust=trust)
        loaded = read_active_build(conn, trust=trust)
    return BoundIndicatorBuild(
        built.build_id,
        built.build_sha256,
        built.inserted,
        tuple(loaded.public_dump()),
    )


class CommuneIndicatorHook:
    """Required fixture hook; construction alone never activates it globally."""

    required_on_complete = True

    def __init__(
        self, *, trust: IndicatorTrustInputs, request: ProjectionRequest
    ) -> None:
        self._trust = trust
        self._request = request
        self.last_receipt: BoundIndicatorBuild | None = None

    def on_run_start(self, dataset_name: str) -> None:
        self.last_receipt = None

    def on_run_complete(self, dataset_name: str, result: EtlResult) -> None:
        self.last_receipt = None
        if not result.success:
            return
        if result.dataset_name != dataset_name:
            raise RuntimeError("Hook result dataset differs from its route")
        work = result._publication_work
        if not isinstance(work, PublicationWork):
            raise RuntimeError("Indicator hook requires publication owner work")  # noqa: TRY004
        candidate = work.assert_current(work.connection)
        if candidate.dataset_name != dataset_name:
            raise RuntimeError("Hook dataset differs from publication candidate")
        self.last_receipt = rebuild_indicator_results(
            work.connection,
            trust=self._trust,
            request=self._request,
            publication_work=work,
        )

    def on_run_failure(self, dataset_name: str, error: str) -> None:
        self.last_receipt = None
