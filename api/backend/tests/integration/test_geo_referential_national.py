"""R1 route A (plan 03-14): an out-of-scope commune is known end to end.

The real ``scripts/import_geo_boundaries.import_boundaries`` imports, on a
migrated PostGIS harness, the communes of every departement its producer
lists (a fake HTTP client serves 44, 79 and 85) and the sections of the
scope (44, 85) only; the real superficie backfill and synthesis rebuild run.
Then 79211 (a synthetic code outside the scope, unrelated to the producer,
where the real Le Pin is 79210) crosses every route of
the API: search proposes it out of coverage, the referential names it, the
commune route and the comparison serve it out of coverage without data, and
a point inside it resolves it out of coverage instead of a 404. A code that
does not exist stays unknown.

Coverage comes from the scope in base (``app.scope``), never from membership
of ``geo_communes``: this is what each link below proves.
"""

import gzip
import json
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
from app.commune.router import router as commune_router
from app.config import settings
from app.database import get_db
from app.zone.router import router as zone_router
from scripts import import_geo_boundaries as geo
from tests.integration.instance_scope_fixture import HOSTABLE_KEYS, install_scope_at
from tests.test_import_geo_boundaries_scope import FakeClient

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
SCOPE = ("44", "85")
LISTED = ("44", "79", "85")

# (code, nom, departement, population, west longitude, south latitude):
# disjoint 0.1-degree squares.
COMMUNES = (
    ("44124", "Le Pin", "44", 700, -1.2, 47.5),
    ("44109", "Nantes", "44", 320000, -1.6, 47.2),
    ("79211", "Le Pin", "79", 1000, -0.7, 46.8),
    ("85262", "Saint-Philbert-de-Bouaine", "85", 3500, -1.5, 46.9),
)
INSIDE_79211 = (46.85, -0.65)  # lat, lon
OUTSIDE_EVERY_COMMUNE = (45.0, 2.0)
UNKNOWN = "00000"


def _square(lon: float, lat: float, size: float = 0.1) -> dict:
    ring = [
        [lon, lat],
        [lon + size, lat],
        [lon + size, lat + size],
        [lon, lat + size],
        [lon, lat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _collection(features: list[dict]) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def _communes_of(dept: str) -> bytes:
    return _collection(
        [
            {
                "type": "Feature",
                "properties": {
                    "code": code,
                    "nom": nom,
                    "population": population,
                    "codeDepartement": departement,
                },
                "geometry": _square(lon, lat),
            }
            for code, nom, departement, population, lon, lat in COMMUNES
            if departement == dept
        ]
    )


def _sections_of(dept: str) -> bytes:
    code, _nom, _dept, _population, lon, lat = next(c for c in COMMUNES if c[2] == dept)
    feature = {
        "type": "Feature",
        "properties": {
            "id": f"{code}000A1",
            "commune": code,
            "prefixe": "000",
            "code": "A1",
        },
        "geometry": _square(lon, lat, 0.05),
    }
    return gzip.compress(_collection([feature]))


def _producer() -> FakeClient:
    polygons = _collection(
        [
            {
                "type": "Feature",
                "properties": {"code": dept, "nom": f"D{dept}"},
                "geometry": _square(-3.0 + i, 44.0, 0.5),
            }
            for i, dept in enumerate(LISTED)
        ]
    )
    routes: dict[str, bytes | Exception | int] = {
        geo.DEPARTEMENTS_LIST_URL: json.dumps(
            [{"code": dept, "nom": f"D{dept}"} for dept in LISTED]
        ).encode(),
        geo.DEPARTEMENTS_URL: polygons,
    }
    for dept in LISTED:
        routes[geo.COMMUNES_URL.format(dept=dept)] = _communes_of(dept)
    for dept in SCOPE:
        routes[geo.SECTIONS_URL.format(dept=dept)] = _sections_of(dept)
    return FakeClient(routes)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    install_scope_at(isolated_pg_dsn, HOSTABLE_KEYS, SCOPE)
    with pytest.MonkeyPatch.context() as context:
        # No source archive: the archive contract has its own tests.
        for name in (
            "SOURCE_ARCHIVE_DIR",
            "SOURCE_ARCHIVE_RUN_ID",
            "SOURCE_ARCHIVE_MIN_FREE_BYTES",
        ):
            context.delenv(name, raising=False)
        client = _producer()
        with psycopg.connect(isolated_pg_dsn) as conn:
            geo.import_boundaries(conn, client)
    return isolated_pg_dsn


@pytest.fixture
async def api(migrated) -> AsyncIterator[httpx.AsyncClient]:
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
    application.include_router(commune_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c
    finally:
        await engine.dispose()


def _out_of_scope(hebergement: dict) -> bool:
    return (hebergement["etat"], hebergement["motif"]) == (
        "hors_couverture",
        "territoire_hors_perimetre",
    )


# (a) import -> referential ------------------------------------------------


def test_import_fills_listed_departements_and_scoped_sections(migrated):
    with psycopg.connect(migrated) as conn:
        communes = conn.execute(
            "SELECT departement_code, count(*) FROM geo_communes GROUP BY 1 ORDER BY 1"
        ).fetchall()
        sections = conn.execute(
            "SELECT departement_code, count(*) FROM geo_sections GROUP BY 1 ORDER BY 1"
        ).fetchall()
    assert communes == [("44", 2), ("79", 1), ("85", 1)]
    assert sections == [("44", 1), ("85", 1)]


# (b) referential -> search ------------------------------------------------


async def test_search_proposes_the_out_of_scope_homonym_out_of_coverage(api):
    response = await api.get("/v1/communes/search", params={"q": "le pin"})
    assert response.status_code == 200, response.text
    by_code = {r["code_insee"]: r for r in response.json()["donnees"]}
    assert set(by_code) == {"44124", "79211"}
    assert by_code["44124"]["hebergement"]["etat"] == "heberge"
    assert _out_of_scope(by_code["79211"]["hebergement"])
    assert by_code["79211"]["departement"] == "79"


# (c) referential -> identity ----------------------------------------------


async def test_referential_names_the_out_of_scope_commune_and_not_an_unknown_code(api):
    response = await api.get("/v1/communes", params={"code_insee": "79211"})
    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]["results"]
    assert (row["code_insee"], row["nom"], row["departement"]) == (
        "79211",
        "Le Pin",
        "79",
    )
    response = await api.get("/v1/communes", params={"code_insee": UNKNOWN})
    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["results"] == []


# (d) referential -> commune route -----------------------------------------


async def test_commune_route_serves_the_out_of_scope_commune_out_of_coverage(api):
    response = await api.get("/v1/commune", params={"code_insee": "79211"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert _out_of_scope(body["meta"]["hebergement"]["commune"])


# (e) referential -> comparison --------------------------------------------


async def test_comparison_keeps_an_out_of_scope_column_and_reports_unknown_codes(api):
    response = await api.get(
        "/v1/communes/comparaison", params={"codes": "44124,79211"}
    )
    assert response.status_code == 200, response.text
    donnees = response.json()["donnees"]
    assert donnees["codes_inconnus"] == []
    first, outside = donnees["communes"]
    assert first["code_insee"] == "44124"
    assert (outside["code_insee"], outside["nom"]) == ("79211", "Le Pin")
    assert outside["hebergement"]["etat"] == "hors_couverture"
    assert outside["synthese"] is None

    response = await api.get(
        "/v1/communes/comparaison", params={"codes": f"44124,{UNKNOWN}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["codes_inconnus"] == [UNKNOWN]


# (f) referential -> GPS -----------------------------------------------------


async def test_point_inside_the_out_of_scope_commune_resolves_it_out_of_coverage(api):
    lat, lon = INSIDE_79211
    response = await api.get("/v1/commune", params={"lat": lat, "lon": lon})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["commune"]["etat"] == "hors_couverture"

    lat, lon = OUTSIDE_EVERY_COMMUNE
    response = await api.get("/v1/commune", params={"lat": lat, "lon": lon})
    assert response.status_code == 404, response.text
