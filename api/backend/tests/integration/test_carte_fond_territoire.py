"""04.10-04 (SEM-01): the base map tiles are relayed for the hosted territory only.

``GET /v1/carte/fond/{z}/{x}/{y}.png`` relays one Plan IGN tile. The route is
not an open relay: a tile is asked to the IGN only when its envelope, widened
by the margin of the shore, touches a departement that hosts the BAN. Proven
here on synthetic rectangles laid on arbitrary tiles, with the real statement
on ``geo_departements`` and the scope read by the real dependency:

- a tile of the hosted territory is served, from one call;
- a tile at sea or in a departement that is not hosted is the same constant
  404, without any call to the IGN (counted by respx);
- the margin reaches a tile off the shore, and no further;
- a kept tile reads the scope only; a failed read is a 503, never a tile.

No network: the IGN is replaced by respx.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_carte_fond_territoire.py
"""

from collections.abc import AsyncIterator, Iterator

import httpx
import psycopg
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.carte import proxy, tuiles
from app.carte.router import carte_router
from app.common.exceptions import register_exception_handlers
from app.config import settings
from app.database import get_db
from app.rate_limit import _bucket_windows, _buckets
from tests.integration.instance_scope_fixture import install_scope_at
from tests.integration.test_adresse_ventes_contract import Api, injected, migrate
from tests.integration.test_carte_parcelles_contract import (
    cadre,
    lectures,
    territoire,
)
from tests.test_carte_proxy import png_uni

pytestmark = pytest.mark.destructive

WMTS = "https://data.geopf.fr/wmts"
GABARIT = "/v1/carte/fond/{z}/{x}/{y}.png"
HEBERGES = ("44", "85")
Z = 16
# Arbitrary tiles: nothing depends on where they are, only on their edges. At
# this latitude a zoom-16 tile is about 0.0055 degree wide and 0.0037 high.
TUILE = (32500, 23000)
# The hosted departement is drawn from half a tile west of TUILE to half a tile
# east of its neighbour: its east edge is 1.5 tile east of the west edge of TUILE.
# Off its east shore, the gap between a tile and the departement is then
# (k - 1.5) tiles for the tile k columns east: 0.0192 degree at k = 5, inside
# the margin of 0.02; 0.0247 at k = 6, outside.
AU_BORD = (TUILE[0] + 3, TUILE[1])
DERNIERE_DE_LA_MARGE = (TUILE[0] + 5, TUILE[1])
PREMIERE_AU_LARGE = (TUILE[0] + 6, TUILE[1])
PLUS_AU_LARGE = (TUILE[0] + 7, TUILE[1])
# North of it, by rows: 4.5 tiles of gap (0.017 degree), then 6.5 (0.024).
AU_NORD_DANS_LA_MARGE = (TUILE[0], TUILE[1] - 6)
AU_NORD_AU_LARGE = (TUILE[0], TUILE[1] - 8)
# Nothing drawn anywhere near.
EN_MER = (TUILE[0] + 200, TUILE[1])
# A departement of the referential that the scope of the test does not host,
# far from the hosted one.
AILLEURS = (TUILE[0], TUILE[1] + 60)
# West half in the hosted departement, east half in the other one; and its
# east neighbour, wholly in the other one but against the hosted one.
MIXTE = (TUILE[0] + 60, TUILE[1] + 60)
VOISINE_NON_HEBERGEE = (MIXTE[0] + 1, MIXTE[1])
IMAGE = png_uni(256, 256)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    migrate(isolated_pg_dsn)
    heberge = territoire(
        cadre(TUILE, -0.5, -0.5, 1.5, 1.5), cadre(MIXTE, -0.5, -0.5, 0.5, 1.5)
    )
    autre = territoire(
        cadre(AILLEURS, -0.5, -0.5, 1.5, 1.5), cadre(MIXTE, 0.5, -0.5, 2.5, 1.5)
    )
    with psycopg.connect(isolated_pg_dsn) as conn:
        for code, nom, wkt in (("85", "Hébergé", heberge), ("49", "Autre", autre)):
            conn.execute(
                "INSERT INTO geo_departements (code, nom, geometry) VALUES "
                "(%s, %s, ST_GeomFromText(%s, 4326))",
                (code, nom, wkt),
            )
    return isolated_pg_dsn


def _vider() -> None:
    tuiles.CACHE.clear()
    tuiles.ECHECS.clear()
    tuiles.EN_COURS.clear()
    proxy.CACHE.clear()
    _buckets.clear()
    _bucket_windows.clear()


@pytest.fixture
async def api(migrated) -> AsyncIterator[Api]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(carte_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    _vider()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield Api(c, statements)
    finally:
        await engine.dispose()
        _vider()


@pytest.fixture
def ign() -> Iterator[respx.Route]:
    """The IGN, replaced: every call that leaves is counted here."""
    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(WMTS).respond(
            200, content=IMAGE, headers={"Content-Type": "image/png"}
        )


def scope(dsn, datasets=("ban",)):
    install_scope_at(dsn, sorted(datasets), HEBERGES)


async def fond(api: Api, ou: tuple[int, int]) -> httpx.Response:
    return await api.client.get(f"/v1/carte/fond/{Z}/{ou[0]}/{ou[1]}.png")


def servie(reponse: httpx.Response) -> None:
    assert reponse.status_code == 200, reponse.text
    assert reponse.headers["content-type"] == "image/png"
    assert reponse.headers["cache-control"] == "public, max-age=86400"
    assert reponse.content == IMAGE


def refusee(reponse: httpx.Response) -> dict:
    assert reponse.status_code == 404, reponse.text
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    assert not reponse.content.startswith(b"\x89PNG")
    corps = reponse.json()
    assert corps["instance"] == GABARIT
    return corps


# --- Hosted territory ---------------------------------------------------------


async def test_tracer_a_tile_of_the_hosted_territory_is_served_from_one_call(
    api, migrated, ign
):
    scope(migrated)

    reponse = await fond(api, TUILE)

    servie(reponse)
    assert ign.call_count == 1
    parametres = dict(ign.calls.last.request.url.params)
    assert (parametres["TILEMATRIX"], parametres["TILECOL"], parametres["TILEROW"]) == (
        str(Z),
        str(TUILE[0]),
        str(TUILE[1]),
    )
    assert len(lectures(api, "geo_departements")) == 1


async def test_a_kept_tile_reads_the_scope_only(api, migrated, ign):
    scope(migrated)
    servie(await fond(api, TUILE))
    api.statements.clear()

    servie(await fond(api, TUILE))

    assert ign.call_count == 1
    assert lectures(api, "geo_departements") == []
    assert len(lectures(api, "instance_scope_datasets")) == 1


async def test_a_tile_astride_a_hosted_departement_is_served(api, migrated, ign):
    scope(migrated)

    servie(await fond(api, MIXTE))

    assert ign.call_count == 1


# --- Outside: one constant 404, no call ---------------------------------------


async def test_a_tile_at_sea_is_refused_without_any_call(api, migrated, ign):
    scope(migrated)

    corps = refusee(await fond(api, EN_MER))

    assert ign.call_count == 0
    assert str(EN_MER[0]) not in str(corps)
    assert len(tuiles.CACHE) == len(tuiles.ECHECS) == 0


async def test_a_tile_of_a_departement_not_hosted_is_the_same_refusal(
    api, migrated, ign
):
    scope(migrated)

    ailleurs = refusee(await fond(api, AILLEURS))
    en_mer = refusee(await fond(api, EN_MER))

    assert ign.call_count == 0
    # Nothing tells a land we do not host from the sea.
    assert ailleurs == en_mer


async def test_without_the_ban_in_the_scope_nothing_is_read_nor_asked(
    api, migrated, ign
):
    scope(migrated, datasets=("cadastre", "dvf"))

    refusee(await fond(api, TUILE))

    assert ign.call_count == 0
    assert lectures(api, "geo_departements") == []


async def test_a_tile_kept_while_hosted_is_refused_once_the_ban_left_the_scope(
    api, migrated, ign
):
    scope(migrated)
    servie(await fond(api, TUILE))
    scope(migrated, datasets=("cadastre",))

    refusee(await fond(api, TUILE))

    assert ign.call_count == 1


# --- The margin of the shore ----------------------------------------------------


async def test_the_margin_reaches_a_tile_off_the_shore_and_no_further(
    api, migrated, ign
):
    scope(migrated)

    servie(await fond(api, AU_BORD))
    servie(await fond(api, DERNIERE_DE_LA_MARGE))
    servie(await fond(api, AU_NORD_DANS_LA_MARGE))
    assert ign.call_count == 3

    refusee(await fond(api, PREMIERE_AU_LARGE))
    refusee(await fond(api, AU_NORD_AU_LARGE))
    assert ign.call_count == 3


async def test_the_margin_is_the_setting(api, migrated, ign, monkeypatch):
    scope(migrated)

    monkeypatch.setattr(settings, "carte_fond_marge_deg", 0.0)
    refusee(await fond(api, AU_BORD))
    # Without margin a tile touching the departement is still served.
    servie(await fond(api, (TUILE[0] + 1, TUILE[1])))
    monkeypatch.setattr(settings, "carte_fond_marge_deg", 0.05)
    servie(await fond(api, PLUS_AU_LARGE))

    assert ign.call_count == 2


async def test_the_margin_serves_a_tile_against_a_hosted_departement(
    api, migrated, ign
):
    scope(migrated)

    # Wholly in a departement that is not hosted, but inside the margin of the
    # hosted one: the rule is the widened envelope, nothing finer.
    servie(await fond(api, VOISINE_NON_HEBERGEE))


# --- A failed read is an outage, never a tile nor a refusal --------------------


async def test_a_failed_read_of_the_referential_is_a_503_without_any_call(
    api, migrated, ign
):
    scope(migrated)

    with injected(
        migrated,
        "ALTER TABLE geo_departements RENAME TO geo_departements_absente",
        restore=("ALTER TABLE geo_departements_absente RENAME TO geo_departements",),
    ):
        panne = await fond(api, TUILE)

    assert panne.status_code == 503, panne.text
    assert panne.headers["content-type"].startswith("application/problem+json")
    assert panne.headers["cache-control"] == "no-store"
    assert not panne.content.startswith(b"\x89PNG")
    assert ign.call_count == 0
    # The outage is not remembered: the next request is served.
    servie(await fond(api, TUILE))
    assert ign.call_count == 1
