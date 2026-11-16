"""Internal links of the commune pages (SEO audit 2026-11-14) on PostGIS.

Two reads of the referential: the names of every page of a departement
(``/v1/departements/{code}/fiches``, the A-Z directory of the departement
page) and the pages near a commune (``/v1/communes/voisines``). The fixture
is a grid of unit squares, so that bordering and distance are known exactly:

    row 2:  44003 44004 44005
    row 1:  44006 [44109] 44007   44008 (half a square right of 44007, 0.3 up)
    row 0:  44010 44011 44012

44008 borders nothing: 44005 and 44007 are 0.5 degree from it, 44012 0.58.

and Paris (75056) with two arrondissements side by side, next to a commune.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.common.exceptions import register_exception_handlers
from app.database import get_db
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from app.zone import router as zone_module
from app.zone.router import router as zone_router

# ``migrated`` (and the ``migrated_dsn`` it needs) are fixtures: imported for
# pytest, not called.
from tests.integration.test_commune_coverage_contract import (  # noqa: F401
    _engine,
    migrated,
    migrated_dsn,
)

pytestmark = pytest.mark.destructive


def _square(x: float, y: float) -> str:
    """The unit square of the grid at (x, y), near Nantes."""
    return (
        f"ST_Multi(ST_SetSRID(ST_MakeEnvelope({-1.6 + x}, {47.2 + y}, "
        f"{-0.6 + x}, {48.2 + y}), 4326))"
    )


# code, name, x, y, population
GRID = (
    ("44003", "Ancenis", 0, 2, 7000),
    ("44004", "Basse-Goulaine", 1, 2, 9000),
    ("44005", "Bouaye", 2, 2, 8000),
    ("44006", "Carquefou", 0, 1, 20000),
    ("44109", "Nantes", 1, 1, 320000),
    ("44007", "Couëron", 2, 1, 22000),
    ("44008", "Guérande", 3.5, 1.3, 16000),
    ("44010", "Indre", 0, 0, 4000),
    ("44011", "La Baule", 1, 0, None),
    ("44012", "Orvault", 2, 0, 27000),
)


def _geo(conn) -> None:
    conn.execute("TRUNCATE geo_arrondissements")
    conn.execute("DELETE FROM geo_communes")
    conn.execute("DELETE FROM geo_departements")
    for code, nom in (("44", "Loire-Atlantique"), ("75", "Paris")):
        conn.execute(
            "INSERT INTO geo_departements (code, nom, geometry) "
            f"VALUES (%s, %s, {_square(0, 0)})",
            (code, nom),
        )
    for code, nom, x, y, population in GRID:
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, population, "
            f"geometry) VALUES (%s, %s, '44', %s, {_square(x, y)})",
            (code, nom, population),
        )
    # Paris far from the grid: its arrondissements side by side, Paris itself
    # over both, and one commune bordering the 12e.
    conn.execute(
        "INSERT INTO geo_communes (code, nom, departement_code, population, "
        "geometry) VALUES ('75056', 'Paris', '75', 2100000, "
        "ST_Multi(ST_SetSRID(ST_MakeEnvelope(10, 50, 12, 51), 4326)))"
    )
    conn.execute(
        "INSERT INTO geo_communes (code, nom, departement_code, population, "
        f"geometry) VALUES ('94080', 'Vincennes', '94', 50000, {_square(13.6, 2.8)})"
    )
    for code, nom, x in (
        ("75101", "Paris 1er Arrondissement", 10),
        ("75112", "Paris 12e Arrondissement", 11),
    ):
        conn.execute(
            "INSERT INTO geo_arrondissements (code, nom, departement_code, "
            "commune_code, population, geometry) VALUES (%s, %s, '75', '75056', "
            f"NULL, ST_Multi(ST_SetSRID(ST_MakeEnvelope({x}, 50, {x + 1}, 51), "
            "4326)))",
            (code, nom),
        )


@pytest.fixture
def grid(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    _geo(conn)
    return conn, dsn


@asynccontextmanager
async def _api(dsn, departements=("44", "75")):
    engine = _engine(dsn, None)
    sessions = async_sessionmaker(engine)
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(zone_router)

    async def database():
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db] = database
    application.dependency_overrides[get_instance_scope] = lambda: InstanceScope(
        frozenset({"ban"}), tuple(departements)
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(application), base_url="http://fixture"
        ) as client:
            yield client
    finally:
        await engine.dispose()


async def test_every_page_of_a_departement_is_named(grid):
    _, dsn = grid
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/44/fiches")
        paris = await api.get("/v1/departements/75/fiches")

    assert response.status_code == 200, response.text
    fiches = response.json()["donnees"]["fiches"]
    assert [(f["code_insee"], f["nom"]) for f in fiches] == sorted(
        (code, nom) for code, nom, *_ in GRID
    )
    assert {f["type_zone"] for f in fiches} == {"commune"}
    assert paris.status_code == 200, paris.text
    assert [
        (f["code_insee"], f["nom"], f["type_zone"])
        for f in paris.json()["donnees"]["fiches"]
    ] == [
        ("75056", "Paris", "commune"),
        ("75101", "Paris 1er Arrondissement", "arrondissement"),
        ("75112", "Paris 12e Arrondissement", "arrondissement"),
    ]


async def test_bordering_communes_come_first_by_population(grid):
    _, dsn = grid
    async with _api(dsn) as api:
        response = await api.get("/v1/communes/voisines?code_insee=44109")

    assert response.status_code == 200, response.text
    body = response.json()["donnees"]
    assert body["code_insee"] == "44109"
    voisines = body["voisines"]
    # The eight squares around Nantes border it (corners included), the
    # largest first, an unknown population last; Guérande is too far.
    assert [v["code_insee"] for v in voisines] == [
        "44012",
        "44007",
        "44006",
        "44004",
        "44005",
        "44003",
        "44010",
        "44011",
    ]
    assert all(v["limitrophe"] for v in voisines)
    assert voisines[0] == {
        "code_insee": "44012",
        "nom": "Orvault",
        "departement_code": "44",
        "type_zone": "commune",
        "limitrophe": True,
    }


async def test_a_far_commune_lists_the_nearest_after_the_bordering_ones(
    grid, monkeypatch
):
    monkeypatch.setattr(zone_module, "VOISINES_MAX", 3)
    _, dsn = grid
    async with _api(dsn) as api:
        response = await api.get("/v1/communes/voisines?code_insee=44008")

    voisines = response.json()["donnees"]["voisines"]
    # No square borders Guérande: the nearest first, by distance and never by
    # population (Orvault, the largest, is the farthest of the three).
    assert [(v["code_insee"], v["limitrophe"]) for v in voisines] == [
        ("44005", False),
        ("44007", False),
        ("44012", False),
    ]


async def test_an_arrondissement_lists_its_siblings_never_its_commune(grid):
    _, dsn = grid
    async with _api(dsn) as api:
        response = await api.get("/v1/communes/voisines?code_insee=75112")
        commune = await api.get("/v1/communes/voisines?code_insee=94080")

    voisines = response.json()["donnees"]["voisines"]
    codes = [(v["code_insee"], v["type_zone"]) for v in voisines]
    assert ("75101", "arrondissement") in codes
    assert ("75056", "commune") not in codes
    assert codes[:2] == [("94080", "commune"), ("75101", "arrondissement")]
    # A commune lists communes only: Paris, never its arrondissements.
    autour = commune.json()["donnees"]["voisines"]
    assert autour[0]["code_insee"] == "75056"
    assert all(v["type_zone"] == "commune" for v in autour)


async def test_codes_are_validated_then_looked_up(grid):
    _, dsn = grid
    async with _api(dsn) as api:
        malformed = await api.get("/v1/communes/voisines?code_insee=4410")
        unknown = await api.get("/v1/communes/voisines?code_insee=44999")
        lower = await api.get("/v1/communes/voisines?code_insee=2a004")

    assert malformed.status_code == 422
    assert unknown.status_code == 404
    assert lower.status_code == 404
    assert "2A004" in lower.text


async def test_the_nearest_contours_are_read_by_the_index(grid):
    conn, _ = grid
    conn.execute("SET enable_seqscan = off")
    plan = "\n".join(
        row[0]
        for row in conn.execute(
            "EXPLAIN SELECT code FROM geo_communes c "
            "ORDER BY c.geometry <-> (SELECT geometry FROM geo_communes "
            "WHERE code = '44109') LIMIT 24"
        )
    )
    conn.execute("RESET enable_seqscan")
    assert "idx_geo_comm_geom" in plan, plan
