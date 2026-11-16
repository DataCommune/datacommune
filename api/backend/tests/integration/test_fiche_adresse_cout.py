"""Cost of the address page reads under load (k6 firing of 2026-11-02).

The firing put 63 % of the PostgreSQL time in two reads of the address page,
and the national trigram fallback of the search at 10 s a call
(``perf/k6/RESULTATS-2026-11-02.md`` § 5.2). Proven here against a real
PostGIS at migration head, whose tables carry the 102 departement partitions
plus the default, as the national base:

1. transports near a point: the geometry bounding box keeps every row the
   geodesic predicate keeps, half a metre inside and outside the radius, at
   any latitude, when the disc reaches a pole and across the antimeridian;
2. buildings on a parcel: the read locks one partition of ``batiments``,
   not all of them, and counts the same buildings;
3. Strategy D of the geocoder runs under its own budget: past it the
   statement is cancelled (an outage under ``"lever"``, no result under
   ``"taire"``), within it the rows are those served without budget, and the
   transaction keeps its own ``statement_timeout`` in every case.
4. the connections of the API (``app.database.CONNECT_ARGS``) never compile a
   statement with JIT, whatever its cost.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 -Paths
        tests/integration/test_fiche_adresse_cout.py
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adresse.relations import BatimentsParcelle, batiments_sur_parcelle
from app.ban import queries as ban_queries
from app.ban.queries import GeocodageIndisponibleError, geocoder
from app.config import settings
from app.transport.queries import gare_la_plus_proche, transports_a_proximite
from tests.integration.test_instance_scope import BACKEND

# (lon, lat): the West of France, near the equator, the southern hemisphere,
# 60° and 78° north, a disc over the North Pole, a disc across the antimeridian.
CENTRES = (
    (-1.5536, 47.2184),
    (-52.33, 4.94),
    (55.45, -20.88),
    (24.94, 60.17),
    (15.63, 78.22),
    (0.0, 89.995),
    (179.995, -16.5),
)
RAYONS_M = (100.0, 1000.0, 5000.0)
AZIMUTS = range(0, 360, 30)
RAYON_GARE_M = 20_000.0

PARCELLE = "85191000AH0001"
CARRE = "POLYGON((-1.43 46.67,-1.429 46.67,-1.429 46.671,-1.43 46.671,-1.43 46.67))"

# Strategy D only: no commune in the query, and a typo that no full-text
# lexeme carries (Strategy B returns nothing).
FAUTE_DE_FRAPPE = "12 rue de la mairei"
ADRESSES_D = 30_000


def _nom(sens: str, centre: int, rayon: float, azimut: int) -> str:
    return f"{sens}-{centre}-{rayon:g}-{azimut}"


def _seed(conn: psycopg.Connection) -> None:
    for c, (lon, lat) in enumerate(CENTRES):
        for rayon in RAYONS_M:
            for azimut in AZIMUTS:
                for sens, distance in (("in", rayon - 0.5), ("out", rayon + 0.5)):
                    conn.execute(
                        "INSERT INTO arrets_transport (stop_id, stop_name, position) "
                        "VALUES (%s, %s, ST_Project(ST_MakePoint(%s, %s)::geography, "
                        "%s, radians(%s))::geometry)",
                        (_nom(sens, c, rayon, azimut),) * 2
                        + (lon, lat, distance, azimut),
                    )
        for azimut in AZIMUTS:
            # The nearest station is just inside 20 km, at a varying azimuth.
            distance = RAYON_GARE_M - 0.5 - azimut / 1000
            conn.execute(
                "INSERT INTO gares (nom_gare, position) VALUES (%s, ST_Project("
                "ST_MakePoint(%s, %s)::geography, %s, radians(%s))::geometry)",
                (_nom("gare", c, RAYON_GARE_M, azimut), lon, lat, distance, azimut),
            )

    conn.execute(
        "INSERT INTO parcelles (id, code_departement, id_parcelle, commune, geometrie) "
        "VALUES (1, '85', %s, '85191', ST_GeomFromText(%s, 4326))",
        (PARCELLE, CARRE),
    )
    for id_, departement, usage, x in (
        (1, "85", "Résidentiel", -1.4298),
        (2, "85", "Annexe", -1.4294),
        (3, "85", None, -1.4292),
        # Same footprint, another partition: never a building of the parcel.
        (4, "44", "Résidentiel", -1.4298),
    ):
        conn.execute(
            "INSERT INTO batiments (id, code_departement, cleabs, usage_1, geometrie) "
            "VALUES (%s, %s, %s, %s, ST_Buffer(ST_SetSRID(ST_MakePoint(%s, 46.6705), "
            "4326), 0.00005, 2))",
            (id_, departement, f"BAT{id_}", usage, x),
        )

    conn.execute(
        "INSERT INTO adresses (id, code_departement, ban_id, numero, nom_voie, "
        "code_postal, code_commune, nom_commune, position) "
        "SELECT 91000000 + g, '85', '85999_' || g, (g %% 50)::text, "
        "'rue de la mairie', '85999', '85999', 'Villetest', "
        "ST_SetSRID(ST_MakePoint(-1.4, 46.6), 4326) "
        "FROM generate_series(1, %s) AS g",
        (ADRESSES_D,),
    )


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn: str) -> str:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    # Committed: the lock count below reads a transaction without any write.
    with psycopg.connect(isolated_pg_dsn) as conn:
        _seed(conn)
    return isolated_pg_dsn


@pytest.fixture()
async def session(migrated: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            # As every connection of the API (app.database).
            await session.execute(text("SET statement_timeout = '15s'"))
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


async def _arrets_geodesiques(session: AsyncSession, lon, lat, rayon) -> set[str]:
    """The stops the geodesic predicate alone keeps, as before the box."""
    resultat = await session.execute(
        text("""
            SELECT stop_name FROM arrets_transport
            WHERE ST_DWithin(
                position::geography, ST_MakePoint(:lon, :lat)::geography, :rayon
            )
        """),
        {"lon": lon, "lat": lat, "rayon": rayon},
    )
    return set(resultat.scalars())


@pytest.mark.parametrize("rayon", RAYONS_M)
@pytest.mark.parametrize(("lon", "lat"), CENTRES)
async def test_the_bounding_box_keeps_every_stop_within_the_radius(
    session, lon, lat, rayon
):
    lignes = await transports_a_proximite(
        session, lat, lon, rayon, limit=10_000, sources=("transports",)
    )

    servis = {ligne["stop_name"] for ligne in lignes}
    assert servis == await _arrets_geodesiques(session, lon, lat, rayon)
    # The edge is exercised: every stop half a metre inside, none outside.
    c = CENTRES.index((lon, lat))
    assert {_nom("in", c, rayon, a) for a in AZIMUTS} <= servis
    assert not {_nom("out", c, rayon, a) for a in AZIMUTS} & servis


@pytest.mark.parametrize(("lon", "lat"), CENTRES)
async def test_the_nearest_station_is_the_geodesic_one(session, lon, lat):
    gare = await gare_la_plus_proche(session, lon, lat, RAYON_GARE_M)

    c = CENTRES.index((lon, lat))
    assert gare is not None
    assert gare["nom_gare"] == _nom("gare", c, RAYON_GARE_M, max(AZIMUTS))


async def test_the_buildings_of_a_parcel_lock_one_partition(session):
    compte = await batiments_sur_parcelle(session, PARCELLE)

    # Three buildings on the parcel in its partition, one of them an annex,
    # an unknown use counted; the twin footprint of departement 44 is not.
    assert compte == BatimentsParcelle(total=3, hors_annexe=2)
    verrouillees = await session.execute(
        text("""
            SELECT count(*) FROM pg_locks l
            JOIN pg_inherits i ON i.inhrelid = l.relation
            WHERE l.pid = pg_backend_pid() AND l.locktype = 'relation'
              AND i.inhparent = 'batiments'::regclass
        """)
    )
    assert verrouillees.scalar_one() == 1


async def _timeout(session: AsyncSession) -> str:
    return (await session.execute(text("SHOW statement_timeout"))).scalar_one()


async def test_strategy_d_past_its_budget_is_an_outage(session, monkeypatch):
    monkeypatch.setattr(ban_queries, "BUDGET_STRATEGIE_D_MS", 1)

    with pytest.raises(GeocodageIndisponibleError):
        await geocoder(
            session, FAUTE_DE_FRAPPE, 8, departements_heberges=["85"], erreurs="lever"
        )
    assert await _timeout(session) == "15s"
    taire = await geocoder(session, FAUTE_DE_FRAPPE, 8, departements_heberges=["85"])
    assert taire == []
    assert await _timeout(session) == "15s"


async def test_strategy_d_within_its_budget_serves_the_same_rows(session, monkeypatch):
    sous_budget = await geocoder(
        session, FAUTE_DE_FRAPPE, 8, departements_heberges=["85"], erreurs="lever"
    )
    assert await _timeout(session) == "15s"
    monkeypatch.setattr(ban_queries, "BUDGET_STRATEGIE_D_MS", None)
    sans_budget = await geocoder(
        session, FAUTE_DE_FRAPPE, 8, departements_heberges=["85"], erreurs="lever"
    )

    assert len(sous_budget) == 8
    assert sous_budget == sans_budget


async def _plan_force(migrated: str, connect_args: dict) -> tuple[bool, dict, str]:
    """(JIT available, plan, statement_timeout) of a statement over every JIT
    threshold, on a connection opened with *connect_args*."""
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args=connect_args,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET jit_above_cost = 0"))
            dispo = (await conn.execute(text("SELECT pg_jit_available()"))).scalar_one()
            plan = (
                await conn.execute(
                    text(
                        "EXPLAIN (ANALYZE, FORMAT JSON) "
                        "SELECT count(*) FROM generate_series(1, 1000)"
                    )
                )
            ).scalar_one()
            delai = (await conn.execute(text("SHOW statement_timeout"))).scalar_one()
            plan = json.loads(plan) if isinstance(plan, str) else plan
            return dispo, plan[0], delai
    finally:
        await engine.dispose()


async def test_api_connections_never_compile_jit(migrated):
    from app.database import CONNECT_ARGS

    dispo, plan_serveur, _ = await _plan_force(migrated, {})
    _, plan_api, delai_api = await _plan_force(migrated, CONNECT_ARGS)

    # The server compiles by default: the check below is not vacuous.
    assert dispo and "JIT" in plan_serveur
    assert "JIT" not in plan_api
    assert delai_api == "15s"
