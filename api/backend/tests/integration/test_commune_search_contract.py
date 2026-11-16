"""COM-01 / D-42..D-44: commune search contract on a migrated database.

``GET /v1/communes/search`` serves the commune referential whatever the
hosted scope: homonyms stay distinct results, a commune out of the instance's
perimeter is listed with its reason (never excluded), the departement filter
and the postal-code mode are exact, the order is total and stable, and the
served population names its year and source (H6). ``demographie`` and
``adresses`` are read only for the departements where they are hosted
(D-23): the statements are captured with their parameters.

``GET /v1/communes?code_insee=`` (H2) serves one referential row, with the
BAN postal code only when the BAN is hosted for its departement.

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
from app.commune.router import router as commune_router
from app.config import settings
from app.database import get_db
from app.scope.coverage import hosting_for_key
from app.scope.model import InstanceScope
from app.zone.router import router as zone_router
from tests.integration.instance_scope_fixture import HOSTABLE_KEYS, install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]

# (code, nom, departement, geo_communes population, lon, lat)
COMMUNES = (
    # Homonyms: one in a hosted departement, one outside the perimeter.
    ("44126", "Le Pin", "44", 700, -1.15, 47.59),
    ("79211", "Le Pin", "79", 1000, -0.63, 46.86),
    # Same name and same population, both hosted, no demographie row: the
    # order falls to the INSEE code.
    ("85901", "Montval", "85", 500, -1.40, 46.70),
    ("44901", "Montval", "44", 500, -1.60, 47.30),
    ("44169", "Saint-Étienne-de-Montluc", "44", 7000, -1.78, 47.28),
    ("44109", "Nantes", "44", 320000, -1.55, 47.22),
    # A hosted commune whose postal code carries a neighbouring prefix (49).
    ("44902", "Bord de Loire", "44", 600, -1.20, 47.35),
)
# (code_insee, departement, annee, population)
DEMOGRAPHIE = (
    ("44126", "44", 2021, 740),
    ("44126", "44", 2022, 750),
    # Outside the hosted departements: never read, never served.
    ("79211", "79", 2022, 99999),
    ("44109", "44", 2022, 323204),
)
# (id, departement, code_commune, code_postal)
ADRESSES = (
    (93000001, "44", "44126", "44540"),
    (93000002, "79", "79211", "79140"),
    (93000003, "44", "44169", "44360"),
    (93000004, "44", "44109", "44000"),
    (93000005, "85", "85901", "85600"),
    (93000006, "44", "44902", "49730"),
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
        for id_, dept, code, code_postal in ADRESSES:
            conn.execute(
                "INSERT INTO adresses (id, code_departement, ban_id, numero, "
                "nom_voie, code_postal, code_commune, nom_commune, position) "
                "VALUES (%s, %s, %s, '1', 'rue du test', %s, %s, 'x', "
                "ST_SetSRID(ST_MakePoint(0, 0), 4326))",
                (id_, dept, f"{code}_0001_00001", code_postal, code),
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


async def _search(api: Api, **params) -> list[dict]:
    response = await api.client.get("/v1/communes/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()["donnees"]


def _departement_lists(api: Api, table: str) -> list[list[str]]:
    """Every list parameter bound to a statement reading *table*."""
    lists = []
    for _sql, params in api.reads(table):
        values = params.values() if isinstance(params, dict) else (params or ())
        lists.extend(list(value) for value in values if isinstance(value, list))
    return lists


async def test_homonyms_stay_two_results_one_out_of_coverage(api, migrated):
    scope = _scope(migrated)

    results = await _search(api, q="le pin")

    assert [r["code_insee"] for r in results] == ["79211", "44126"]
    outside, hosted = results
    # No merge, no dedup by name: department, postal code and INSEE differ.
    assert outside["nom"] == hosted["nom"] == "Le Pin"
    assert (outside["departement"], hosted["departement"]) == ("79", "44")
    assert outside["hebergement"] == {
        "etat": "hors_couverture",
        "motif": "territoire_hors_perimetre",
        "departements": [],
    }
    assert outside["code_postal"] is None
    assert (
        outside["population"],
        outside["population_annee"],
        outside["population_source"],
    ) == (1000, None, "geo_communes")
    assert hosted["hebergement"]["etat"] == "heberge"
    assert hosted["hebergement"]["motif"] is None
    assert hosted["code_postal"] == "44540"
    assert (
        hosted["population"],
        hosted["population_annee"],
        hosted["population_source"],
    ) == (750, 2022, "demographie")
    # Same object /v1/commune would serve for each code.
    for result in results:
        expected = hosting_for_key("commune", scope, result["departement"])
        assert result["hebergement"] == expected.model_dump(mode="json")


async def test_out_of_coverage_hosting_equals_the_commune_route(api, migrated):
    _scope(migrated)
    (outside,) = [r for r in await _search(api, q="le pin") if r["departement"] == "79"]

    response = await api.client.get("/v1/commune", params={"code_insee": "79211"})

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["hebergement"]["commune"] == outside["hebergement"]


async def test_departement_filter_is_exact(api, migrated):
    _scope(migrated)

    assert [
        r["code_insee"] for r in await _search(api, q="le pin", departement="79")
    ] == ["79211"]
    assert [
        r["code_insee"] for r in await _search(api, q="le pin", departement="44")
    ] == ["44126"]
    assert await _search(api, q="le pin", departement="85") == []


@pytest.mark.parametrize("departement", ["00", "99", "ZZ", "200", "4"])
async def test_unknown_departement_is_refused(api, migrated, departement):
    _scope(migrated)
    response = await api.client.get(
        "/v1/communes/search", params={"q": "le pin", "departement": departement}
    )

    assert response.status_code == 422, response.text


async def _search_envelope(api: Api, **params) -> dict:
    response = await api.client.get("/v1/communes/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


HOSTED_BAN = {"etat": "heberge", "motif": None, "departements": ["44", "85"]}
OUTSIDE_BAN = {
    "etat": "hors_couverture",
    "motif": "territoire_hors_perimetre",
    "departements": [],
}


def _bound_to_hosted(api: Api) -> bool:
    """Every read of ``adresses`` is bound to the hosted departements only."""
    lists = _departement_lists(api, "adresses")
    return bool(lists) and all(sorted(lst) == ["44", "85"] for lst in lists)


async def test_postal_code_finds_the_commune_only_where_the_ban_is_hosted(
    api, migrated
):
    """CR-01: a postal search always names the BAN coverage of its territory."""
    _scope(migrated)

    body = await _search_envelope(api, q="44540")
    (found,) = body["donnees"]
    assert (found["code_insee"], found["code_postal"]) == ("44126", "44540")
    assert body["meta"]["hebergement"]["ban"] == HOSTED_BAN

    # The address exists in departement 79, which the BAN does not host here:
    # out of coverage with its reason, never a silent empty list.
    api.statements.clear()
    body = await _search_envelope(api, q="79140")
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["ban"] == OUTSIDE_BAN
    assert _bound_to_hosted(api)

    # No address at all, outside the perimeter: same typed state, bounded read.
    api.statements.clear()
    body = await _search_envelope(api, q="33000")
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["ban"] == OUTSIDE_BAN
    assert _bound_to_hosted(api)

    # No address in a hosted departement: a zero bounded to the served ones.
    body = await _search_envelope(api, q="44999")
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["ban"] == HOSTED_BAN

    # The departement filter names the territory, not the postal prefix.
    body = await _search_envelope(api, q="33000", departement="44")
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["ban"]["etat"] == "heberge"

    # BAN not hosted at all: nothing is read, the reason is served.
    api.statements.clear()
    _scope(migrated, without={"ban"})
    body = await _search_envelope(api, q="44540")
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["ban"]["motif"] == "dataset_non_heberge"
    assert api.reads("adresses") == []


async def test_a_hosted_commune_at_a_neighbouring_postal_code_is_still_served(
    api, migrated
):
    """The postal prefix never skips the bounded read: results win over the meta."""
    _scope(migrated)
    api.statements.clear()

    body = await _search_envelope(api, q="49730")

    (found,) = body["donnees"]
    assert (found["code_insee"], found["code_postal"]) == ("44902", "49730")
    assert found["hebergement"]["etat"] == "heberge"
    # Prefix 49 is not hosted: the meta says so, the hosted commune is still found.
    assert body["meta"]["hebergement"]["ban"] == OUTSIDE_BAN
    assert _bound_to_hosted(api)


@pytest.mark.parametrize(
    "q",
    ["saint etienne de montluc", "SAINT-ÉTIENNE", "saint  etienne-de", "Saint-Etienne"],
)
async def test_matching_ignores_accents_case_hyphens_and_spaces(api, migrated, q):
    _scope(migrated)

    assert [r["code_insee"] for r in await _search(api, q=q)] == ["44169"]


async def test_order_is_total_and_stable(api, migrated):
    _scope(migrated)

    first = await _search(api, q="montval")
    second = await _search(api, q="montval")

    # Same name, same population: the INSEE code breaks the tie.
    assert [r["code_insee"] for r in first] == ["44901", "85901"]
    assert first == second


async def test_served_population_follows_the_hosted_demographie(api, migrated):
    _scope(migrated)
    (nantes,) = await _search(api, q="nantes")
    assert (
        nantes["population"],
        nantes["population_annee"],
        nantes["population_source"],
    ) == (323204, 2022, "demographie")

    api.statements.clear()
    _scope(migrated, without={"demographie"})
    (nantes,) = await _search(api, q="nantes")

    assert (
        nantes["population"],
        nantes["population_annee"],
        nantes["population_source"],
    ) == (320000, None, "geo_communes")
    assert api.reads("demographie") == []
    # Without its structural source the commune is out of coverage.
    assert nantes["hebergement"]["motif"] == "dataset_non_heberge"


async def test_no_read_of_an_unhosted_departement(api, migrated):
    _scope(migrated)
    api.statements.clear()

    results = await _search(api, q="le pin")

    # Every statement reading demographie or adresses is bound to the hosted
    # departements only; the row seeded for 79 is never served.
    for table in ("demographie", "adresses"):
        lists = _departement_lists(api, table)
        assert lists, table
        assert all(sorted(lst) == ["44", "85"] for lst in lists), (table, lists)
    assert 99999 not in [r["population"] for r in results]

    api.statements.clear()
    _scope(migrated, without={"ban"})
    results = await _search(api, q="le pin")

    assert [r["code_postal"] for r in results] == [None, None]
    assert api.reads("adresses") == []


async def test_meta_names_the_unhosted_sources(api, migrated):
    _scope(migrated)
    response = await api.client.get("/v1/communes/search", params={"q": "le pin"})
    assert response.json().get("meta") is None

    _scope(migrated, without={"ban"})
    response = await api.client.get("/v1/communes/search", params={"q": "le pin"})
    hebergement = response.json()["meta"]["hebergement"]
    assert hebergement["ban"]["motif"] == "dataset_non_heberge"
    assert hebergement["demographie"]["etat"] == "heberge"


async def test_commune_referential_by_code_insee(api, migrated):
    _scope(migrated)

    response = await api.client.get("/v1/communes", params={"code_insee": "44126"})
    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]["results"]
    assert (row["code_insee"], row["departement"], row["code_postal"]) == (
        "44126",
        "44",
        "44540",
    )

    api.statements.clear()
    response = await api.client.get("/v1/communes", params={"code_insee": "79211"})
    (row,) = response.json()["donnees"]["results"]
    assert (row["nom"], row["departement"], row["code_postal"]) == (
        "Le Pin",
        "79",
        None,
    )
    assert api.reads("adresses") == []

    response = await api.client.get("/v1/communes", params={"code_insee": "00000"})
    assert response.json()["donnees"]["results"] == []
    assert response.json()["donnees"]["total_count"] == 0

    # The paginated list never reads adresses.
    api.statements.clear()
    response = await api.client.get("/v1/communes", params={"departement": "44"})
    rows = response.json()["donnees"]["results"]
    assert {r["code_postal"] for r in rows} == {None}
    assert api.reads("adresses") == []

    _scope(migrated, without={"ban"})
    api.statements.clear()
    response = await api.client.get("/v1/communes", params={"code_insee": "44126"})
    (row,) = response.json()["donnees"]["results"]
    assert row["code_postal"] is None
    assert api.reads("adresses") == []
