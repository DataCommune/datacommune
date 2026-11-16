"""DATA-02 on disposable PostGIS: a DPE without source geometry is kept.

Two real observations run through the DPE pipeline (download, transform,
stage, reconciliation, swap) against the official-shaped local source API,
then the stored rows are read by the production inventory service and the
real ASGI routes. Expected behaviour (REQUIREMENTS.md DATA-02):

- a document without usable source coordinates is stored with a null
  ``position``, never a borrowed BAN geometry, and is counted present;
- with a usable BAN context (source key present and a local address with that
  key) it is consulted by reference in the V2 contract, without ADEME number;
- without a usable BAN context it is stored, counted and flagged, never listed
  in an address inventory and never reachable through another address;
- a document absent from the next observation stays stored with its state;
- spatial readers never return a document without position.

Run only through the disposable PostGIS harness::

    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dpe_without_geometry.py
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.publication_readiness import require_publication
from app.database import get_db
from app.dpe import queries
from app.dpe.inventory_service import (
    load_dpe_document,
    load_dpe_inventory,
    load_dpe_inventory_result,
)
from app.dpe.references import RefreshRequiredError, issue_document
from app.etl_dpe.pipeline import DpePipeline
from app.logements import router as logements
from app.main import app
from app.middleware import RequestLoggingMiddleware
from tests.integration.instance_scope_fixture import install_instance_scope
from tests.integration.publication_fixture import install_publication_journal
from tests.integration.test_dpe_source_staging import (
    _CREATE_RELATION,
    COLUMNS_MIGRATION,
    RAW_ADDRESS_MIGRATION,
    _cleanup,
    _migrate,
    _stage_all,
    _transport,
)


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    bind_departements(("44", "85"), DpePipeline)


@pytest.fixture(scope="module")
def pg_dsn(isolated_pg_dsn):
    """Keep this module's destructive public fixtures out of the shared schema."""
    return isolated_pg_dsn


# Synthetic identities only: no real ADEME number, BAN key or address.
WITH_CONTEXT = "fixture-dpe03existant-44-0"
NO_BAN_KEY = "fixture-dpe03existant-44-1"
KEY_WITHOUT_ADDRESS = "fixture-dpe02neuf-44-0"
DROPPED_LATER = "fixture-dpe02neuf-44-1"
LOSES_GEOMETRY = "fixture-dpe03existant-85-0"
SECOND_ONLY = "fixture-second-observation-only"
CONTEXT_BAN = "44109_0001_00003"
UNKNOWN_BAN = "44109_0002_00404"
ADDRESS_ID = 90_816_001
CONSULTED = {
    "type_batiment": "appartement",
    "surface_habitable_logement": 48.2,
    "etiquette_dpe": "E",
    "etiquette_ges": "C",
    "date_etablissement_dpe": "2024-03-11",
    "date_fin_validite_dpe": "2034-03-10",
    "conso_5_usages_ep": 231.4,
    "emission_ges_5_usages": 7.1,
}


def _without_geometry(row: dict) -> None:
    row["coordonnee_cartographique_x_ban"] = None
    row["coordonnee_cartographique_y_ban"] = None


def _first_observation(row: dict, _slug: str) -> None:
    number = row["numero_dpe"]
    if number in {WITH_CONTEXT, NO_BAN_KEY, KEY_WITHOUT_ADDRESS, DROPPED_LATER}:
        _without_geometry(row)
    if number == WITH_CONTEXT:
        row.update(CONSULTED, identifiant_ban=CONTEXT_BAN)
    if number == KEY_WITHOUT_ADDRESS:
        row["identifiant_ban"] = UNKNOWN_BAN


def _second_observation(row: dict, slug: str) -> None:
    if row["numero_dpe"] == DROPPED_LATER:
        # Absent from this complete observation; its slot carries a new document.
        row["numero_dpe"] = SECOND_ONLY
        return
    _first_observation(row, slug)
    if row["numero_dpe"] == LOSES_GEOMETRY:
        _without_geometry(row)


def _observe(connection, pg_dsn, directory: Path, change) -> dict[str, int]:
    """Run one complete observation to the swap; return the published counters."""
    with pytest.MonkeyPatch.context() as patch:
        _transport(patch, directory, change)
        pipeline = DpePipeline()
        try:
            _stage_all(pipeline, pg_dsn)
            counters = pipeline.stage_counters()
            pipeline.swap()
        finally:
            pipeline.close()
    connection.commit()
    return counters


@dataclass(frozen=True, slots=True)
class Observed:
    dsn: str
    first: dict[str, int]
    second: dict[str, int]
    rows: dict[str, dict]


def _stored_rows(connection) -> dict[str, dict]:
    rows = connection.execute(
        text("""
        SELECT numero_dpe, id, identifiant_ban, source_state, is_tombstone,
               position IS NULL AS no_position, longitude, latitude,
               etiquette_dpe
        FROM diagnostics_dpe
    """)
    ).mappings()
    result = {row["numero_dpe"]: dict(row) for row in rows}
    connection.commit()
    return result


def _publish_address_context(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("""
            DROP TABLE IF EXISTS public.adresses CASCADE;
            CREATE TABLE public.adresses (
                id bigint NOT NULL,
                code_departement text NOT NULL,
                ban_id text,
                PRIMARY KEY (id, code_departement)
            ) PARTITION BY LIST (code_departement);
            CREATE TABLE public.adresses_44
                PARTITION OF public.adresses FOR VALUES IN ('44');
        """)
        conn.execute(
            "INSERT INTO public.adresses (id, code_departement, ban_id) "
            "VALUES (%s, '44', %s)",
            (ADDRESS_ID, CONTEXT_BAN),
        )
        install_publication_journal(conn)
        install_instance_scope(conn, ("dpe", "ban"), ("44", "85"))
        conn.execute("""
            INSERT INTO etl_publications
                (generation, dataset_name, table_name, status, relation_oids)
            VALUES (gen_random_uuid(), 'dpe', 'diagnostics_dpe', 'published',
                jsonb_build_object('diagnostics_dpe', 'diagnostics_dpe'::regclass::oid))
        """)


@pytest.fixture
def observed(pg_dsn, monkeypatch, tmp_path) -> Iterator[Observed]:
    engine = create_engine(pg_dsn.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as connection:
        _cleanup(connection)
        connection.execute(text("DROP TABLE IF EXISTS etl_publications CASCADE"))
        connection.execute(text(_CREATE_RELATION))
        _migrate(connection)
        _migrate(connection, name=COLUMNS_MIGRATION)
        _migrate(connection, name=RAW_ADDRESS_MIGRATION)
        connection.execute(
            text("""
            CREATE TABLE IF NOT EXISTS etl_old_tables (
                id SERIAL PRIMARY KEY, table_name TEXT NOT NULL UNIQUE,
                dataset_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        )
        connection.commit()
        monkeypatch.setattr(DpePipeline, "get_sync_dsn", staticmethod(lambda: pg_dsn))
        monkeypatch.setattr(
            DpePipeline, "min_staging_rows_final", property(lambda _: 1)
        )
        try:
            first = _observe(connection, pg_dsn, tmp_path / "first", _first_observation)
            second = _observe(
                connection, pg_dsn, tmp_path / "second", _second_observation
            )
            _publish_address_context(pg_dsn)
            yield Observed(pg_dsn, first, second, _stored_rows(connection))
        finally:
            connection.rollback()
            _cleanup(connection)
            connection.execute(text("DROP TABLE IF EXISTS public.adresses CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS etl_publications CASCADE"))
            connection.commit()
    engine.dispose()


@pytest.fixture
async def http(observed, client, monkeypatch):
    """Bind the real documentary routes and service factory to the observation."""
    engine = create_async_engine(
        observed.dsn.replace("postgresql://", "postgresql+asyncpg://"),
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides.pop(require_publication, None)
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(logements, "async_session", sessions)
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    try:
        yield client, sessions
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def test_rows_without_geometry_are_stored_present_without_position(observed):
    rows = observed.rows
    for number in (WITH_CONTEXT, NO_BAN_KEY, KEY_WITHOUT_ADDRESS, LOSES_GEOMETRY):
        row = rows[number]
        assert row["source_state"] == "present", number
        assert row["is_tombstone"] is False, number
        assert row["no_position"] is True, number
        assert row["longitude"] is None and row["latitude"] is None, number
    # The source key is kept, and still no BAN geometry is borrowed for it.
    assert rows[WITH_CONTEXT]["identifiant_ban"] == CONTEXT_BAN
    assert rows[WITH_CONTEXT]["etiquette_dpe"] == "E"
    # A previous position is never kept once the source drops its geometry.
    assert rows[LOSES_GEOMETRY]["no_position"] is True
    # Documents with source geometry keep a position.
    assert rows[SECOND_ONLY]["no_position"] is False
    assert observed.first == {"documents_sans_geometrie": 4}
    assert observed.second == {"documents_sans_geometrie": 4}


def test_absence_from_the_next_observation_deletes_nothing(observed):
    dropped = observed.rows[DROPPED_LATER]
    assert dropped["source_state"] == "absent_from_complete_scope"
    assert dropped["is_tombstone"] is True
    assert dropped["no_position"] is True
    # Eight documents per observation: seven kept present, one retired, one new.
    assert len(observed.rows) == 9
    assert sum(row["source_state"] == "present" for row in observed.rows.values()) == 8


def test_work_base_count_queries_split_consultable_and_flagged(observed):
    """Same predicates as the work-base gate of plan 02-16, task 2."""
    with psycopg.connect(observed.dsn) as conn:
        row = conn.execute("""
            SELECT count(*) AS sans_geometrie,
                   count(*) FILTER (WHERE d.identifiant_ban IS NOT NULL
                       AND EXISTS (SELECT 1 FROM public.adresses a
                                   WHERE a.ban_id = d.identifiant_ban
                                     AND a.code_departement = d.code_departement))
                       AS consultables
            FROM public.diagnostics_dpe d
            WHERE d.position IS NULL AND d.source_state = 'present'
              AND d.etiquette_dpe IS NOT NULL
        """).fetchone()
    # Only the consulted fixture carries an energy class among the four.
    assert row == (1, 1)


async def test_document_with_ban_context_is_consulted_by_reference_v2(http, observed):
    client, sessions = http
    seeded = await load_dpe_inventory(sessions, ban_id=CONTEXT_BAN)
    assert seeded.state == "ready" and seeded.total == 1

    listed = await client.get(
        "/v1/bien/dpe/inventaire", params={"contexte": seeded.context_ref}
    )
    assert listed.status_code == 200
    inventory = listed.json()
    assert inventory["total"] == 1
    assert inventory["distribution"]["E"] == 1

    response = await client.get(
        "/v1/bien/dpe/document",
        params={
            "contexte": seeded.context_ref,
            "reference": inventory["items"][0]["document_ref"],
        },
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    document = response.json()
    assert document["version"] == 2
    assert "numero_dpe" not in document
    assert WITH_CONTEXT not in response.text
    assert "position" not in document and "longitude" not in document
    assert document["type_batiment"] == CONSULTED["type_batiment"]
    assert document["surface_habitable"] == CONSULTED["surface_habitable_logement"]
    assert document["etiquette_dpe"] == CONSULTED["etiquette_dpe"]
    assert document["etiquette_ges"] == CONSULTED["etiquette_ges"]
    assert document["date_etablissement_dpe"] == CONSULTED["date_etablissement_dpe"]
    assert document["date_fin_validite_dpe"] == CONSULTED["date_fin_validite_dpe"]
    assert document["conso_5_usages_ep"] == CONSULTED["conso_5_usages_ep"]
    assert document["emission_ges_5_usages"] == CONSULTED["emission_ges_5_usages"]


async def test_document_without_ban_context_is_kept_but_never_referenced(
    http, observed
):
    client, sessions = http
    for number in (NO_BAN_KEY, KEY_WITHOUT_ADDRESS):
        assert observed.rows[number]["source_state"] == "present"

    # The key exists at the source, but no local address carries it.
    unknown = await load_dpe_inventory_result(sessions, ban_id=UNKNOWN_BAN)
    assert unknown.page.state == "identity_unavailable"
    assert unknown.page.items == [] and unknown.page.context_ref is None

    # The only address inventory lists its own document, never these two.
    seeded = await load_dpe_inventory(sessions, ban_id=CONTEXT_BAN)
    assert seeded.total == 1

    # A validly signed reference pointing at a document outside the context
    # fails explicitly: no reconciliation is invented across addresses.
    for number in (NO_BAN_KEY, KEY_WITHOUT_ADDRESS):
        forged = issue_document(
            seeded.context_ref,
            document_id=observed.rows[number]["id"],
            dpe_number=number,
        )
        with pytest.raises(RefreshRequiredError):
            await load_dpe_document(
                sessions, context_ref=seeded.context_ref, document_ref=forged
            )
        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": seeded.context_ref, "reference": forged},
        )
        assert response.status_code == 409
        assert number not in response.text


async def test_spatial_reader_never_returns_a_document_without_position(http, observed):
    _, sessions = http
    async with sessions() as session:
        # Lambert 93 (354780.58, 6690061.58) of the fixture, in WGS84.
        found = await queries.dpe_par_point(
            session, lat=47.0, lon=-1.6, rayon_m=500_000
        )
    numbers = {row["numero_dpe"] for row in found}
    assert numbers
    assert not numbers & {WITH_CONTEXT, NO_BAN_KEY, KEY_WITHOUT_ADDRESS, LOSES_GEOMETRY}
