"""Real PostgreSQL proofs for the DPE inventory snapshot and generations.

Run only through the disposable PostGIS harness::

    pwsh -NoProfile -File scripts/run_contract_integration.ps1 \
        tests/integration/test_dpe_inventory_generation.py

The tests use the production query/service code.  A second psycopg connection
performs and observes concurrent writes or DDL; deterministic events place the
operations on either side of the exact service boundary under test.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.dpe import queries, references
from app.dpe.inventory_service import (
    load_dpe_document,
    load_dpe_inventory,
    load_dpe_inventory_result,
)
from app.dpe.references import RefreshRequiredError, verify_context
from app.resolve import multi as resolve_multi
from tests.integration.publication_fixture import install_publication_journal


@pytest.fixture(scope="module")
def pg_dsn(isolated_pg_dsn):
    """Keep this module's destructive public fixtures out of the shared schema."""
    return isolated_pg_dsn


pytestmark = pytest.mark.asyncio

BAN_ID = "44999_0001_00042"
REUSED_BAN_ID = "44999_0001_00999"
ADDRESS_ID = 90_800_001
DOCUMENT_ID = 90_800_101
DPE_NUMBER = "DPE-SYNTHETIQUE-ORIGINAL"
REUSED_DPE_NUMBER = "DPE-SYNTHETIQUE-REUTILISE"

_DROP_RELATIONS = """
    DROP TABLE IF EXISTS public.diagnostics_dpe_retired CASCADE;
    DROP TABLE IF EXISTS public.diagnostics_dpe_new CASCADE;
    DROP TABLE IF EXISTS public.diagnostics_dpe CASCADE;
    DROP TABLE IF EXISTS public.adresses_retired CASCADE;
    DROP TABLE IF EXISTS public.adresses_new CASCADE;
    DROP TABLE IF EXISTS public.adresses CASCADE;
"""

_CREATE_RELATIONS = """
    CREATE TABLE public.adresses (
        id bigint NOT NULL,
        code_departement text NOT NULL,
        ban_id text,
        PRIMARY KEY (id, code_departement)
    ) PARTITION BY LIST (code_departement);

    CREATE TABLE public.adresses_44
        PARTITION OF public.adresses FOR VALUES IN ('44');

    CREATE TABLE public.diagnostics_dpe (
        id bigint PRIMARY KEY,
        numero_dpe text NOT NULL,
        numero_dpe_remplace text,
        identifiant_ban text,
        code_departement text NOT NULL,
        type_batiment text,
        surface_habitable numeric,
        etiquette_dpe text,
        etiquette_ges text,
        date_etablissement_dpe date,
        date_fin_validite_dpe date,
        annee_construction integer,
        periode_construction text,
        type_installation_chauffage text,
        type_energie_principale_chauffage text,
        qualite_isolation_enveloppe text,
        conso_5_usages_ep numeric,
        emission_ges_5_usages numeric,
        cout_total_5_usages numeric,
        regime_dpe text,
        source_observations jsonb NOT NULL DEFAULT '[]'::jsonb,
        source_state text NOT NULL DEFAULT 'unknown',
        source_snapshot_at timestamptz,
        is_tombstone boolean NOT NULL DEFAULT false,
        lifecycle_facts jsonb NOT NULL DEFAULT
            '{"replacement_state":"not_checked","graph_issues":[],"date_issues":[]}'::jsonb,
        revision_evidence jsonb NOT NULL DEFAULT
            '{"status":"unverified","kind":null,"revision_date":null,"verified_at":null,"evidence_sha256":null,"official_source":null}'::jsonb,
        numero_etage_appartement integer,
        complement_adresse_logement text
    );
"""


@dataclass(frozen=True, slots=True)
class InventoryDatabase:
    """Disposable schema plus the service's real async session factory."""

    pg_dsn: str
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]


def _connect(pg_dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(
        pg_dsn,
        connect_timeout=5,
        autocommit=autocommit,
        options="-c statement_timeout=15000 -c lock_timeout=10000",
    )


def _insert_document(
    cursor: psycopg.Cursor,
    *,
    table: str,
    document_id: int,
    dpe_number: str,
    ban_id: str = BAN_ID,
    energy_class: str = "D",
) -> None:
    if table not in {"diagnostics_dpe", "diagnostics_dpe_new"}:
        raise AssertionError("unexpected synthetic relation")
    cursor.execute(
        f"""
        INSERT INTO public.{table} (
            id, numero_dpe, numero_dpe_remplace, identifiant_ban,
            code_departement, type_batiment, surface_habitable,
            etiquette_dpe, etiquette_ges, date_etablissement_dpe,
            date_fin_validite_dpe, annee_construction, periode_construction,
            type_installation_chauffage,
            type_energie_principale_chauffage,
            qualite_isolation_enveloppe, conso_5_usages_ep,
            emission_ges_5_usages, cout_total_5_usages
        ) VALUES (
            %s, %s, NULL, %s, '44', 'appartement', 61.5,
            %s, 'C', DATE '2024-01-15', DATE '2034-01-14', 1988,
            '1980-1989', 'individuel', 'electricite', 'moyenne',
            12345, 678, 1450
        )
        """,
        (document_id, dpe_number, ban_id, energy_class),
    )


def _set_facts(
    cursor: psycopg.Cursor,
    *,
    table: str,
    state: str = "present",
    replacement: str = "no_successor_known",
) -> None:
    """Synthetic fixture writes only; publication tests prepare the new relation."""
    from psycopg.types.json import Jsonb

    assert table in {"diagnostics_dpe", "diagnostics_dpe_new"}
    cursor.execute(
        f"""
        UPDATE public.{table} SET regime_dpe = 'existant', source_state = %s,
            source_snapshot_at = %s, is_tombstone = %s,
            lifecycle_facts = %s, source_observations = %s
        """,
        (
            state,
            datetime(2026, 9, 5, 22, 30, tzinfo=UTC),
            state == "absent_from_complete_scope",
            Jsonb(
                {
                    "replacement_state": replacement,
                    "graph_issues": [],
                    "date_issues": [],
                }
            ),
            Jsonb(
                [
                    {
                        "source_slug": "dpe03existant",
                        "department": "44",
                        "source_version": "a" * 64,
                        "import_id": "synthetic-import",
                        "observed_at": "2026-09-05T22:30:00Z",
                        "last_seen_at": "2026-09-04T12:00:00Z",
                        "state": state,
                    }
                ]
            ),
        ),
    )


def _create_schema(pg_dsn: str) -> None:
    with (
        _connect(pg_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(_DROP_RELATIONS)
        cursor.execute(_CREATE_RELATIONS)
        install_publication_journal(connection)
        cursor.execute(
            """
            INSERT INTO public.adresses
                (id, code_departement, ban_id)
            VALUES (%s, '44', %s)
            """,
            (ADDRESS_ID, BAN_ID),
        )
        _insert_document(
            cursor,
            table="diagnostics_dpe",
            document_id=DOCUMENT_ID,
            dpe_number=DPE_NUMBER,
        )


def _drop_schema(pg_dsn: str) -> None:
    with _connect(pg_dsn, autocommit=True) as connection:
        connection.execute(_DROP_RELATIONS)


@pytest.fixture()
def inventory_schema(pg_dsn: str) -> Iterator[str]:
    """Publish a minimal production-shaped schema visible to all connections."""
    _create_schema(pg_dsn)
    try:
        yield pg_dsn
    finally:
        _drop_schema(pg_dsn)


@pytest.fixture()
async def inventory_db(inventory_schema: str) -> AsyncIterator[InventoryDatabase]:
    engine = create_async_engine(
        inventory_schema.replace("postgresql://", "postgresql+asyncpg://"),
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield InventoryDatabase(inventory_schema, engine, factory)
    finally:
        await engine.dispose()


def _relation_generations(pg_dsn: str) -> tuple[int, int, int]:
    with (
        _connect(pg_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT 'public.adresses'::regclass::oid::bigint,
                   a.tableoid::bigint,
                   'public.diagnostics_dpe'::regclass::oid::bigint
            FROM public.adresses AS a
            WHERE a.id = %s
            """,
            (ADDRESS_ID,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), int(row[2])


def _mutate_between_page_and_aggregate(pg_dsn: str) -> None:
    """Commit a write with unchanged relation OIDs from a second connection."""
    with _connect(pg_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                UPDATE public.diagnostics_dpe
                SET etiquette_dpe = 'F'
                WHERE id = %s
                """,
            (DOCUMENT_ID,),
        )
        _insert_document(
            cursor,
            table="diagnostics_dpe",
            document_id=DOCUMENT_ID + 1,
            dpe_number="DPE-SYNTHETIQUE-INSERE",
            energy_class="A",
        )
        _set_facts(cursor, table="diagnostics_dpe", replacement="replaced")


def _observed_inventory(pg_dsn: str) -> tuple[int, list[str]]:
    with (
        _connect(pg_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT count(*)::integer,
                   array_agg(etiquette_dpe ORDER BY id)
            FROM public.diagnostics_dpe
            WHERE identifiant_ban = %s
            """,
            (BAN_ID,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0]), list(row[1])


def _prepare_swap(pg_dsn: str) -> None:
    with _connect(pg_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                CREATE TABLE public.adresses_new (
                    id bigint NOT NULL,
                    code_departement text NOT NULL,
                    ban_id text,
                    PRIMARY KEY (id, code_departement)
                ) PARTITION BY LIST (code_departement);

                CREATE TABLE public.adresses_new_44
                    PARTITION OF public.adresses_new FOR VALUES IN ('44');
                """
        )
        cursor.execute(
            """
                INSERT INTO public.adresses_new
                    (id, code_departement, ban_id)
                VALUES (%s, '44', %s)
                """,
            (ADDRESS_ID, BAN_ID),
        )
        cursor.execute(
            """
                CREATE TABLE public.diagnostics_dpe_new
                    (LIKE public.diagnostics_dpe INCLUDING ALL)
                """
        )
        _insert_document(
            cursor,
            table="diagnostics_dpe_new",
            document_id=DOCUMENT_ID,
            dpe_number="DPE-SYNTHETIQUE-NOUVELLE-GENERATION",
            energy_class="A",
        )
        _set_facts(cursor, table="diagnostics_dpe_new", replacement="replaced")


def _initialize_facts(pg_dsn: str) -> None:
    with _connect(pg_dsn) as connection, connection.cursor() as cursor:
        _set_facts(cursor, table="diagnostics_dpe")


def _rename_swap(
    pg_dsn: str,
    waiter_pid: queue.Queue[int],
    rename_attempted: threading.Event,
) -> None:
    """Atomically replace both parents, blocking first on the address lock."""
    with _connect(pg_dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '10s'")
        cursor.execute("SET statement_timeout = '12s'")
        cursor.execute("SELECT pg_backend_pid()")
        row = cursor.fetchone()
        assert row is not None
        waiter_pid.put(int(row[0]))
        rename_attempted.set()
        cursor.execute(
            """
                ALTER TABLE public.adresses
                RENAME TO adresses_retired
                """
        )
        cursor.execute(
            """
                ALTER TABLE public.adresses_new
                RENAME TO adresses
                """
        )
        cursor.execute(
            """
                ALTER TABLE public.diagnostics_dpe
                RENAME TO diagnostics_dpe_retired
                """
        )
        cursor.execute(
            """
                ALTER TABLE public.diagnostics_dpe_new
                RENAME TO diagnostics_dpe
                """
        )


def _relation_locks(
    pg_dsn: str,
    backend_pid: int,
) -> list[tuple[str, str, bool]]:
    with (
        _connect(pg_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT relation_class.relname, locks.mode, locks.granted
            FROM pg_locks AS locks
            JOIN pg_class AS relation_class
              ON relation_class.oid = locks.relation
            WHERE locks.pid = %s
              AND locks.locktype = 'relation'
              AND locks.relation IN (
                  'public.adresses'::regclass,
                  'public.diagnostics_dpe'::regclass
              )
            ORDER BY relation_class.relname, locks.mode
            """,
            (backend_pid,),
        )
        return [
            (str(relation), str(mode), bool(granted))
            for relation, mode, granted in cursor.fetchall()
        ]


def _wait_for_rename_blocker(
    pg_dsn: str,
    *,
    waiter_pid: int,
    expected_blocker_pid: int,
    relation: str,
    timeout: float = 5.0,
) -> tuple[list[int], bool]:
    """Poll PostgreSQL's lock graph to a deadline, without timing sleeps."""
    if relation not in {"adresses", "diagnostics_dpe"}:
        raise AssertionError("unexpected synthetic relation")
    deadline = time.monotonic() + timeout
    with (
        _connect(pg_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        while time.monotonic() < deadline:
            cursor.execute(
                """
                SELECT pg_blocking_pids(%s),
                       EXISTS (
                           SELECT 1
                           FROM pg_locks
                           WHERE pid = %s
                             AND mode = 'AccessExclusiveLock'
                             AND NOT granted
                             AND relation = to_regclass(%s)
                       )
                """,
                (waiter_pid, waiter_pid, f"public.{relation}"),
            )
            row = cursor.fetchone()
            assert row is not None
            blockers = [int(pid) for pid in row[0]]
            waiting_for_access_exclusive = bool(row[1])
            if expected_blocker_pid in blockers and waiting_for_access_exclusive:
                return blockers, waiting_for_access_exclusive
    raise AssertionError("RENAME never appeared blocked by the inventory reader")


def _assert_inventory_relations_exclusively_lockable(pg_dsn: str) -> None:
    """Fail immediately if the completed inventory still holds either parent."""
    with _connect(pg_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            LOCK TABLE ONLY public.adresses
            IN ACCESS EXCLUSIVE MODE NOWAIT
            """
        )
        cursor.execute(
            """
            LOCK TABLE ONLY public.diagnostics_dpe
            IN ACCESS EXCLUSIVE MODE NOWAIT
            """
        )


def _replace_document_ban(pg_dsn: str, ban_id: str) -> None:
    with _connect(pg_dsn) as connection:
        connection.execute(
            """
            UPDATE public.diagnostics_dpe
            SET identifiant_ban = %s
            WHERE id = %s
            """,
            (ban_id, DOCUMENT_ID),
        )


def _replace_document_number(pg_dsn: str, dpe_number: str) -> None:
    with _connect(pg_dsn) as connection:
        connection.execute(
            "UPDATE public.diagnostics_dpe SET numero_dpe = %s WHERE id = %s",
            (dpe_number, DOCUMENT_ID),
        )


def _statement_index(statements: Sequence[str], fragment: str) -> int:
    normalized_fragment = " ".join(fragment.upper().split())
    return next(
        index
        for index, statement in enumerate(statements)
        if normalized_fragment in statement
    )


async def test_context_uses_parent_oid_not_address_partition_tableoid(
    inventory_db: InventoryDatabase,
) -> None:
    page = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    assert page.context_ref is not None
    context = verify_context(page.context_ref)

    parent_oid, partition_tableoid, diagnostics_oid = await asyncio.to_thread(
        _relation_generations,
        inventory_db.pg_dsn,
    )

    assert parent_oid != partition_tableoid
    assert context.addresses_generation == parent_oid
    assert context.diagnostics_generation == diagnostics_oid
    assert page.items[0].lifecycle is None


async def test_tombstone_stays_in_inventory_unknown_and_out_of_attribution(
    inventory_db,
):
    def contaminate():
        with _connect(inventory_db.pg_dsn) as conn, conn.cursor() as cursor:
            _set_facts(
                cursor,
                table="diagnostics_dpe",
                state="absent_from_complete_scope",
                replacement="replaced",
            )

    await asyncio.to_thread(contaminate)
    result = await load_dpe_inventory_result(inventory_db.factory, ban_id=BAN_ID)
    page = result.page
    assert page.total == 1 and page.distribution.unknown == 1
    assert page.distribution.d == 0 and result.apartment_document_count == 0
    assert page.items[0].etiquette_dpe is None
    assert page.items[0].lifecycle.detail_availability == "source_reconciled"
    detail = await load_dpe_document(
        inventory_db.factory,
        context_ref=page.context_ref,
        document_ref=page.items[0].document_ref,
    )
    assert (
        detail.type_batiment
        is detail.surface_habitable
        is detail.conso_5_usages_ep
        is None
    )
    assert detail.date_etablissement_dpe is detail.date_fin_validite_dpe is None
    assert detail.validity_by_date == "unknown"
    async with inventory_db.factory() as session:
        assert await queries.dpe_par_identifiant_ban(session, BAN_ID) == ([], False)
        assert await queries.dpe_par_identifiants_ban(session, [BAN_ID]) == ([], False)


async def test_failed_swap_rollback_preserves_data_facts_and_references(inventory_db):
    await asyncio.to_thread(_initialize_facts, inventory_db.pg_dsn)
    before = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    await asyncio.to_thread(_prepare_swap, inventory_db.pg_dsn)

    def rollback():
        with _connect(inventory_db.pg_dsn) as conn:
            try:
                conn.execute(
                    "ALTER TABLE public.diagnostics_dpe "
                    "RENAME TO diagnostics_dpe_retired"
                )
                conn.execute(
                    "ALTER TABLE public.diagnostics_dpe_new RENAME TO diagnostics_dpe"
                )
                raise RuntimeError("synthetic publication failure")
            except RuntimeError:
                conn.rollback()

    await asyncio.to_thread(rollback)
    after = await load_dpe_inventory(
        inventory_db.factory, context_ref=before.context_ref
    )
    detail = await load_dpe_document(
        inventory_db.factory,
        context_ref=before.context_ref,
        document_ref=before.items[0].document_ref,
    )
    # Signed tokens may have a newer issuance second; their authenticated target
    # and every public fact must remain identical after the rolled-back swap.
    assert [item.model_dump(exclude={"document_ref"}) for item in after.items] == [
        item.model_dump(exclude={"document_ref"}) for item in before.items
    ]
    context = verify_context(before.context_ref)
    assert [
        references.verify_document(item.document_ref, context=context)
        for item in after.items
    ] == [
        references.verify_document(item.document_ref, context=context)
        for item in before.items
    ]
    assert after.distribution == before.distribution
    assert detail.etiquette_dpe == "D"
    assert detail.lifecycle == before.items[0].lifecycle
    assert verify_context(after.context_ref) == verify_context(before.context_ref)


async def test_pre_migration_v1_page_document_and_cursor_require_refresh(inventory_db):
    page = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    context = verify_context(page.context_ref)
    cursor = references.issue_cursor(
        context, anchor_id=DOCUMENT_ID, dpe_number=DPE_NUMBER
    )

    def legacy(token, serializer):
        payload = serializer.loads(token)
        payload.pop("ad")
        payload["v"] = 1
        return serializer.dumps(payload)

    old_context = legacy(page.context_ref, references._CONTEXT_SERIALIZER)
    old_document = legacy(page.items[0].document_ref, references._DOCUMENT_SERIALIZER)
    old_cursor = legacy(cursor, references._CURSOR_SERIALIZER)
    with pytest.raises(RefreshRequiredError):
        await load_dpe_inventory(inventory_db.factory, context_ref=old_context)
    with pytest.raises(RefreshRequiredError):
        await load_dpe_inventory(
            inventory_db.factory, context_ref=page.context_ref, cursor=old_cursor
        )
    with pytest.raises(RefreshRequiredError):
        await load_dpe_document(
            inventory_db.factory,
            context_ref=page.context_ref,
            document_ref=old_document,
        )


async def test_repeatable_read_keeps_page_and_aggregate_on_one_snapshot(
    inventory_db: InventoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await asyncio.to_thread(_initialize_facts, inventory_db.pg_dsn)
    page_read = asyncio.Event()
    release_aggregate = asyncio.Event()
    reader_pids: list[int] = []
    original_page = queries.dpe_inventory_page

    async def gated_page(
        session: AsyncSession,
        **kwargs: Any,
    ) -> tuple[list[queries.DpeInventoryRecord], bool]:
        result = await original_page(session, **kwargs)
        pid_result = await session.execute(text("SELECT pg_backend_pid()"))
        reader_pids.append(int(pid_result.scalar_one()))
        page_read.set()
        await asyncio.wait_for(release_aggregate.wait(), timeout=10)
        return result

    monkeypatch.setattr(queries, "dpe_inventory_page", gated_page)
    read_task = asyncio.create_task(
        load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    )
    try:
        await asyncio.wait_for(page_read.wait(), timeout=10)
        await asyncio.to_thread(
            _mutate_between_page_and_aggregate,
            inventory_db.pg_dsn,
        )
    except BaseException:
        release_aggregate.set()
        await asyncio.wait_for(
            asyncio.gather(read_task, return_exceptions=True),
            timeout=12,
        )
        raise
    finally:
        release_aggregate.set()

    page = await asyncio.wait_for(read_task, timeout=10)
    assert reader_pids
    assert page.total == 1
    assert len(page.items) == 1
    assert page.items[0].etiquette_dpe == "D"
    assert page.distribution.d == 1
    assert page.distribution.a == 0
    assert page.distribution.f == 0
    assert page.items[0].lifecycle.replacement_state == "no_successor_known"
    assert page.items[0].lifecycle.source_snapshot_date == date(2026, 9, 6)

    committed_total, committed_classes = await asyncio.to_thread(
        _observed_inventory,
        inventory_db.pg_dsn,
    )
    assert committed_total == 2
    assert committed_classes == ["F", "A"]
    fresh = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    assert fresh.items[0].lifecycle.replacement_state == "replaced"


async def test_rename_is_blocked_by_parent_locks_and_invalidates_old_context(
    inventory_db: InventoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before/during/after RENAME yields old coherence, then explicit refresh."""
    await asyncio.to_thread(_initialize_facts, inventory_db.pg_dsn)
    before_swap = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    assert before_swap.context_ref is not None
    assert before_swap.items[0].etiquette_dpe == "D"
    assert before_swap.items[0].lifecycle.replacement_state == "no_successor_known"
    old_context = verify_context(before_swap.context_ref)
    await asyncio.to_thread(_prepare_swap, inventory_db.pg_dsn)

    locks_acquired = asyncio.Event()
    release_reader = asyncio.Event()
    reader_pids: list[int] = []
    statements: list[str] = []
    original_target = queries.dpe_inventory_target_by_id

    def record_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.upper().split()))

    async def gated_target(
        session: AsyncSession,
        **kwargs: Any,
    ) -> queries.DpeInventoryTarget | None:
        pid_result = await session.execute(text("SELECT pg_backend_pid()"))
        reader_pids.append(int(pid_result.scalar_one()))
        locks_acquired.set()
        await asyncio.wait_for(release_reader.wait(), timeout=10)
        return await original_target(session, **kwargs)

    event.listen(
        inventory_db.engine.sync_engine, "before_cursor_execute", record_statement
    )
    monkeypatch.setattr(queries, "dpe_inventory_target_by_id", gated_target)

    reader_task = asyncio.create_task(
        load_dpe_inventory(
            inventory_db.factory,
            context_ref=before_swap.context_ref,
        )
    )
    rename_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(locks_acquired.wait(), timeout=10)
        assert reader_pids
        reader_pid = reader_pids[0]

        held_locks = await asyncio.to_thread(
            _relation_locks,
            inventory_db.pg_dsn,
            reader_pid,
        )
        assert ("adresses", "AccessShareLock", True) in held_locks
        assert ("diagnostics_dpe", "AccessShareLock", True) in held_locks

        waiter_pid: queue.Queue[int] = queue.Queue(maxsize=1)
        rename_attempted = threading.Event()
        rename_task = asyncio.create_task(
            asyncio.to_thread(
                _rename_swap,
                inventory_db.pg_dsn,
                waiter_pid,
                rename_attempted,
            )
        )
        attempted = await asyncio.to_thread(rename_attempted.wait, 5)
        assert attempted, "RENAME worker did not reach its DDL barrier"
        ddl_pid = await asyncio.to_thread(waiter_pid.get, True, 5)

        blockers, waiting = await asyncio.to_thread(
            _wait_for_rename_blocker,
            inventory_db.pg_dsn,
            waiter_pid=ddl_pid,
            expected_blocker_pid=reader_pid,
            relation="adresses",
        )
        assert waiting is True
        assert reader_pid in blockers
    finally:
        release_reader.set()
        tasks = [reader_task, *([rename_task] if rename_task is not None else [])]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=15,
            )
        except TimeoutError as exc:
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks, timeout=1)
            raise AssertionError("concurrent database tasks did not settle") from exc
        event.remove(
            inventory_db.engine.sync_engine,
            "before_cursor_execute",
            record_statement,
        )

    during_swap = reader_task.result()
    assert during_swap.total == 1
    assert during_swap.items[0].etiquette_dpe == "D"
    assert during_swap.items[0].lifecycle == before_swap.items[0].lifecycle
    assert verify_context(during_swap.context_ref or "") == old_context
    assert rename_task is not None
    rename_task.result()

    set_index = _statement_index(
        statements,
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    )
    address_lock_index = _statement_index(
        statements,
        "LOCK TABLE ONLY public.adresses IN ACCESS SHARE MODE",
    )
    diagnostics_lock_index = _statement_index(
        statements,
        "LOCK TABLE ONLY public.diagnostics_dpe IN ACCESS SHARE MODE",
    )
    generation_index = _statement_index(
        statements,
        "SELECT to_regclass('public.adresses')::oid::bigint",
    )
    assert set_index < address_lock_index < diagnostics_lock_index < generation_index

    with pytest.raises(RefreshRequiredError):
        await load_dpe_inventory(
            inventory_db.factory,
            context_ref=before_swap.context_ref,
        )
    with pytest.raises(RefreshRequiredError):
        await load_dpe_document(
            inventory_db.factory,
            context_ref=before_swap.context_ref,
            document_ref=before_swap.items[0].document_ref,
        )

    after_swap = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    assert after_swap.total == 1
    assert after_swap.items[0].etiquette_dpe == "A"
    assert after_swap.items[0].lifecycle.replacement_state == "replaced"
    detail = await load_dpe_document(
        inventory_db.factory,
        context_ref=after_swap.context_ref,
        document_ref=after_swap.items[0].document_ref,
    )
    assert detail.lifecycle == after_swap.items[0].lifecycle
    assert after_swap.context_ref is not None
    after_context = verify_context(after_swap.context_ref)
    assert after_context.addresses_generation != old_context.addresses_generation
    assert after_context.diagnostics_generation != old_context.diagnostics_generation


async def test_reused_document_id_on_another_ban_is_never_substituted(
    inventory_db: InventoryDatabase,
) -> None:
    original = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    assert original.context_ref is not None
    document_ref = original.items[0].document_ref

    await asyncio.to_thread(
        _replace_document_ban,
        inventory_db.pg_dsn,
        REUSED_BAN_ID,
    )
    # The selected address and its opaque context remain current. Only the DPE
    # row moved to another BAN key while retaining the same technical id.
    still_current = await load_dpe_inventory(
        inventory_db.factory,
        context_ref=original.context_ref,
    )
    assert still_current.context_ref == original.context_ref
    assert still_current.state == "absent"
    assert still_current.total == 0
    with pytest.raises(RefreshRequiredError):
        await load_dpe_document(
            inventory_db.factory,
            context_ref=original.context_ref,
            document_ref=document_ref,
        )

    await asyncio.to_thread(
        _replace_document_ban,
        inventory_db.pg_dsn,
        BAN_ID,
    )
    fresh = await load_dpe_inventory(inventory_db.factory, ban_id=BAN_ID)
    assert fresh.context_ref is not None
    document_ref = fresh.items[0].document_ref

    await asyncio.to_thread(
        _replace_document_number,
        inventory_db.pg_dsn,
        REUSED_DPE_NUMBER,
    )
    with pytest.raises(RefreshRequiredError):
        await load_dpe_document(
            inventory_db.factory,
            context_ref=fresh.context_ref,
            document_ref=document_ref,
        )


async def test_inventory_locks_are_released_before_a_slow_proxy(
    inventory_db: InventoryDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory_connection_checked_in = asyncio.Event()
    proxy_probe_finished = asyncio.Event()
    proxy_started = asyncio.Event()
    release_proxy = asyncio.Event()
    lock_probe_errors: list[BaseException] = []
    original_lock = queries.lock_dpe_inventory_relations

    async def observed_lock(
        session: AsyncSession,
    ) -> queries.DpeRelationGenerations:
        generations = await original_lock(session)
        connection = await session.connection()
        connection.sync_connection.info["dpe_inventory_probe"] = True
        return generations

    def record_inventory_checkin(
        _dbapi_connection: Any,
        connection_record: Any,
    ) -> None:
        if connection_record.info.pop("dpe_inventory_probe", False):
            inventory_connection_checked_in.set()

    async def slow_proxy(
        name: str,
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[str, dict[str, bool], list[Any]]:
        assert name == "risques"
        try:
            assert inventory_connection_checked_in.is_set(), (
                "proxy task started before the inventory connection was checked in"
            )
            await asyncio.to_thread(
                _assert_inventory_relations_exclusively_lockable,
                inventory_db.pg_dsn,
            )
        except BaseException as exc:
            lock_probe_errors.append(exc)
            raise
        finally:
            proxy_probe_finished.set()
        proxy_started.set()
        await asyncio.wait_for(release_proxy.wait(), timeout=10)
        return name, {"synthetic": True}, []

    event.listen(inventory_db.engine.sync_engine, "checkin", record_inventory_checkin)
    monkeypatch.setattr(queries, "lock_dpe_inventory_relations", observed_lock)
    monkeypatch.setattr(resolve_multi, "_query_one_dataset", slow_proxy)

    request_task = asyncio.create_task(
        resolve_multi.query_multiple_datasets(
            inventory_db.factory,
            ["dpe", "risques"],
            47.2,
            -1.5,
            canonical_parcelle=None,
            canonical_ban_id=BAN_ID,
        )
    )
    try:
        await asyncio.wait_for(proxy_probe_finished.wait(), timeout=10)
        assert lock_probe_errors == []
        await asyncio.wait_for(proxy_started.wait(), timeout=10)
    except BaseException:
        release_proxy.set()
        await asyncio.wait_for(
            asyncio.gather(request_task, return_exceptions=True),
            timeout=12,
        )
        raise
    finally:
        release_proxy.set()
        event.remove(
            inventory_db.engine.sync_engine,
            "checkin",
            record_inventory_checkin,
        )

    result = await asyncio.wait_for(request_task, timeout=10)
    assert result["dpe_inventory"].state == "ready"
    assert result["dpe_inventory"].total == 1
    assert result["donnees"]["risques"] == {"synthetic": True}
