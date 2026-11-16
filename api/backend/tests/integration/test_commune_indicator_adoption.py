import asyncio
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commune import indicator_adoption as adoption
from app.commune.dependencies import COMMUNE_SOURCES
from app.commune.indicator_adoption import raw_multiset_query, source_columns
from app.commune.queries import commune_synthese_par_codes
from app.etl import publication_validation
from app.etl.publication import Publication, requires_fresh_start
from app.etl_dvf import reconstruction as dvf_reconstruction
from app.etl_dvf.reconstruction import reconstruit_mutations_grain
from tests.integration.publication_fixture import install_publication_journal

pytestmark = pytest.mark.destructive
# Received departements of the adopted synthetic lot (the existing adoption).
LOT = ("44", "85")


@pytest.fixture
def adoption_target(isolated_pg_dsn):
    with psycopg.connect(isolated_pg_dsn, autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS datacommune_preprod WITH (FORCE)")
        admin.execute("CREATE DATABASE datacommune_preprod")
    target = isolated_pg_dsn.rsplit("/", 1)[0] + "/datacommune_preprod"
    try:
        yield target
    finally:
        with psycopg.connect(isolated_pg_dsn, autocommit=True) as admin:
            admin.execute("DROP DATABASE datacommune_preprod WITH (FORCE)")


def test_raw_digest_detects_non_projection_change_with_same_oid_and_count(
    isolated_pg_dsn,
):
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute(
            "CREATE TABLE mutations(id_mutation text, projected integer, "
            "legacy_note text, observed_at date, geom geometry(Point,4326))"
        )
        conn.execute(
            "INSERT INTO mutations VALUES "
            "('m1',1,'before','2025-01-02',ST_SetSRID(ST_Point(1,2),4326)),"
            "('m1',1,NULL,'2025-01-02',NULL)"
        )
        columns = source_columns(conn)
        oid_before = conn.execute("SELECT 'mutations'::regclass::oid").fetchone()[0]
        count_before, digest_before = conn.execute(
            raw_multiset_query(columns)
        ).fetchone()

        conn.execute(
            "UPDATE mutations SET legacy_note='after' WHERE legacy_note='before'"
        )
        oid_after = conn.execute("SELECT 'mutations'::regclass::oid").fetchone()[0]
        count_after, digest_after = conn.execute(raw_multiset_query(columns)).fetchone()

    assert (oid_before, count_before) == (oid_after, count_after)
    assert digest_before != digest_after


def test_global_pre_pending_refusal_leaves_no_journal(isolated_pg_dsn):
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        install_publication_journal(conn)
    pipeline = SimpleNamespace(dataset_name="dvf", production_table_name="mutations")

    def refuse(_owner):
        raise RuntimeError("precondition_changed_under_global_lock")

    with (
        pytest.raises(RuntimeError, match="precondition_changed_under_global_lock"),
        Publication(isolated_pg_dsn, pipeline, refuse),
    ):
        pytest.fail("publication body must not start")
    with psycopg.connect(isolated_pg_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM etl_publications").fetchone() == (0,)


def _install_dvf_schema(conn):
    conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    conn.execute("CREATE EXTENSION postgis")
    # code_nature_culture (migration 043): read by the reconstruction since
    # D-02 variant V (phase 04.4).
    conn.execute(
        """CREATE TABLE mutations(
        id bigserial PRIMARY KEY, code_departement varchar(3) NOT NULL,
        id_mutation text NOT NULL, date_mutation date NOT NULL,
        nature_mutation varchar(50), valeur_fonciere numeric(15,2),
        code_type_local varchar(2), code_commune varchar(5), id_parcelle varchar(14),
        code_nature_culture varchar(5),
        surface_reelle_bati integer, surface_terrain integer,
        nombre_pieces_principales smallint, nom_commune text,
        adresse_numero varchar(10), adresse_nom_voie text, code_postal varchar(5),
        position geometry(Point,4326))"""
    )
    conn.execute(
        """INSERT INTO mutations
        (code_departement,id_mutation,date_mutation,nature_mutation,valeur_fonciere,
         code_type_local,code_commune,id_parcelle,surface_reelle_bati,
         surface_terrain,nombre_pieces_principales,nom_commune,adresse_numero,
         adresse_nom_voie,code_postal,position)
        VALUES ('44','SYN-M1','2025-01-02','Vente',100000,'1','44001',
        '44001000AA0001',50,100,3,'Synthétique','1','Rue test','44000',
        ST_SetSRID(ST_Point(-1,47),4326))"""
    )
    path = Path(__file__).parents[2] / "migrations/versions/087_dvf_mutations_grain.py"
    spec = importlib.util.spec_from_file_location("adoption_087", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = SimpleNamespace(execute=conn.execute)
    migration.upgrade()
    # 105 (plan 04.1-14): the reconstruction reads and writes adresse_suffixe.
    path = path.with_name("105_dvf_adresse_suffixe.py")
    spec = importlib.util.spec_from_file_location("adoption_105", path)
    suffixe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suffixe)
    suffixe.op = SimpleNamespace(execute=conn.execute)
    suffixe.upgrade()
    # 108 (plan 04.4-07): the amount of the grain has the type of `mutations`.
    conn.execute(
        "ALTER TABLE mutations_grain ALTER COLUMN valeur_fonciere TYPE NUMERIC(15, 2)"
    )
    install_publication_journal(conn)
    conn.execute(
        """CREATE TABLE validation_runs(
        id bigserial PRIMARY KEY,dataset_name text,row_count_before bigint,
        row_count_after bigint,row_drop_pct float,null_rate_findings jsonb,
        passed boolean,blocked_swap boolean,notes text)"""
    )
    conn.execute(
        """CREATE TABLE dataset_metadata(
        dataset_name text PRIMARY KEY,status text,last_updated_at timestamptz,
        row_count bigint,last_import_duration_seconds float,
        source_period_label text,source_updated_at timestamptz,
        effective_departments text[])"""
    )
    conn.execute(
        """CREATE TABLE matview_metadata(
        matview_name text PRIMARY KEY,last_refresh_at timestamptz,
        last_refresh_duration_seconds float,last_refresh_status text,last_error text)"""
    )
    conn.execute("CREATE TABLE alembic_version(version_num text PRIMARY KEY)")
    conn.execute(
        "INSERT INTO alembic_version VALUES('096_commune_indicator_build_activation')"
    )


def _backup(tmp_path, stock):
    path = tmp_path / "backup.json"
    path.write_text(
        json.dumps(
            {
                "backup_id": "SYN-BACKUP",
                "captured_at": "2026-09-10T00:00:00Z",
                "capture_mode": "pg_dump_consistent_snapshot",
                "restore_path": "/private/SYN-BACKUP.dump",
                "catalog_verified": True,
                "restore_tested": True,
                "environment": "preproduction",
                "vmid": 277,
                "database": "datacommune_preprod",
                "sha256": "f" * 64,
                "restore_exit_code": 0,
                "restore_target_removed": True,
                "restored_counts": {
                    "mutations": stock.source_rows,
                    "mutations_grain": stock.mutation_count,
                    "diagnostics_dpe": 540717,
                    "schema": "094_publication_generations",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _prepare_adoption(target, tmp_path, monkeypatch):
    with psycopg.connect(target) as conn:
        _install_dvf_schema(conn)
    reconstruit_mutations_grain(target, LOT)
    stock = adoption.measure_stock(target, LOT)
    with psycopg.connect(target) as conn:
        identity = conn.execute(
            "SELECT (pg_control_system()).system_identifier::text,current_database(),"
            "coalesce(inet_server_addr()::text,'local'),inet_server_port()"
        ).fetchone()

    def rebuild_view(conn):
        conn.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese")
        conn.execute(
            "CREATE MATERIALIZED VIEW v_commune_synthese AS "
            "SELECT code_commune code_insee FROM mutations_grain"
        )

    monkeypatch.setattr(
        adoption.DependentMatviewHook,
        "_DEPENDENCIES",
        {"dvf": (("v_commune_synthese", rebuild_view),)},
    )
    monkeypatch.setattr(adoption.settings, "deployment_sha", "a" * 40)
    monkeypatch.setattr(
        adoption.settings,
        "database_url",
        target.replace("postgresql://", "postgresql+asyncpg://"),
    )
    request = adoption.AdoptionRequest(
        scope="preproduction_ct277",
        expected_candidate_sha="a" * 40,
        expected_runtime_sha256=adoption.runtime_source_sha256(),
        expected_schema_revision="096_commune_indicator_build_activation",
        expected_system_identifier=identity[0],
        expected_database=identity[1],
        expected_server_address=identity[2],
        expected_server_port=identity[3],
        backup_receipt=_backup(tmp_path, stock),
        expected_backup_sha256="f" * 64,
        expected_source_rows=stock.source_rows,
        expected_mutation_count=stock.mutation_count,
        expected_all_years_projection_sha256=stock.all_years_projection_sha256,
        expected_projection_count=stock.received_projection_count,
        expected_projection_sha256=stock.received_projection_sha256,
        expected_raw_sha256=stock.raw_sha256,
        departments=LOT,
    )
    return request, stock


def test_complete_adoption_uses_real_reconstruction_hooks_and_publication(
    adoption_target, tmp_path, monkeypatch
):
    target = adoption_target
    request, stock = _prepare_adoption(target, tmp_path, monkeypatch)
    receipt = adoption.adopt_existing_dvf_stock(target, request)

    assert receipt["source_stock_preserved"] is True
    with psycopg.connect(target) as conn:
        assert conn.execute(
            "SELECT status,row_count FROM etl_publications ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("published", stock.source_rows)
        assert conn.execute("SELECT count(*) FROM validation_runs").fetchone() == (1,)
        assert conn.execute(
            "SELECT last_refresh_status FROM matview_metadata"
        ).fetchone() == ("success",)


@pytest.mark.parametrize(
    "boundary",
    [
        "after_staging_commit",
        "after_aggregate_commit",
        "after_parcels_commit",
        "after_indexes_commit",
        "validation",
        "view",
        "metadata",
        "postcommit",
    ],
)
def test_adoption_failure_boundaries_leave_failed_barrier_and_raw_intact(  # noqa: C901
    adoption_target, tmp_path, monkeypatch, boundary
):
    target = adoption_target
    request, before = _prepare_adoption(target, tmp_path, monkeypatch)
    reconstruction_boundary = {
        "after_staging_commit": "_reconstruire_mutations",
        "after_aggregate_commit": "_peupler_mutation_parcelles",
        "after_parcels_commit": "_create_staging_indexes",
        "after_indexes_commit": "_garde_50_pourcent",
    }.get(boundary)
    if reconstruction_boundary:
        original = getattr(dvf_reconstruction, reconstruction_boundary)

        def fail_boundary(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError(f"SYN-{boundary}")

        monkeypatch.setattr(dvf_reconstruction, reconstruction_boundary, fail_boundary)
    elif boundary == "validation":
        monkeypatch.setattr(
            publication_validation,
            "validate_publication",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("SYN-validation")
            ),
        )
    elif boundary == "view":

        def fail_view(_conn):
            raise RuntimeError("SYN-view")

        monkeypatch.setattr(
            adoption.DependentMatviewHook,
            "_DEPENDENCIES",
            {"dvf": (("v_commune_synthese", fail_view),)},
        )
    elif boundary == "metadata":
        monkeypatch.setattr(
            adoption,
            "update_dataset_metadata",
            lambda *_: (_ for _ in ()).throw(RuntimeError("SYN-metadata")),
        )
    else:
        real_measure = adoption.measure_stock
        calls = 0

        def fail_postcommit(dsn, departments):
            nonlocal calls
            calls += 1
            measured = real_measure(dsn, departments)
            if calls == 4:
                return replace(measured, raw_sha256="0" * 64)
            return measured

        monkeypatch.setattr(adoption, "measure_stock", fail_postcommit)

    with pytest.raises((RuntimeError, adoption.AdoptionRefused)):
        adoption.adopt_existing_dvf_stock(target, request)

    after = adoption.measure_stock(target, LOT)
    assert (after.source_rows, after.raw_sha256, after.source_oid) == (
        before.source_rows,
        before.raw_sha256,
        before.source_oid,
    )
    with psycopg.connect(target) as conn:
        assert conn.execute(
            "SELECT status FROM etl_publications ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("failed",)
    assert requires_fresh_start(target, "dvf") is True

    async def rejected_read():
        engine = create_async_engine(
            target.replace("postgresql://", "postgresql+asyncpg://")
        )
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                await commune_synthese_par_codes(
                    session, ["44001"], list(COMMUNE_SOURCES)
                )
        finally:
            await engine.dispose()

    with pytest.raises(HTTPException) as blocked:
        asyncio.run(rejected_read())
    assert blocked.value.status_code == 503

    if reconstruction_boundary:
        monkeypatch.setattr(dvf_reconstruction, reconstruction_boundary, original)
        reconstruit_mutations_grain(target, LOT)
        recovered = adoption.measure_stock(target, LOT)
        assert recovered.mutation_count == before.mutation_count
