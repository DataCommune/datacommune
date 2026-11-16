"""04.6-02: the directory of communes against PostGIS.

``GET /v1/communes/annuaire`` filters the referential before pagination: the
served departements (``couverture=servies``, default), the name, the
departement and an inclusive range of the served population (H6) apply to the
total and to the page alike; an unknown population is outside any bounded
range; a page past the total is empty and still says the total. The prices of
the hosted codes come from ``v_commune_synthese`` through the publication
barrier: a commune without any DVF row has NULL counts, served as
``not_reported``, never as zero.

The scope is written by the production path (``replace_scope``) and read by
the real scope dependency of the API: only the session is overridden.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.exceptions import register_exception_handlers
from app.commune.dependencies import COMMUNE_INTERMEDIATES
from app.config import settings
from app.database import get_db
from app.zone.router import router as zone_router
from tests.integration.instance_scope_fixture import HOSTABLE_KEYS, install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]

# (code, nom, departement, geo_communes population, lon, lat)
COMMUNES = (
    ("85191", "La Roche-sur-Yon", "85", 54849, -1.43, 46.67),
    ("44109", "Nantes", "44", 327734, -1.55, 47.22),
    # Referential 5711, served 3746 (demographie): the range reads the served one.
    ("85901", "Rang A", "85", 5711, -1.30, 46.80),
    ("85902", "Rang B", "85", 5000, -1.20, 46.90),
    # Unknown population: outside any bounded range, listed without one.
    ("85903", "Sans Population", "85", None, -1.10, 46.95),
    # Departement outside the hosted perimeter: listed only with couverture=toutes.
    ("79210", "Hors Perimetre", "79", 4000, -0.63, 46.86),
)
DEMOGRAPHIE = (
    ("85191", "85", 2022, 54699),
    ("44109", "44", 2022, 325070),
    ("85901", "85", 2022, 3746),
    ("85902", "85", 2022, 4000),
)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    with psycopg.connect(isolated_pg_dsn) as conn:
        for code, nom, dept, population, lon, lat in COMMUNES:
            conn.execute(
                "INSERT INTO geo_communes (code, nom, departement_code, "
                "population, geometry) VALUES (%s, %s, %s, %s, "
                "ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0.01)))",
                (code, nom, dept, population, lon, lat),
            )
        for code, dept, annee, population in DEMOGRAPHIE:
            conn.execute(
                "INSERT INTO demographie (code_insee, code_departement, annee, "
                "population) VALUES (%s, %s, %s, %s)",
                (code, dept, annee, population),
            )
    install_scope_at(isolated_pg_dsn, sorted(HOSTABLE_KEYS), ("44", "85"))
    with psycopg.connect(isolated_pg_dsn, autocommit=True) as conn:
        for view in COMMUNE_INTERMEDIATES.values():
            conn.execute(f"REFRESH MATERIALIZED VIEW {view}")
        conn.execute("REFRESH MATERIALIZED VIEW v_commune_synthese")
    return isolated_pg_dsn


@pytest.fixture
async def client(migrated) -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(zone_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c
    finally:
        await engine.dispose()


async def _annuaire(client: httpx.AsyncClient, **params) -> dict:
    response = await client.get("/v1/communes/annuaire", params=params)
    assert response.status_code == 200, response.text
    return response.json()["donnees"]


def _codes(donnees: dict) -> list[str]:
    return [row["code_insee"] for row in donnees["results"]]


async def test_default_lists_the_served_departements_only(client):
    servies = await _annuaire(client, per_page=100)
    toutes = await _annuaire(client, per_page=100, couverture="toutes")

    assert servies["departements_servis"] == ["44", "85"]
    assert "79210" not in _codes(servies)
    assert servies["total_count"] == 5
    assert "79210" in _codes(toutes)
    assert toutes["total_count"] == 6


async def test_the_population_range_bounds_the_total_and_the_page(client):
    ranged = await _annuaire(
        client, population_min=3746, population_max=4000, sort_by="population"
    )

    # Inclusive bounds on the served value: 85901 (served 3746, referential
    # 5711) and 85902 (4000) are in; the unknown population is out.
    assert sorted(_codes(ranged)) == ["85901", "85902"]
    assert ranged["total_count"] == 2
    empty = await _annuaire(client, population_min=1_000_000)
    assert (empty["results"], empty["total_count"]) == ([], 0)


async def test_the_total_follows_name_and_departement_with_paging(client):
    first = await _annuaire(client, nom="rang", per_page=1, sort_by="nom", order="asc")
    second = await _annuaire(
        client, nom="rang", per_page=1, page=2, sort_by="nom", order="asc"
    )
    beyond = await _annuaire(client, nom="rang", per_page=1, page=3)
    vendee = await _annuaire(client, departement="85", per_page=100)

    assert (_codes(first), first["total_count"], first["has_next"]) == (
        ["85901"],
        2,
        True,
    )
    assert (_codes(second), second["has_next"]) == (["85902"], False)
    assert (beyond["results"], beyond["total_count"]) == ([], 2)
    assert vendee["total_count"] == 4


async def test_prices_without_dvf_rows_are_not_reported_never_zero(client):
    donnees = await _annuaire(client, departement="85", per_page=100)

    assert donnees["prix"]["etat"] == "servi"
    roche = next(r for r in donnees["results"] if r["code_insee"] == "85191")
    assert (roche["population"], roche["population_annee"]) == (54699, 2022)
    for kind in ("maison", "appartement"):
        price = roche["prix"][kind]
        assert price["mediane_m2"] is None
        assert price["effectif"] is None
        assert price["etat"]["etat"] == "not_reported"
    # No served count: no window is invented.
    assert donnees["prix"]["fenetre_mediane_dvf"] is None


async def test_out_of_coverage_rows_carry_their_reason(client):
    donnees = await _annuaire(client, couverture="toutes", departement="79")

    (row,) = donnees["results"]
    assert row["hebergement"]["etat"] == "hors_couverture"
    assert row["prix"]["maison"]["etat"] == {
        "etat": "out_of_coverage",
        "motif": "territoire_hors_perimetre",
    }
    assert donnees["prix"]["etat"] == "sans_lecture"
