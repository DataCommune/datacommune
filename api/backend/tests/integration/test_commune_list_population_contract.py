"""SEM-02 / COM-01 (H6): the commune directory serves the population of the
commune page and of the search.

``GET /v1/communes`` (paginated directory, and its ``code_insee`` mode) serves
the latest ``demographie`` year, with its year and source, when that dataset
is hosted for the commune's departement and has a row; otherwise the
``geo_communes`` referential value, whose year is unknown (``population_annee``
null, never invented). ``sort_by=population`` orders on the served value.
``demographie`` is read only for the hosted departements (D-23): the
statements are captured with their parameters.

The values are copied from the work base (2026-09-29): 85191 is
54 849 in ``geo_communes`` and 54 699 (2022) in ``demographie``; 44109 is
327 734 / 325 070.

Not seeded on purpose: 85076 and 85223. Their ``demographie`` 2022 rows
describe the territory before the merges of 85021 and 85233, so the commune
page, the search and the directory serve a partial population for them. That
is a distinct defect, followed in
the internal tracking (2026-09-29, partial population of merged communes).

The scope is written by the production path (``replace_scope``) and read by
the real scope dependency of the API: only the session is overridden.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.exceptions import register_exception_handlers
from app.commune.dependencies import COMMUNE_INTERMEDIATES
from app.commune.router import router as commune_router
from app.config import settings
from app.database import get_db
from app.scope.model import InstanceScope
from app.zone.router import router as zone_router
from tests.integration.instance_scope_fixture import HOSTABLE_KEYS, install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]

# (code, nom, departement, geo_communes population, lon, lat)
COMMUNES = (
    ("85191", "La Roche-sur-Yon", "85", 54849, -1.43, 46.67),
    ("44109", "Nantes", "44", 327734, -1.55, 47.22),
    # The referential order (5711 > 5000) is the reverse of the served one
    # (3746 < 4000): the population sort must follow the served value.
    ("85901", "Rang A", "85", 5711, -1.30, 46.80),
    ("85902", "Rang B", "85", 5000, -1.20, 46.90),
    # Hosted departement, no demographie row: the referential, year unknown.
    ("44901", "Sans Recensement", "44", 800, -1.60, 47.30),
    # Departement outside the hosted perimeter.
    ("79210", "Hors Perimetre", "79", 1000, -0.63, 46.86),
)
# (code_insee, departement, annee, population)
DEMOGRAPHIE = (
    # An older year is never served: the latest one is.
    ("85191", "85", 2021, 54500),
    ("85191", "85", 2022, 54699),
    ("44109", "44", 2022, 325070),
    ("85901", "85", 2022, 3746),
    ("85902", "85", 2022, 4000),
    # Outside the hosted departements: never read, never served.
    ("79210", "79", 2022, 99999),
)
HOSTED_44_85 = [c for c in COMMUNES if c[2] in ("44", "85")]


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
    return isolated_pg_dsn


@dataclass
class Api:
    client: httpx.AsyncClient
    statements: list[tuple[str, object]]

    def reads(self, table: str) -> list[tuple[str, object]]:
        return [(sql, params) for sql, params in self.statements if table in sql]


@pytest.fixture
async def api(migrated) -> AsyncIterator[Api]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    statements: list[tuple[str, object]] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, parameters, _context, _many):
        statements.append((statement, parameters))

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(zone_router)
    application.include_router(commune_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield Api(c, statements)
    finally:
        await engine.dispose()


def _scope(dsn, *, without=(), departements=("44", "85")) -> InstanceScope:
    return install_scope_at(
        dsn, sorted(set(HOSTABLE_KEYS) - set(without)), departements
    )


def _departement_lists(api: Api, table: str) -> list[list[str]]:
    """Every list parameter bound to a statement reading *table*."""
    lists = []
    for _sql, params in api.reads(table):
        values = params.values() if isinstance(params, dict) else (params or ())
        lists.extend(list(value) for value in values if isinstance(value, list))
    return lists


async def _directory(api: Api, **params) -> list[dict]:
    response = await api.client.get("/v1/communes", params=params)
    assert response.status_code == 200, response.text
    return response.json()["donnees"]["results"]


def _served(row: dict) -> tuple:
    """(population, year, source); a missing key reads as None."""
    return (
        row.get("population"),
        row.get("population_annee"),
        row.get("population_source"),
    )


def _by_code(rows: list[dict]) -> dict[str, dict]:
    return {row["code_insee"]: row for row in rows}


async def test_directory_serves_the_dated_population(api, migrated):
    _scope(migrated)

    vendee = _by_code(await _directory(api, departement="85", per_page=100))
    loire = _by_code(await _directory(api, departement="44", per_page=100))

    # Before H6 in the directory: 54849, no year, no source.
    assert _served(vendee["85191"]) == (54699, 2022, "demographie")
    assert _served(loire["44109"]) == (325070, 2022, "demographie")
    assert _served(loire["44901"]) == (800, None, "geo_communes")


async def test_unhosted_departement_keeps_the_referential(api, migrated):
    _scope(migrated)
    api.statements.clear()

    (row,) = await _directory(api, departement="79", per_page=100)

    assert row["code_insee"] == "79210"
    assert _served(row) == (1000, None, "geo_communes")
    # Every statement reading demographie is bound to the hosted departements
    # only; the row seeded for 79 is never served.
    lists = _departement_lists(api, "demographie")
    assert lists
    assert all(sorted(lst) == ["44", "85"] for lst in lists), lists


async def test_without_demographie_the_table_is_never_read(api, migrated):
    _scope(migrated, without={"demographie"})
    api.statements.clear()

    vendee = _by_code(await _directory(api, departement="85", per_page=100))

    assert _served(vendee["85191"]) == (54849, None, "geo_communes")
    assert api.reads("demographie") == []


async def test_population_sort_follows_the_served_value(api, migrated):
    _scope(migrated)

    desc = await _directory(
        api, departement="85", sort_by="population", order="desc", per_page=100
    )
    asc = await _directory(
        api, departement="85", sort_by="population", order="asc", per_page=100
    )
    by_name = await _directory(
        api, departement="85", sort_by="nom", order="asc", per_page=100
    )

    # The referential order would be 85191, 85901 (5711), 85902 (5000).
    assert [r["code_insee"] for r in desc] == ["85191", "85902", "85901"]
    assert [r["code_insee"] for r in asc] == ["85901", "85902", "85191"]
    assert [r["code_insee"] for r in by_name] == ["85191", "85901", "85902"]


async def test_directory_equals_the_search(api, migrated):
    _scope(migrated)

    for code, nom, dept, *_ in HOSTED_44_85:
        directory = _by_code(await _directory(api, departement=dept, per_page=100))
        response = await api.client.get(
            "/v1/communes/search", params={"q": nom, "departement": dept}
        )
        assert response.status_code == 200, response.text
        search = _by_code(response.json()["donnees"])
        assert _served(directory[code]) == _served(search[code]), code

    (row,) = await _directory(api, code_insee="85191")
    response = await api.client.get(
        "/v1/communes/search", params={"q": "La Roche", "departement": "85"}
    )
    (found,) = response.json()["donnees"]
    assert _served(row) == _served(found) == (54699, 2022, "demographie")


async def test_directory_equals_the_commune_page(api, migrated):
    _scope(migrated)
    with psycopg.connect(migrated, autocommit=True) as conn:
        for view in COMMUNE_INTERMEDIATES.values():
            conn.execute(f"REFRESH MATERIALIZED VIEW {view}")
        conn.execute("REFRESH MATERIALIZED VIEW v_commune_synthese")

    response = await api.client.get("/v1/commune", params={"code_insee": "85191"})
    assert response.status_code == 200, response.text
    (page,) = response.json()["donnees"]
    vendee = _by_code(await _directory(api, departement="85", per_page=100))

    assert (page["population"], page["annee_demographie"]) == (54699, 2022)
    assert _served(vendee["85191"])[:2] == (
        page["population"],
        page["annee_demographie"],
    )
