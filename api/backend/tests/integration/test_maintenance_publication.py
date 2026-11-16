"""Maintenance admission and rollback on a disposable PostGIS database."""

import asyncio
import logging
import threading
import time
from types import SimpleNamespace

import psycopg
import pytest

from app.commune.dependencies import PUBLICATION_LOCK_ID
from app.etl.publication import Publication
from app.etl.runner import PipelineRunner
from app.scheduler import jobs
from tests.integration.test_cor20_publication import (
    PublicationPipeline,
    create_chain,
    read,
)
from tests.integration.test_cor20_publication import published as _published_fixture

published = _published_fixture


@pytest.fixture()
async def maintenance(published, monkeypatch):
    dsn, client = published
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute("ALTER TABLE matview_metadata ADD last_refresh_at timestamptz")
        conn.execute("ALTER TABLE matview_metadata ADD last_error text")
        conn.execute(
            "ALTER TABLE matview_metadata ADD last_refresh_duration_seconds float"
        )
        conn.execute(
            "INSERT INTO matview_metadata VALUES"
            "('v_commune_synthese','success',NOW(),NULL,1),"
            "('communes_lookup','success',NOW(),NULL,1)"
        )
        conn.execute("CREATE MATERIALIZED VIEW communes_lookup AS SELECT 1 AS id")
        conn.execute(
            "CREATE TABLE etl_old_tables(id serial,table_name text,"
            "created_at timestamptz DEFAULT '2000-01-01')"
        )
        conn.execute("CREATE TABLE retired_old(id int)")
        conn.execute("INSERT INTO etl_old_tables(table_name) VALUES('retired_old')")
    monkeypatch.setattr(
        jobs, "settings", SimpleNamespace(database_url=dsn, etl_old_table_ttl_hours=1)
    )
    monkeypatch.setattr(jobs, "recreate_commune_synthese", create_chain)
    monkeypatch.setattr(
        jobs,
        "recreate_communes_lookup",
        lambda conn: conn.execute(
            "DROP MATERIALIZED VIEW communes_lookup; "
            "CREATE MATERIALIZED VIEW communes_lookup AS SELECT 2 AS id"
        ),
    )
    monkeypatch.setattr(jobs, "recreate_missing_matviews", lambda conn, log: None)
    return dsn, client


OPERATIONS = [
    jobs.refresh_commune_synthese_view,
    jobs.refresh_communes_lookup,
    jobs.cleanup_old_tables,
    lambda: PipelineRunner._inline_cleanup_old_tables(
        jobs.settings.database_url, logging.getLogger(__name__)
    ),
]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("holder", ["writer", "reader"])
async def test_admission_refusal_does_not_touch_ddl_or_metadata(
    maintenance, operation, holder
):
    dsn, _client = maintenance
    with psycopg.connect(dsn, autocommit=True) as owner:
        suffix = "_shared" if holder == "reader" else ""
        owner.execute(f"SELECT pg_advisory_lock{suffix}(%s)", (PUBLICATION_LOCK_ID,))
        owner.execute(
            "INSERT INTO etl_publications(generation,dataset_name,table_name,status)"
            " VALUES(gen_random_uuid(),'revenus','revenus','pending')"
        )
        before = owner.execute(
            "SELECT matview_name,last_refresh_status,last_refresh_at,last_error "
            "FROM matview_metadata ORDER BY matview_name"
        ).fetchall()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            await asyncio.wait_for(asyncio.to_thread(operation), 2)
        assert (
            owner.execute(
                "SELECT matview_name,last_refresh_status,last_refresh_at,last_error "
                "FROM matview_metadata ORDER BY matview_name"
            ).fetchall()
            == before
        )
        assert owner.execute("SELECT status FROM etl_publications").fetchall() == [
            ("pending",)
        ]
        assert owner.execute("SELECT to_regclass('retired_old')").fetchone()[0]


async def test_maintenance_excludes_http_reader_and_publication(
    maintenance, monkeypatch
):
    dsn, client = maintenance
    entered, release = threading.Event(), threading.Event()

    def rebuild(conn):
        create_chain(conn)
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(jobs, "recreate_commune_synthese", rebuild)
    task = asyncio.create_task(asyncio.to_thread(jobs.refresh_commune_synthese_view))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await read(client)).status_code == 503
        with (
            pytest.raises(psycopg.errors.LockNotAvailable),
            Publication(dsn, PublicationPipeline(dsn)),
        ):
            pytest.fail("Publication entered during maintenance")
    finally:
        release.set()
        await task
    assert (await read(client)).status_code == 200


async def test_ddl_rollback_records_failure_without_rehabilitating_journal(
    maintenance, monkeypatch
):
    dsn, client = maintenance
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO etl_publications(generation,dataset_name,table_name,status)"
            " VALUES(gen_random_uuid(),'revenus','revenus','failed')"
        )
        before = conn.execute("SELECT 'v_commune_synthese'::regclass::oid").fetchone()

    def broken(conn):
        conn.execute("DROP MATERIALIZED VIEW v_commune_synthese")
        conn.execute("SELECT 1/0")

    monkeypatch.setattr(jobs, "recreate_commune_synthese", broken)
    with pytest.raises(psycopg.errors.DivisionByZero):
        jobs.refresh_commune_synthese_view()
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute("SELECT 'v_commune_synthese'::regclass::oid").fetchone()
            == before
        )
        assert conn.execute(
            "SELECT last_refresh_status FROM matview_metadata "
            "WHERE matview_name='v_commune_synthese'"
        ).fetchone() == ("failed",)
        assert conn.execute("SELECT status FROM etl_publications").fetchall() == [
            ("failed",)
        ]
    assert (await read(client)).status_code == 503


async def test_rebuild_changes_view_oid_not_journalled_source_oids(maintenance):
    dsn, client = maintenance
    with Publication(dsn, PublicationPipeline(dsn)) as publication:
        publication.observe_stock()
        publication.publish(lambda conn: None)
    with psycopg.connect(dsn) as conn:
        journal = conn.execute(
            "SELECT relation_oids,status FROM etl_publications"
        ).fetchall()
        old_oid = conn.execute("SELECT 'v_commune_synthese'::regclass::oid").fetchone()
    jobs.refresh_commune_synthese_view()
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute("SELECT relation_oids,status FROM etl_publications").fetchall()
            == journal
        )
        assert journal[0][0].keys() == {"revenus"}
        assert (
            conn.execute("SELECT 'v_commune_synthese'::regclass::oid").fetchone()
            != old_oid
        )
    assert (await read(client)).status_code == 200


@pytest.mark.parametrize("failure", [False, True])
async def test_exclusion_survives_until_metadata_commit(
    maintenance, monkeypatch, failure
):
    dsn, client = maintenance
    barrier = 87654321
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "CREATE FUNCTION pause_metadata() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN PERFORM pg_advisory_xact_lock(87654321); RETURN NEW; END $$"
        )
        conn.execute(
            "CREATE TRIGGER pause_metadata BEFORE UPDATE ON matview_metadata "
            "FOR EACH ROW EXECUTE FUNCTION pause_metadata()"
        )
    if failure:
        monkeypatch.setattr(
            jobs, "recreate_commune_synthese", lambda conn: conn.execute("SELECT 1/0")
        )
    owner = psycopg.connect(dsn, autocommit=True)
    owner.execute("SELECT pg_advisory_lock(%s)", (barrier,))
    task = asyncio.create_task(asyncio.to_thread(jobs.refresh_commune_synthese_view))
    try:
        until = time.monotonic() + 2
        waiting = False
        while time.monotonic() < until:
            waiting = owner.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                "AND objid=%s AND NOT granted)",
                (barrier,),
            ).fetchone()[0]
            if waiting:
                break
            await asyncio.sleep(0.005)
        assert waiting, "metadata transaction must reach the controlled trigger"
        assert (await read(client)).status_code == 503
        with (
            pytest.raises(psycopg.errors.LockNotAvailable),
            Publication(dsn, PublicationPipeline(dsn)),
        ):
            pytest.fail("Publication admitted before maintenance metadata commit")
    finally:
        owner.close()
        if failure:
            with pytest.raises(psycopg.errors.DivisionByZero):
                await task
        else:
            await task
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM etl_publications").fetchone() == (0,)


@pytest.mark.parametrize("status", ["pending", "failed"])
async def test_successful_repair_never_promotes_incomplete_publication(
    maintenance, status
):
    dsn, client = maintenance
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO etl_publications(generation,dataset_name,table_name,status)"
            " VALUES(gen_random_uuid(),'revenus','revenus',%s)",
            (status,),
        )
    jobs.refresh_commune_synthese_view()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT status FROM etl_publications").fetchall() == [
            (status,)
        ]
    assert (await read(client)).status_code == 503


@pytest.mark.parametrize("inline", [False, True])
async def test_cleanup_repair_failure_rolls_back_registry_and_drop(
    maintenance, monkeypatch, inline
):
    dsn, _client = maintenance

    def broken(conn, log):
        conn.execute("SELECT 1/0")

    module = "app.etl.runner" if inline else "app.scheduler.jobs"
    monkeypatch.setattr(f"{module}.recreate_missing_matviews", broken)
    operation = OPERATIONS[3] if inline else jobs.cleanup_old_tables
    with pytest.raises(psycopg.errors.DivisionByZero):
        operation()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT to_regclass('retired_old')").fetchone()[0]
        assert conn.execute("SELECT count(*) FROM etl_old_tables").fetchone() == (1,)


async def test_dvf_source_oid_includes_grain_not_views(maintenance):
    dsn, _client = maintenance
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE TABLE mutations(id int)")
        conn.execute("INSERT INTO mutations VALUES(1)")
    pipeline = SimpleNamespace(dataset_name="dvf", production_table_name="mutations")
    with Publication(dsn, pipeline) as publication:
        publication.observe_stock()
        publication.publish(lambda conn: None)
    with psycopg.connect(dsn) as conn:
        before = conn.execute("SELECT relation_oids FROM etl_publications").fetchone()[
            0
        ]
    jobs.refresh_commune_synthese_view()
    with psycopg.connect(dsn) as conn:
        assert set(before) == {"mutations", "mutations_grain"}
        assert (
            conn.execute("SELECT relation_oids FROM etl_publications").fetchone()[0]
            == before
        )
        for name, oid in before.items():
            assert (
                conn.execute("SELECT to_regclass(%s)::oid", (name,)).fetchone()[0]
                == oid
            )


async def test_failure_metadata_rejection_keeps_original_error_and_releases_lock(
    maintenance, monkeypatch
):
    dsn, _client = maintenance
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "CREATE FUNCTION reject_metadata() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'synthetic metadata rejection'; END $$"
        )
        conn.execute(
            "CREATE TRIGGER reject_metadata BEFORE UPDATE ON matview_metadata "
            "FOR EACH ROW EXECUTE FUNCTION reject_metadata()"
        )
        before = conn.execute(
            "SELECT * FROM matview_metadata ORDER BY matview_name"
        ).fetchall()
    monkeypatch.setattr(
        jobs, "recreate_commune_synthese", lambda conn: conn.execute("SELECT 1/0")
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        jobs.refresh_commune_synthese_view()
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute(
                "SELECT * FROM matview_metadata ORDER BY matview_name"
            ).fetchall()
            == before
        )
        assert conn.execute("SELECT count(*) FROM etl_publications").fetchone() == (0,)
        assert conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (PUBLICATION_LOCK_ID,)
        ).fetchone()[0]
