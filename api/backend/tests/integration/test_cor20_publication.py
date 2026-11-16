"""Real publication barriers and source OIDs in a wrapper-owned child database."""

import asyncio
import importlib.util
import threading
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commune.dependencies import COMMUNE_EDGES, COMMUNE_SOURCES
from app.etl.base import DEPARTEMENTS
from app.etl.publication import (
    Publication,
    dataset_execution_lock,
    requires_fresh_start,
)
from app.etl.runner import PipelineRunner
from tests.etl.test_runner import FakePipeline, _patch_all
from tests.integration.instance_scope_fixture import (
    HOSTABLE_KEYS,
    install_instance_scope,
)


def create_chain(conn):
    conn.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    for view, source in COMMUNE_EDGES[13:]:
        conn.execute(
            sql.SQL("DROP MATERIALIZED VIEW IF EXISTS {} CASCADE").format(
                sql.Identifier(view)
            )
        )
        conn.execute(
            sql.SQL("CREATE MATERIALIZED VIEW {} AS SELECT code_insee FROM {}").format(
                sql.Identifier(view), sql.Identifier(source)
            )
        )
    joins = " ".join(
        f"LEFT JOIN {source} t{n} USING (code_insee)"
        for n, (_view, source) in enumerate(COMMUNE_EDGES[:13])
        if source != "demographie"
    )
    conn.execute(
        "CREATE MATERIALIZED VIEW v_commune_synthese AS "
        "SELECT d.code_insee, t1.version AS nom_commune FROM demographie d " + joins
    )


def create_fixture(conn):
    # Caller owns a unique disposable child DB; never reset a shared schema.
    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
    for table in COMMUNE_SOURCES.values():
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}(code_insee text NOT NULL, version text NOT NULL)"
            ).format(sql.Identifier(table))
        )
        conn.execute(
            sql.SQL("INSERT INTO {} VALUES ('44999','old')").format(
                sql.Identifier(table)
            )
        )
    conn.execute(
        "CREATE TABLE matview_metadata(matview_name text PRIMARY KEY, last_refresh_"
        "status text)"
    )
    conn.execute(
        "CREATE TABLE dataset_metadata(dataset_name text PRIMARY KEY,status text,la"
        "st_updated_at timestamptz,row_count bigint,last_import_duration_seconds fl"
        "oat,source_period_label text,source_updated_at timestamptz,effective_depar"
        "tments text[])"
    )
    conn.execute(
        "INSERT INTO dataset_metadata(dataset_name,status,last_updated_at,row_count"
        ") VALUES('revenus','disponible','2020-01-01',1)"
    )
    conn.execute(
        "CREATE TABLE validation_runs(id bigserial PRIMARY KEY,dataset_name text,ro"
        "w_count_before bigint,row_count_after bigint,row_drop_pct float,null_rate_"
        "findings jsonb,passed boolean,blocked_swap boolean,notes text)"
    )
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/094_publication_generations.py"
    )
    spec = importlib.util.spec_from_file_location(
        "publication_migration", migration_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = SimpleNamespace(execute=conn.execute)
    module.upgrade()
    create_chain(conn)
    # Every guarded route reads the scope first (DATA-03): host all of it.
    install_instance_scope(conn, HOSTABLE_KEYS, DEPARTEMENTS)


@pytest.fixture()
async def published(isolated_pg_dsn, client, monkeypatch):
    from app.database import get_db
    from app.main import app
    from app.middleware import RequestLoggingMiddleware

    with psycopg.connect(isolated_pg_dsn) as conn:
        create_fixture(conn)
    engine = create_async_engine(
        isolated_pg_dsn.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={"server_settings": {"statement_timeout": "500"}},
    )
    sessions = async_sessionmaker(engine)

    async def db():
        async with sessions() as session:
            yield session

    from app.common.publication_readiness import require_publication

    app.dependency_overrides.pop(require_publication, None)
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    try:
        yield isolated_pg_dsn, client
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def read(client):
    return await asyncio.wait_for(
        client.get("/v1/commune", params={"code_insee": "44999"}), 2
    )


@pytest.mark.parametrize("edge", COMMUNE_EDGES)
async def test_every_edge_rejects_retired_oid(published, edge):
    dsn, client = published
    assert (await read(client)).status_code == 200
    _view, source = edge
    with psycopg.connect(dsn) as conn:
        kind = conn.execute(
            "SELECT relkind FROM pg_class WHERE oid=to_regclass(%s)", (source,)
        ).fetchone()[0]
        noun = "MATERIALIZED VIEW" if kind == "m" else "TABLE"
        conn.execute(
            sql.SQL("ALTER " + noun + " {} RENAME TO retired_source").format(
                sql.Identifier(source)
            )
        )
        conn.execute(
            sql.SQL("CREATE TABLE {} AS SELECT * FROM retired_source").format(
                sql.Identifier(source)
            )
        )
    response = await read(client)
    assert response.status_code == 503
    assert "donnees" not in response.json()


@pytest.mark.parametrize("status", ["pending", "failed"])
async def test_durable_incomplete_generation_rejects_even_rebound_views(
    published, status
):
    dsn, client = published
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO etl_publications(generation,dataset_name,table_name,status"
            ") VALUES(gen_random_uuid(),'revenus','revenus',%s)",
            (status,),
        )
    assert (await read(client)).status_code == 503


async def test_missing_view_remains_explicitly_unavailable(published):
    dsn, client = published
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP MATERIALIZED VIEW v_commune_synthese")
    assert (await read(client)).status_code == 503


class PublicationPipeline(FakePipeline):
    dataset_name = "revenus"
    production_table_name = "revenus"
    staging_table_name = "revenus_staging"

    def __init__(self, dsn, fail_swap=False):
        self.dsn = dsn
        self.fail_swap = fail_swap

    def get_sync_dsn(self):
        return self.dsn

    def swap(self):
        if self.fail_swap:
            raise OSError("synthetic disk full")
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DROP TABLE IF EXISTS revenus_old CASCADE")
            conn.execute(
                "CREATE TABLE revenus_staging AS SELECT * FROM revenus WHERE code_i"
                "nsee='44999'"
            )
            conn.execute("UPDATE revenus_staging SET version='new'")
            conn.execute("INSERT INTO revenus_staging VALUES ('45000','retained')")
            conn.execute("ALTER TABLE revenus RENAME TO revenus_old")
            conn.execute("ALTER TABLE revenus_staging RENAME TO revenus")


@pytest.mark.parametrize("failure", [None, "before_swap", "required_hook", "metadata"])
async def test_runner_publication_two_connections(published, failure):
    dsn, client = published
    assert (await read(client)).json()["donnees"][0]["nom_commune"] == "old"
    before_metadata = PipelineRunner._update_dataset_metadata
    observations = []

    class RequiredHook:
        required_on_complete = True

        def on_run_start(self, *_):
            pass

        def on_run_failure(self, *_):
            pass

        def on_run_complete(self, *_):
            with psycopg.connect(dsn) as conn:
                observations.append(
                    conn.execute(
                        "SELECT status FROM etl_publications ORDER BY id DESC LIMIT 1"
                    ).fetchone()[0]
                )
                create_chain(conn)
            if failure == "required_hook":
                raise RuntimeError("synthetic required rejection")

    def execute():
        with _patch_all(staging_exists=False, checkpoint_data=None):  # noqa: SIM117
            with (
                patch("app.etl.runner.Publication", Publication),
                patch("app.etl.runner.dataset_execution_lock", dataset_execution_lock),
                patch("app.etl.runner.requires_fresh_start", requires_fresh_start),
                patch.object(
                    PipelineRunner,
                    "_update_dataset_metadata",
                    staticmethod(
                        before_metadata
                        if failure != "metadata"
                        else lambda *_a, **_kw: (_ for _ in ()).throw(
                            OSError("synthetic disk full")
                        )
                    ),
                ),
            ):
                return PipelineRunner([RequiredHook()]).run(
                    PublicationPipeline(dsn, failure == "before_swap")
                )

    result = await asyncio.to_thread(execute)
    with psycopg.connect(dsn) as conn:
        state, count = conn.execute(
            "SELECT status,row_count FROM etl_publications ORDER BY id DESC LIMIT 1"
        ).fetchone()
        metadata = conn.execute(
            "SELECT row_count,last_updated_at::date::text FROM dataset_metadata WHE"
            "RE dataset_name='revenus'"
        ).fetchone()
    assert result.success is (failure is None)
    if failure is None:
        assert state == "published" and count == 2
        assert metadata[0] == 2  # Full stock, while the run's staged counter is zero.
        assert (await read(client)).json()["donnees"][0]["nom_commune"] == "new"
    else:
        assert state == "failed"
        assert metadata == (1, "2020-01-01")
        assert (await read(client)).status_code == 503
    if failure != "before_swap":
        assert observations == ["pending"]


async def test_cli_scheduler_share_lock_and_reader_sees_pending(published, monkeypatch):
    from app.scheduler import jobs

    dsn, client = published
    entered, release = threading.Event(), threading.Event()
    metadata_update = PipelineRunner._update_dataset_metadata

    class BarrierHook:
        required_on_complete = True

        def on_run_start(self, *_):
            pass

        def on_run_failure(self, *_):
            pass

        def on_run_complete(self, *_):
            entered.set()
            assert release.wait(5), "bounded barrier timed out"
            with psycopg.connect(dsn) as conn:
                create_chain(conn)

    def writer():
        with _patch_all(staging_exists=False, checkpoint_data=None):  # noqa: SIM117
            with (
                patch("app.etl.runner.Publication", Publication),
                patch("app.etl.runner.dataset_execution_lock", dataset_execution_lock),
                patch("app.etl.runner.requires_fresh_start", requires_fresh_start),
                patch.object(
                    PipelineRunner,
                    "_update_dataset_metadata",
                    staticmethod(metadata_update),
                ),
            ):
                return PipelineRunner([BarrierHook()]).run(PublicationPipeline(dsn))

    monkeypatch.setitem(jobs.PIPELINE_MAP, "revenus", lambda: PublicationPipeline(dsn))
    monkeypatch.setattr(jobs, "build_default_hooks", lambda: [])
    future = asyncio.create_task(asyncio.to_thread(writer))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert (await read(client)).status_code == 503
        # Real scheduler entrypoint reaches the same runner exclusion as CLI.
        await asyncio.to_thread(jobs.run_scheduled_pipeline_with_retry, "revenus")
        with pytest.raises(RuntimeError, match="Advisory lock"):
            await asyncio.to_thread(PipelineRunner().run, PublicationPipeline(dsn))
        with psycopg.connect(dsn) as conn:
            assert (
                conn.execute("SELECT count(*) FROM etl_publications").fetchone()[0] == 1
            )
    finally:
        release.set()
    assert (await future).success
    assert (await read(client)).status_code == 200


async def test_failed_error_journal_leaves_durable_pending(published):
    dsn, client = published

    class FailedJournal:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, query, *args):
            if "SET status='failed'" in str(query):
                raise OSError("synthetic journal disk full")
            return self.conn.execute(query, *args)

    with (
        pytest.raises(OSError),
        Publication(dsn, PublicationPipeline(dsn)) as publication,
    ):
        publication.connection = FailedJournal(publication.connection)
        raise OSError("synthetic publication disk full")
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute("SELECT status FROM etl_publications").fetchone()[0]
            == "pending"
        )
    assert (await read(client)).status_code == 503


@pytest.mark.parametrize("invalid", [False, True])
async def test_real_required_validation_preserves_optional_nulls(
    published, monkeypatch, invalid
):
    from app.data_quality.profiles import PROFILES, DatasetProfile, NullSpec
    from app.etl.hooks import ValidationHook
    from app.etl.registry import registry

    dsn, _client = published
    monkeypatch.setitem(
        PROFILES,
        "revenus",
        DatasetProfile(
            dataset="revenus",
            table="revenus",
            nulls=NullSpec(
                required_cols=("code_insee",), expected_cols=("optional_value",)
            ),
        ),
    )
    monkeypatch.setitem(registry._pipelines, "revenus", PublicationPipeline)
    with psycopg.connect(dsn) as conn:
        conn.execute("ALTER TABLE revenus ADD COLUMN optional_value text")
        if invalid:
            conn.execute("ALTER TABLE revenus ALTER COLUMN code_insee DROP NOT NULL")
            conn.execute("UPDATE revenus SET code_insee=NULL")
    from app.etl.schemas import EtlResult

    result = EtlResult(
        dataset_name="revenus",
        stages=[],
        total_rows_loaded=0,
        total_duration_seconds=0,
        success=True,
    )
    result._publication_row_count = 1
    monkeypatch.setattr("app.config.settings.database_url", dsn)
    if invalid:
        with pytest.raises(RuntimeError, match="validation rejected"):
            ValidationHook().on_run_complete("revenus", result)
    else:
        ValidationHook().on_run_complete("revenus", result)
    with psycopg.connect(dsn) as conn:
        assert conn.execute(
            "SELECT passed,row_count_after FROM validation_runs"
        ).fetchone() == (not invalid, 1)


@pytest.mark.parametrize("dataset", sorted(COMMUNE_SOURCES))
@pytest.mark.parametrize("state", ["pending", "failed"])
async def test_all_thirteen_raw_route_mappings_are_guarded(published, dataset, state):
    dsn, client = published
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO etl_publications(generation,dataset_name,table_name,status"
            ") VALUES(gen_random_uuid(),%s,%s,%s)",
            (dataset, COMMUNE_SOURCES[dataset], state),
        )
    path = "/v1/" + dataset.replace("_", "-")
    response = await client.get(
        path, params={"code_insee": "44999", "lat": 47, "lon": -1}
    )
    assert response.status_code == 503, (dataset, response.text)
    assert "donnees" not in response.json()


async def test_raw_guard_shared_lock_protects_following_select(published):
    from sqlalchemy import text

    from app.common.publication_readiness import guard_publication

    dsn, _client = published
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    sessions = async_sessionmaker(engine)
    try:
        async with sessions() as session:
            await guard_publication(session, ["revenus"])
            with (
                pytest.raises(psycopg.errors.LockNotAvailable),
                Publication(dsn, PublicationPipeline(dsn)),
            ):
                pytest.fail("writer entered while reader transaction remained open")
            assert (
                await session.execute(
                    text("SELECT version FROM revenus WHERE code_insee='44999'")
                )
            ).scalar_one() == "old"
        with Publication(dsn, PublicationPipeline(dsn)):
            pass
    finally:
        await engine.dispose()


async def test_unknown_guard_dataset_is_rejected_without_sql(published):
    from app.common.publication_readiness import guard_publication

    session = AsyncMock()
    with pytest.raises(ValueError, match="Unknown"):
        await guard_publication(session, ["unknown"])
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "path,params",
    [
        ("/v1/zone/stats", {"type": "commune", "code": "44999"}),
        ("/v1/zone/resolve", {"type": "commune", "code": "44999", "datasets": "dpe"}),
    ],
)
async def test_other_explicit_dpe_readers_are_guarded(published, path, params):
    dsn, client = published
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO etl_publications(generation,dataset_name,table_name,status"
            ") VALUES(gen_random_uuid(),'dpe','diagnostics_dpe','pending')"
        )
    assert (await client.get(path, params=params)).status_code == 503


@pytest.mark.parametrize("path", ["/v1/commune", "/v1/revenus"])
async def test_missing_journal_is_unavailable(published, path):
    dsn, client = published
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE etl_publications")
    response = await client.get(path, params={"code_insee": "44999"})
    assert response.status_code == 503
    assert "donnees" not in response.json()


async def test_real_app_quota_commit_precedes_guard_and_data_read(
    published, monkeypatch
):
    from typing import Annotated

    from fastapi import Depends
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.auth.dependencies import check_quota_hybrid
    from app.common.publication_readiness import require_publication
    from app.database import get_db
    from app.main import app
    from app.revenus import router as revenus_module
    from tests.conftest import _fake_key_info

    dsn, client = published
    route = next(
        route for route in app.routes if getattr(route, "path", None) == "/v1/revenus"
    )
    calls = [dependency.call for dependency in route.dependant.dependencies]
    assert calls.index(check_quota_hybrid) < calls.index(require_publication)
    events = []

    async def quota(db: Annotated[AsyncSession, Depends(get_db)]):
        await db.execute(text("SELECT 1"))
        await db.commit()
        events.append(("quota_committed", db))
        return _fake_key_info()

    async def rows(db, codes):
        assert events == [("quota_committed", db)]
        with (
            pytest.raises(psycopg.errors.LockNotAvailable),
            Publication(dsn, PublicationPipeline(dsn)),
        ):
            pytest.fail("publication entered between guard and source read")
        assert (
            await db.execute(
                text("SELECT version FROM revenus WHERE code_insee='44999'")
            )
        ).scalar_one() == "old"
        events.append(("source_read", db))
        return []

    app.dependency_overrides[check_quota_hybrid] = quota
    monkeypatch.setattr(revenus_module, "revenus_par_codes", rows)
    response = await client.get("/v1/revenus", params={"code_insee": "44999"})
    assert response.status_code == 200
    assert [event[0] for event in events] == ["quota_committed", "source_read"]


@pytest.mark.parametrize("pool_size", [1, 2])
async def test_repeatable_snapshot_admission_is_bounded_and_observes_latest(
    published, pool_size
):
    import time

    from fastapi import HTTPException
    from sqlalchemy import text

    from app.common.publication_readiness import (
        ADMISSION_UNAVAILABLE,
        PUBLICATION_NOT_VALIDATED,
        guard_snapshot_publication,
    )

    dsn, _client = published
    # pool_size=1: the main transaction holds the only connection, admission
    # waits for a second one. The admission checkout bound, not pool_timeout
    # (30 s), breaks that hold-and-wait: an independent 3 s ceiling.
    ceiling = 1.0 if pool_size > 1 else 3.0
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=30,
    )
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            # The SET has not frozen the first data snapshot. Another connection
            # commits an unavailable generation before its acquisition.
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    "INSERT INTO etl_publications(generation,dataset_name,"
                    "table_name,status) "
                    "VALUES(gen_random_uuid(),'dpe','diagnostics_dpe','fai"
                    "led')"
                )
            started = time.perf_counter()
            with pytest.raises(HTTPException) as rejected:
                await guard_snapshot_publication(session, ["dpe"])
            assert rejected.value.status_code == 503
            assert time.perf_counter() - started < ceiling
            if pool_size > 1:
                assert rejected.value.detail == PUBLICATION_NOT_VALIDATED
            else:
                # Expired admission checkout (asyncio.timeout), not the pool's
                # own timeout (sqlalchemy.exc.TimeoutError).
                assert rejected.value.detail == ADMISSION_UNAVAILABLE
                assert type(rejected.value.__cause__) is TimeoutError
        # The abandoned checkout got the freed connection and returned it.
        assert await _drain_admission_tasks() == 0
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        await _drain_then_dispose(engine)


def _admission_engine(dsn, *, checkout_delay):
    """Two pooled, pre-pinged connections; later checkouts take *checkout_delay*.

    The delay runs inside the pool checkout (pre-ping included) without
    blocking the loop: it stands for a slow pool, a reconnection or a brief
    stall of the process, never for a writer.
    """
    from sqlalchemy import event
    from sqlalchemy.util import await_only

    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
    )
    delayed = []

    def slow_checkout(*_args):
        if delayed:
            await_only(asyncio.sleep(checkout_delay))

    event.listen(engine.sync_engine.pool, "checkout", slow_checkout)
    return engine, delayed


async def _start_snapshot(engine, session, delayed):
    from sqlalchemy import text

    # Warm both pool slots so the admission checkout is a pre-pinged reuse.
    async with engine.connect() as first, engine.connect() as second:
        await first.execute(text("SELECT 1"))
        await second.execute(text("SELECT 1"))
    await session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    )
    # Only the admission connection, checked out next, is slow.
    delayed.append(True)


async def test_slow_admission_checkout_is_not_writer_contention(published):
    """A checkout slower than the 250 ms budget no longer refuses the read."""
    import time

    from sqlalchemy import text

    from app.common.publication_readiness import guard_snapshot_publication
    from app.commune.dependencies import PUBLICATION_LOCK_ID

    dsn, _client = published
    engine, delayed = _admission_engine(dsn, checkout_delay=0.4)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await _start_snapshot(engine, session, delayed)
            started = time.perf_counter()
            await guard_snapshot_publication(session, ["dpe"])
            assert time.perf_counter() - started >= 0.4, "delay not applied"
            # The main transaction kept its shared lock past admission.
            held = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                        "AND pid=pg_backend_pid() AND mode='ShareLock' "
                        "AND granted AND classid=:hi AND objid=:lo"
                    ),
                    {
                        "hi": PUBLICATION_LOCK_ID >> 32,
                        "lo": PUBLICATION_LOCK_ID & 0xFFFFFFFF,
                    },
                )
            ).scalar_one()
            assert held == 1
    finally:
        await _drain_then_dispose(engine)


# Contract A (261013-adm, after the GPT review): once the admission connection
# is obtained, a real writer is refused at once by the reader try-lock. The
# whole refusal takes at most the checkout bound (2 s) plus the 250 ms budget:
# a slow checkout delays the refusal, it never admits the read nor fakes a
# writer. Literal ceiling, independent of the module constants.
_WRITER_REFUSAL_CEILING_S = 2.25


@pytest.mark.parametrize("checkout_delay", [0.0, 0.4, 3.0])
async def test_writer_is_refused_within_checkout_bound_plus_budget(
    published, checkout_delay
):
    """A writer holding the lock: 503, never an admitted read, within 2.25 s."""
    import time

    from fastapi import HTTPException

    from app.common.publication_readiness import (
        ADMISSION_UNAVAILABLE,
        PUBLICATION_LOCKED,
        guard_snapshot_publication,
    )
    from app.commune.dependencies import PUBLICATION_LOCK_ID

    dsn, _client = published
    engine, delayed = _admission_engine(dsn, checkout_delay=checkout_delay)
    try:
        with psycopg.connect(dsn, autocommit=True) as writer:
            writer.execute("SELECT pg_advisory_lock(%s)", (PUBLICATION_LOCK_ID,))
            async with async_sessionmaker(engine)() as session, session.begin():
                await _start_snapshot(engine, session, delayed)
                started = time.perf_counter()
                with pytest.raises(HTTPException) as refused:
                    await guard_snapshot_publication(session, ["dpe"])
                elapsed = time.perf_counter() - started
        assert refused.value.status_code == 503
        assert elapsed < _WRITER_REFUSAL_CEILING_S
        if checkout_delay < 2.0:
            # Connection obtained: refused by the writer's lock, not a budget.
            assert refused.value.detail == PUBLICATION_LOCKED
            assert refused.value.__cause__ is None
            # The refusal waited for the slow checkout (the accepted cost).
            assert elapsed >= checkout_delay
        else:
            # Checkout never completed: refused when its own bound expires.
            assert refused.value.detail == ADMISSION_UNAVAILABLE
            assert type(refused.value.__cause__) is TimeoutError
    finally:
        await _drain_then_dispose(engine)


async def _drain_admission_tasks():
    """Wait (10 s at most, never cancelling) for background admission tasks.

    Returns how many are still running, so a test can report them.
    """
    from app.common.publication_readiness import drain_admission_tasks

    return await drain_admission_tasks(10)


async def _drain_then_dispose(engine):
    """Drain without cancelling, then always dispose; report what remained."""
    try:
        remaining = await _drain_admission_tasks()
    finally:
        await engine.dispose()
    assert remaining == 0, f"{remaining} admission task(s) still running"


async def test_expired_admission_checkout_leaks_no_pool_slot(published, monkeypatch):
    """Expire the checkout bound while the pool replaces dead connections.

    Real driver (asyncpg). Each round warms the pool, terminates its backends,
    then admits under a tiny bound: the admission checkout's pre-ping fails and
    the pool invalidates and reconnects while the bound expires. A checkout
    cancelled there loses its pool slot (SQLAlchemy 2.0.46). Afterwards no
    connection may stay checked out, the next admission and read succeed, and
    disposing the engine closes every backend it opened.
    """
    import time
    from uuid import uuid4

    from fastapi import HTTPException
    from sqlalchemy import text

    from app.common import publication_readiness as readiness

    dsn, _client = published
    name = "adm-cancel-" + uuid4().hex[:12]
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"server_settings": {"application_name": name}},
    )
    sessions = async_sessionmaker(engine)
    tiny = (0.0, 0.0005, 0.001, 0.002, 0.004, 0.008)
    pings = {"failed": 0, "cancelled": 0}
    expired = 0
    dialect = engine.sync_engine.dialect
    real_ping = dialect.do_ping

    def ping(dbapi_connection):
        try:
            return real_ping(dbapi_connection)
        except asyncio.CancelledError:
            pings["cancelled"] += 1
            raise
        except Exception:
            pings["failed"] += 1
            raise

    monkeypatch.setattr(dialect, "do_ping", ping)

    def backends():
        with psycopg.connect(dsn, autocommit=True) as admin:
            return admin.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
                (name,),
            ).fetchone()[0]

    async def admit():
        async with sessions() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await readiness.guard_snapshot_publication(session, ["dpe"])
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1

    try:
        for round_ in range(30):
            monkeypatch.setattr(readiness, "ADMISSION_CHECKOUT_TIMEOUT_S", 2.0)
            await admit()  # both pool slots live
            with psycopg.connect(dsn, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE application_name=%s",
                    (name,),
                )
            monkeypatch.setattr(
                readiness, "ADMISSION_CHECKOUT_TIMEOUT_S", tiny[round_ % len(tiny)]
            )
            try:
                await admit()
            except HTTPException as exc:
                assert exc.detail == readiness.ADMISSION_UNAVAILABLE
                assert type(exc.__cause__) is TimeoutError
                expired += 1
        assert await _drain_admission_tasks() == 0
        print(
            f"ADMISSION_EXPIRY rounds=30 expired={expired} "
            f"failed_pings={pings['failed']} cancelled_pings={pings['cancelled']} "
            f"{engine.sync_engine.pool.status()}"
        )
        assert expired, "no admission expired: the test proves nothing"
        assert pings["failed"], "no dead connection was pre-pinged"
        assert engine.sync_engine.pool.checkedout() == 0

        monkeypatch.undo()
        await admit()
        assert await _drain_admission_tasks() == 0
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        await _drain_then_dispose(engine)
    deadline = time.monotonic() + 5
    while backends() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert backends() == 0


# KI-P1: a failure between the committed swap and the publication.
#
# The swap commits on its own connection (``BasePipeline.swap_tables``): from
# then on the production table holds the new generation while the commune
# views stay bound to the previous one, which is a mixture. Only the journal
# row (``etl_publications``) and the reader guard stand between that mixture
# and a response. These tests inject the failure right after the real swap,
# at three points the runner reaches before ``Publication.publish``:
#
# - ``observe``: the stock measurement raises (exception, journal ``failed``);
# - ``required``: a required hook raises (exception, journal ``failed``);
# - ``crash``: the process dies (no ``__exit__``, journal stays ``pending``).
#
# Each case checks the mixture is physically present, the API refuses it
# (/v1/commune, and the journal guard alone: /v1/commune also checks the view
# edges, which would hide a broken journal check), and the next run discards
# resume evidence (``requires_fresh_start``) and publishes cleanly.


class RealSwapPipeline(FakePipeline):
    """Minimal pipeline whose swap is the production two-RENAME swap."""

    dataset_name = "revenus"
    production_table_name = "revenus"
    staging_table_name = "revenus_staging"

    def __init__(self, dsn):
        self.dsn = dsn
        self.dropped_staging = 0

    def get_sync_dsn(self):
        return self.dsn

    def drop_staging_table(self, dsn):
        self.dropped_staging += 1
        super().drop_staging_table(dsn)

    def swap(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DROP TABLE IF EXISTS revenus_staging")
            conn.execute(
                "CREATE TABLE revenus_staging AS "
                "SELECT code_insee, 'new'::text AS version FROM revenus"
            )
        self.swap_tables(self.dsn)


class _Hook:
    required_on_complete = True

    def __init__(self, dsn, fail):
        self.dsn = dsn
        self.fail = fail

    def on_run_start(self, *_):
        pass

    def on_run_failure(self, *_):
        pass

    def on_run_complete(self, *_):
        if self.fail:
            raise RuntimeError("synthetic required failure after swap")
        with psycopg.connect(self.dsn) as conn:
            create_chain(conn)


def _crash_exit(publication, *_):
    # A killed process runs no handler: the server ends the session, which
    # releases the advisory lock, and the committed pending row remains.
    publication.connection.close()


def _observe_failure(_publication):
    raise OSError("synthetic stock measurement failure")


def _run(pipeline, hooks, *, resume_evidence=False, injections=()):
    evidence = {"loaded_files": ["leftover.csv"]} if resume_evidence else None
    with (
        _patch_all(staging_exists=resume_evidence, checkpoint_data=evidence),
        patch("app.etl.runner.Publication", Publication),
        patch("app.etl.runner.dataset_execution_lock", dataset_execution_lock),
        patch("app.etl.runner.requires_fresh_start", requires_fresh_start),
        ExitStack() as stack,
    ):
        for target, value in injections:
            stack.enter_context(patch.object(Publication, target, value))
        return PipelineRunner(hooks).run(pipeline)


async def _journal_guard_refuses(dsn):
    """The reader guard alone, without the view-edge check of /v1/commune."""
    from fastapi import HTTPException

    from app.common.publication_readiness import guard_publication

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with async_sessionmaker(engine)() as session:
            try:
                await guard_publication(session, ["revenus"])
            except HTTPException as refused:
                assert refused.status_code == 503
                return True
            return False
    finally:
        await engine.dispose()


def _journal(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT status FROM etl_publications WHERE dataset_name='revenus' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]


def _stored(dsn):
    with psycopg.connect(dsn) as conn:
        table = conn.execute(
            "SELECT version FROM revenus WHERE code_insee='44999'"
        ).fetchone()[0]
        view = conn.execute(
            "SELECT nom_commune FROM v_commune_synthese WHERE code_insee='44999'"
        ).fetchone()
    return table, view[0] if view else None


_CASES = {
    "observe": ("failed", False, [("observe_stock", _observe_failure)]),
    "required": ("failed", True, []),
    "crash": ("pending", True, [("__exit__", _crash_exit)]),
}


@pytest.mark.parametrize("case", list(_CASES))
async def test_failure_after_swap_is_never_served_and_next_run_restarts(
    published, case
):
    dsn, client = published
    expected_state, hook_fails, injections = _CASES[case]
    assert (await read(client)).json()["donnees"][0]["nom_commune"] == "old"

    result = await asyncio.to_thread(
        _run,
        RealSwapPipeline(dsn),
        [_Hook(dsn, fail=hook_fails)],
        injections=injections,
    )
    assert result.success is False

    # The swap committed: production holds the new generation while the
    # commune view still reads the previous one. That mixture exists.
    assert _stored(dsn) == ("new", "old")
    assert _journal(dsn) == expected_state
    assert requires_fresh_start(dsn, "revenus")
    response = await read(client)
    assert response.status_code == 503, response.text
    assert await _journal_guard_refuses(dsn)

    # Next run: leftover checkpoint and staging must be discarded, not resumed.
    # Only the fresh-start branch drops staging on a successful run.
    pipeline = RealSwapPipeline(dsn)
    result = await asyncio.to_thread(
        _run, pipeline, [_Hook(dsn, fail=False)], resume_evidence=True
    )
    assert result.success, result.error
    assert pipeline.dropped_staging == 1
    assert "resume_stock" not in [stage.stage_name for stage in result.stages]
    assert _journal(dsn) == "published"
    assert not requires_fresh_start(dsn, "revenus")
    assert _stored(dsn) == ("new", "new")
    assert not await _journal_guard_refuses(dsn)
    response = await read(client)
    assert response.status_code == 200, response.text
    assert response.json()["donnees"][0]["nom_commune"] == "new"
