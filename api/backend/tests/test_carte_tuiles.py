"""04.10-04: the Plan IGN tiles of the interactive map, relayed by the server.

No network: the IGN is replaced by respx. What is proven here is the privacy
boundary (the outbound request is fixed but for three bounded indices, nothing
of the visitor reaches the IGN), the shape of what is accepted and kept, the
short memory of a failure, the budget toward the upstream and the single call
of concurrent requests.

The second half proves the route, ``GET /v1/carte/fond/{z}/{x}/{y}.png``, with
a session that returns rows (only the SQL is simulated): its own per-visitor
bucket, the bounded path, the hosted territory decided before any call, the
order of the I/O, and three answers that never merge: a refusal (404), a limit
(429) and an outage (503), which is never an image. The statement on the
departements itself is proven on PostGIS
(``tests/integration/test_carte_fond_territoire.py``).
"""

import asyncio
import gzip
import json
import re
from pathlib import Path

import httpx
import pytest
import respx
from cachetools import TTLCache
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError
from starlette.routing import Match

from app.adresse.router import BAN_ID_PATTERN
from app.carte import proxy, router, tuiles
from app.config import settings
from app.database import get_db
from app.main import app
from app.rate_limit import _bucket_windows, _buckets
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from tests.scope_doubles import FULL_SCOPE, SCOPE_44_85
from tests.test_carte_proxy import EN_TETES_VISITEUR, _png_invalides, png_uni
from tests.test_carte_proxy import PNG as IMAGE_448

pytestmark = pytest.mark.unit

WMTS = "https://data.geopf.fr/wmts"
# Tile of the probe of plan 01 (04.10-AMONT-IGN.md, section 4): zoom, column, row.
Z, X, Y = 18, 132784, 90184
COTE = 256
TUILE = png_uni(COTE, COTE)
EN_TETE_PNG = {"Content-Type": "image/png"}


def tuile_de_taille(octets: int) -> bytes:
    """A valid 256 x 256 PNG of exactly *octets* bytes."""
    return png_uni(COTE, COTE, remplissage=octets - len(TUILE) - 12)


def _vider():
    tuiles.CACHE.clear()
    tuiles.ECHECS.clear()
    tuiles.EN_COURS.clear()
    proxy.CACHE.clear()
    proxy.NEGATIF.clear()
    proxy.EN_COURS.clear()
    _buckets.clear()
    _bucket_windows.clear()


@pytest.fixture(autouse=True)
def etat_propre():
    """Empty caches, failure memory and rate buckets around every test."""
    _vider()
    yield
    _vider()


@pytest.fixture()
def ign():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(WMTS).respond(200, content=TUILE, headers=EN_TETE_PNG)


# --- The outbound request ----------------------------------------------------


async def test_the_outbound_request_is_fixed_but_for_the_three_indices(ign):
    corps = await tuiles.telecharger_tuile(Z, X, Y)

    assert corps == TUILE
    requete = ign.calls.last.request
    assert requete.method == "GET"
    assert requete.url.scheme == "https"
    assert requete.url.host == "data.geopf.fr"
    assert requete.url.path == "/wmts"
    assert dict(requete.url.params) == {
        "SERVICE": "WMTS",
        "REQUEST": "GetTile",
        "VERSION": "1.0.0",
        "LAYER": "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2",
        "STYLE": "normal",
        "FORMAT": "image/png",
        "TILEMATRIXSET": "PM_0_19",
        "TILEMATRIX": "18",
        "TILEROW": "90184",
        "TILECOL": "132784",
    }
    assert requete.content == b""


async def test_two_tiles_differ_by_their_indices_only(ign):
    await tuiles.telecharger_tuile(16, 33196, 22546)
    une = dict(ign.calls.last.request.url.params)
    await tuiles.telecharger_tuile(19, 265568, 180368)
    autre = dict(ign.calls.last.request.url.params)

    variables = {"TILEMATRIX", "TILEROW", "TILECOL"}
    assert {k for k in une if une[k] != autre[k]} == variables
    assert (une["TILEMATRIX"], une["TILECOL"], une["TILEROW"]) == (
        "16",
        "33196",
        "22546",
    )


async def test_only_the_fixed_headers_are_sent(ign):
    await tuiles.telecharger_tuile(Z, X, Y)

    envoyees = {
        nom.lower(): valeur for nom, valeur in ign.calls.last.request.headers.items()
    }
    assert envoyees["user-agent"] == "DataCommune (+https://datacommune.fr)"
    assert envoyees["accept"] == "image/png"
    # A compressed answer would be inflated in memory before any size check.
    assert envoyees["accept-encoding"] == "identity"
    assert envoyees["host"] == "data.geopf.fr"
    assert set(envoyees) == {
        "user-agent",
        "accept",
        "host",
        "accept-encoding",
        "connection",
    }


async def test_the_wmts_base_is_a_setting(monkeypatch):
    monkeypatch.setattr(settings, "carte_wmts_url", "https://wmts.example.test/x")
    with respx.mock(assert_all_called=False) as mock:
        autre = mock.get("https://wmts.example.test/x").respond(
            200, content=TUILE, headers=EN_TETE_PNG
        )
        corps = await tuiles.telecharger_tuile(Z, X, Y)

    assert corps == TUILE
    assert autre.call_count == 1


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [
        (15, 16598, 11273),
        (20, 531136, 360736),
        (16, 65536, 22546),
        (16, 33196, 65536),
        (19, 524288, 180368),
        (16, -1, 22546),
        (16, 33196, -1),
        (0, 0, 0),
    ],
)
async def test_a_tile_outside_the_grid_is_never_asked(ign, z, x, y):
    with pytest.raises(ValueError, match="grille"):
        await tuiles.telecharger_tuile(z, x, y)
    with pytest.raises(ValueError, match="grille"):
        await tuiles.obtenir_tuile(z, x, y)

    assert ign.call_count == 0
    assert len(tuiles.CACHE) == len(tuiles.ECHECS) == 0


@pytest.mark.parametrize(
    ("z", "x", "y"), [(16, 0, 0), (16, 65535, 65535), (19, 524287, 0)]
)
async def test_the_edges_of_the_grid_are_asked(ign, z, x, y):
    assert await tuiles.telecharger_tuile(z, x, y) == TUILE


def test_the_zoom_bounds_are_those_of_the_map_routes():
    assert (tuiles.ZOOM_MIN, tuiles.ZOOM_MAX) == (router.ZOOM_MIN, router.ZOOM_MAX)
    assert (tuiles.ZOOM_MIN, tuiles.ZOOM_MAX) == (16, 19)


# --- Acceptance of the IGN answer --------------------------------------------


def _reponses_refusees():
    def png(contenu: bytes, **en_tetes: str) -> httpx.Response:
        return httpx.Response(200, content=contenu, headers={**EN_TETE_PNG, **en_tetes})

    limite = type(settings).model_fields["carte_fond_tuile_max_octets"].default
    cas = {
        "status-500": httpx.Response(500, content=TUILE, headers=EN_TETE_PNG),
        "status-404": httpx.Response(404, content=TUILE, headers=EN_TETE_PNG),
        "status-204": httpx.Response(204, headers=EN_TETE_PNG),
        "redirect": httpx.Response(
            302, headers={"Location": "https://evil.example/x.png"}
        ),
        "content-type-html": httpx.Response(
            200, content=TUILE, headers={"Content-Type": "text/html"}
        ),
        "content-type-missing": httpx.Response(200, content=TUILE),
        "body-without-png-signature": png(b"<ExceptionReport/>" * 10),
        "body-over-the-limit": png(tuile_de_taille(limite + 1)),
        "empty-body": png(b""),
        "gzip-encoded": png(gzip.compress(TUILE), **{"Content-Encoding": "gzip"}),
        # A complete PNG, but of the static map: not a tile.
        "png-of-448": png(IMAGE_448),
    }
    for nom in ("idat-crc-wrong", "width-255", "cut-33", "unknown-row-filter"):
        cas[f"malformed-{nom}"] = png(_png_invalides(COTE)[nom])
    return [pytest.param(reponse, id=nom) for nom, reponse in cas.items()]


@pytest.mark.parametrize("refusee", _reponses_refusees())
async def test_an_unacceptable_answer_is_an_upstream_failure_never_kept(refusee):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).mock(return_value=refusee)
        ailleurs = mock.get("https://evil.example/x.png").respond(
            200, content=TUILE, headers=EN_TETE_PNG
        )
        with pytest.raises(proxy.CarteAmontIndisponibleError):
            await tuiles.obtenir_tuile(Z, X, Y)

    assert route.call_count == 1
    # A redirect is an answer, not something to follow.
    assert ailleurs.call_count == 0
    assert len(tuiles.CACHE) == 0
    assert len(proxy.CACHE) == 0


@pytest.mark.parametrize(
    "erreur",
    [
        httpx.ReadTimeout("lent"),
        httpx.ConnectTimeout("lent"),
        httpx.ConnectError("coupe"),
        httpx.RemoteProtocolError("casse"),
    ],
)
async def test_a_network_error_or_timeout_is_an_upstream_failure(erreur):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMTS).mock(side_effect=erreur)
        with pytest.raises(proxy.CarteAmontIndisponibleError):
            await tuiles.obtenir_tuile(Z, X, Y)

    assert len(tuiles.CACHE) == 0


async def test_a_tile_of_exactly_the_limit_is_accepted_and_one_byte_more_is_not():
    limite = settings.carte_fond_tuile_max_octets
    juste, trop = tuile_de_taille(limite), tuile_de_taille(limite + 1)
    assert (len(juste), len(trop)) == (limite, limite + 1)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMTS).respond(200, content=juste, headers=EN_TETE_PNG)
        assert await tuiles.telecharger_tuile(Z, X, Y) == juste
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMTS).respond(200, content=trop, headers=EN_TETE_PNG)
        with pytest.raises(proxy.CarteAmontIndisponibleError):
            await tuiles.telecharger_tuile(Z, X, Y)


async def test_the_size_is_checked_before_a_chunk_is_appended(monkeypatch):
    class EspionOctets(bytearray):
        plus_grand = 0

        def __iadd__(self, autre):
            EspionOctets.plus_grand = max(
                EspionOctets.plus_grand, len(self) + len(autre)
            )
            return super().__iadd__(autre)

    async def morceaux():
        yield b"a" * 60
        yield b"b" * 60

    # Wherever the bytes are accumulated, the relay or the shared reader.
    monkeypatch.setattr(proxy, "bytearray", EspionOctets, raising=False)
    monkeypatch.setattr(tuiles, "bytearray", EspionOctets, raising=False)
    monkeypatch.setattr(settings, "carte_fond_tuile_max_octets", 100)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMTS).respond(200, content=morceaux(), headers=EN_TETE_PNG)
        with pytest.raises(proxy.CarteAmontIndisponibleError):
            await tuiles.telecharger_tuile(Z, X, Y)

    assert 0 < EspionOctets.plus_grand <= 100


async def test_the_tile_is_read_by_a_fresh_client_short_and_without_redirect(
    ign, monkeypatch
):
    clients = []
    vrai_client = httpx.AsyncClient

    def espion(**reglages):
        clients.append(reglages)
        return vrai_client(**reglages)

    monkeypatch.setattr(httpx, "AsyncClient", espion)

    await tuiles.telecharger_tuile(Z, X, Y)
    await tuiles.telecharger_tuile(Z, X + 1, Y)

    # One client per call: nothing travels from one request to the next.
    assert len(clients) == 2
    for reglages in clients:
        assert reglages["follow_redirects"] is False
        assert reglages["timeout"].read <= 5.0
        assert reglages["timeout"].connect <= 3.0


# --- The tile cache -----------------------------------------------------------


async def test_a_read_tile_is_kept_and_not_asked_again(ign):
    premiere = await tuiles.obtenir_tuile(Z, X, Y)
    seconde = await tuiles.obtenir_tuile(Z, X, Y)

    assert premiere == seconde == TUILE
    assert ign.call_count == 1


async def test_the_cache_key_is_the_three_indices(ign):
    await tuiles.obtenir_tuile(Z, X, Y)
    await tuiles.obtenir_tuile(Z, X + 1, Y)
    await tuiles.obtenir_tuile(Z, X, Y + 1)
    await tuiles.obtenir_tuile(Z - 1, X // 2, Y // 2)
    await tuiles.obtenir_tuile(Z, X, Y)

    assert ign.call_count == 4
    assert set(tuiles.CACHE) == {
        (Z, X, Y),
        (Z, X + 1, Y),
        (Z, X, Y + 1),
        (Z - 1, X // 2, Y // 2),
    }


def test_the_tile_cache_is_its_own_bounded_in_bytes_for_the_time_of_the_images():
    assert isinstance(tuiles.CACHE, TTLCache)
    assert tuiles.CACHE is not proxy.CACHE
    assert tuiles.CACHE.maxsize == 64 * 1024 * 1024
    assert tuiles.CACHE.ttl == proxy.CACHE.ttl == 1_814_400
    tuiles.CACHE[(Z, X, Y)] = b"x" * 1000
    assert tuiles.CACHE.currsize == 1000
    assert proxy.CACHE.currsize == 0


async def test_scrolling_tiles_never_evicts_a_static_image(ign, monkeypatch):
    petit = proxy.nouveau_cache(octets=len(TUILE) + 10, ttl_secondes=60)
    monkeypatch.setattr(tuiles, "CACHE", petit)
    proxy.CACHE[("44109_9999_00004", "plan")] = IMAGE_448

    for colonne in range(3):
        await tuiles.obtenir_tuile(Z, X + colonne, Y)
    await tuiles.obtenir_tuile(Z, X, Y)

    # The first tile was evicted by its own budget and asked again.
    assert ign.call_count == 4
    assert list(petit) == [(Z, X, Y)]
    assert proxy.CACHE[("44109_9999_00004", "plan")] == IMAGE_448


# --- The short memory of a failure --------------------------------------------


@pytest.fixture()
def horloge(monkeypatch):
    """The failure memory on a clock the test moves."""
    temps = [1000.0]
    monkeypatch.setattr(
        tuiles,
        "ECHECS",
        TTLCache(
            maxsize=tuiles.ECHECS.maxsize,
            ttl=tuiles.ECHECS.ttl,
            timer=lambda: temps[0],
        ),
    )
    return temps


async def test_a_failure_is_remembered_briefly_then_the_ign_is_asked_again(horloge):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(200, content=TUILE, headers=EN_TETE_PNG),
            ]
        )
        with pytest.raises(proxy.CarteAmontIndisponibleError):
            await tuiles.obtenir_tuile(Z, X, Y)
        # Within the memory: the same failure, without a call.
        horloge[0] += settings.carte_fond_echec_ttl_secondes - 1
        with pytest.raises(proxy.CarteAmontIndisponibleError):
            await tuiles.obtenir_tuile(Z, X, Y)
        assert route.call_count == 1
        assert len(tuiles.CACHE) == 0
        # Another tile is not concerned.
        assert (Z, X + 1, Y) not in tuiles.ECHECS

        horloge[0] += 2
        apres = await tuiles.obtenir_tuile(Z, X, Y)

    assert apres == TUILE
    assert route.call_count == 2
    assert (Z, X, Y) in tuiles.CACHE


def test_the_failure_memory_is_short_and_bounded():
    assert isinstance(tuiles.ECHECS, TTLCache)
    assert tuiles.ECHECS.ttl == settings.carte_fond_echec_ttl_secondes
    # An outage must not outlive itself by much: far below the 404 memory.
    assert 0 < tuiles.ECHECS.ttl <= 60 < proxy.NEGATIF.ttl
    assert tuiles.ECHECS.maxsize == settings.carte_cache_negatif_entrees == 10_000


async def test_the_failure_memory_evicts_beyond_its_bound(monkeypatch):
    monkeypatch.setattr(tuiles, "ECHECS", TTLCache(maxsize=2, ttl=30))
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).respond(500)
        for colonne in range(3):
            with pytest.raises(proxy.CarteAmontIndisponibleError):
                await tuiles.obtenir_tuile(Z, X + colonne, Y)
        with pytest.raises(proxy.CarteAmontIndisponibleError):
            await tuiles.obtenir_tuile(Z, X, Y)

    assert route.call_count == 4
    assert len(tuiles.ECHECS) == 2


# --- Budget toward the upstream ------------------------------------------------


async def test_the_upstream_budget_counts_the_calls_made_only(ign, monkeypatch):
    monkeypatch.setattr(settings, "carte_fond_rate_limit_amont", 2)

    await tuiles.obtenir_tuile(Z, X, Y)
    await tuiles.obtenir_tuile(Z, X + 1, Y)
    # A kept tile costs no token.
    await tuiles.obtenir_tuile(Z, X, Y)
    with pytest.raises(tuiles.BudgetAmontEpuiseError) as refus:
        await tuiles.obtenir_tuile(Z, X + 2, Y)

    assert refus.value.reset > 0
    assert ign.call_count == 2
    # What is kept is still served while the IGN is protected.
    assert await tuiles.obtenir_tuile(Z, X + 1, Y) == TUILE
    # A spent budget is not an outage of the tile: nothing is remembered.
    assert len(tuiles.ECHECS) == 0
    assert len(_buckets["carte:fond:amont"]) == 2


async def test_the_upstream_budget_is_apart_from_the_one_of_the_static_map(
    ign, monkeypatch
):
    # Were the budget of the images spent here, the first tile would stop.
    monkeypatch.setattr(settings, "carte_rate_limit_amont", 0)

    assert await tuiles.obtenir_tuile(Z, X, Y) == TUILE
    assert "carte:amont" not in _buckets
    assert tuiles.AMONT_BUCKET == "carte:fond:amont"


async def test_a_remembered_failure_costs_no_token(monkeypatch):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMTS).respond(500)
        for _ in range(3):
            with pytest.raises(proxy.CarteAmontIndisponibleError):
                await tuiles.obtenir_tuile(Z, X, Y)

    assert len(_buckets["carte:fond:amont"]) == 1


def test_the_settings_of_the_base_map_are_the_planned_ones():
    defauts = {nom: champ.default for nom, champ in type(settings).model_fields.items()}

    # URL_SERVICE of 04.10-AMONT-IGN.md.
    assert defauts["carte_wmts_url"] == "https://data.geopf.fr/wmts"
    assert defauts["carte_fond_rate_limit_per_ip"] == 600
    # LIMITE_PAR_SECONDE=non_publiee: the fallback of the plan, per minute.
    assert defauts["carte_fond_rate_limit_amont"] == 1200
    assert defauts["carte_fond_cache_octets"] == 64 * 1024 * 1024
    assert defauts["carte_fond_marge_deg"] == 0.02
    assert defauts["carte_fond_echec_ttl_secondes"] == 30
    # Above an uncompressed 256 x 256 RGBA image, far above the probed tile.
    assert 256 * 256 * 4 < defauts["carte_fond_tuile_max_octets"] <= 1024 * 1024
    assert defauts["carte_fond_tuile_max_octets"] > 20 * 24_272
    # The settings of the static map did not move.
    assert defauts["carte_wms_url"] == "https://data.geopf.fr/wms-r/wms"
    assert defauts["carte_rate_limit_per_ip"] == 60
    assert defauts["carte_rate_limit_amont"] == 600
    assert defauts["carte_cache_octets"] == 64 * 1024 * 1024
    assert defauts["carte_image_max_octets"] == 2 * 1024 * 1024


# --- Concurrent requests --------------------------------------------------------


async def test_concurrent_requests_for_one_tile_make_one_call():
    async def lent(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=TUILE, headers=EN_TETE_PNG)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).mock(side_effect=lent)
        corps = await asyncio.gather(
            tuiles.obtenir_tuile(Z, X, Y),
            tuiles.obtenir_tuile(Z, X, Y),
            tuiles.obtenir_tuile(Z, X, Y),
        )

    assert corps == [TUILE, TUILE, TUILE]
    assert route.call_count == 1
    assert len(_buckets["carte:fond:amont"]) == 1
    assert tuiles.EN_COURS == {}


async def test_a_failed_shared_call_reaches_every_waiter_and_is_dropped():
    async def lent(request):
        await asyncio.sleep(0.05)
        raise httpx.ConnectError("coupe")

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).mock(side_effect=lent)
        issues = await asyncio.gather(
            tuiles.obtenir_tuile(Z, X, Y),
            tuiles.obtenir_tuile(Z, X, Y),
            return_exceptions=True,
        )

    assert [type(issue) for issue in issues] == [proxy.CarteAmontIndisponibleError] * 2
    assert route.call_count == 1
    assert tuiles.EN_COURS == {}
    assert (Z, X, Y) in tuiles.ECHECS


# =============================================================================
# The route: GET /v1/carte/fond/{z}/{x}/{y}.png
# =============================================================================

GABARIT = "/v1/carte/fond/{z}/{x}/{y}.png"
GABARIT_IMAGE = "/v1/carte/{ban_id}/{couche}.png"
GABARIT_PARCELLES = "/v1/carte/parcelles/{z}/{x}/{y}"
TITRE_VALIDATION = "Erreur de validation des donnees"
SANS_BAN = InstanceScope(
    datasets=frozenset({"cadastre", "dvf"}), departements=("44", "85")
)
REFUS = {
    "type": "about:blank",
    "title": "Tuile hors du territoire servi",
    "status": 404,
    "instance": GABARIT,
}
PANNE = {
    "type": "about:blank",
    "title": "Carte momentanement indisponible",
    "status": 503,
    "instance": GABARIT,
}


def _url(z: int = Z, x: int = X, y: int = Y) -> str:
    return f"/v1/carte/fond/{z}/{x}/{y}.png"


class _Codes:
    """Result of the territory statement: the codes of the departements."""

    def __init__(self, codes: list[str]) -> None:
        self._codes = codes

    def scalars(self):
        return self

    def all(self) -> list[str]:
        return list(self._codes)


class SessionFactice:
    """Only the SQL is simulated: ``execute`` returns *codes*; calls are journaled."""

    def __init__(self, journal: list[str], codes: list[str]) -> None:
        self.journal = journal
        self.codes = codes
        self.parametres: list[dict] = []
        self.panne = False

    async def execute(self, statement, params=None):
        self.journal.append("lecture")
        self.parametres.append(dict(params or {}))
        if self.panne:
            raise OperationalError("SELECT", {}, Exception("coupe"))
        return _Codes(self.codes)

    async def rollback(self):
        self.journal.append("rollback")


@pytest.fixture()
def journal() -> list[str]:
    return []


@pytest.fixture()
def session(journal) -> SessionFactice:
    return SessionFactice(journal, ["85"])


@pytest.fixture()
def perimetre() -> list[InstanceScope]:
    """The scope the route reads; a test replaces the element to change it."""
    return [FULL_SCOPE]


@pytest.fixture()
async def client(session, perimetre):
    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_instance_scope] = lambda: perimetre[0]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def amont(journal):
    """The IGN, replaced; every call that leaves is journaled."""

    def _repond(request):
        journal.append("ign")
        return httpx.Response(200, content=TUILE, headers=EN_TETE_PNG)

    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(WMTS).mock(side_effect=_repond)


def _routes_de(chemin: str) -> list[str]:
    """Templates of the API routes that fully match *chemin*."""
    scope = {"type": "http", "path": chemin, "method": "GET"}
    return [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.matches(scope)[0] == Match.FULL
    ]


def _appels(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _appels(dep)


# --- The tile is served, built by the server alone ----------------------------


async def test_the_route_serves_the_tile_with_its_headers(client, amont):
    reponse = await client.get(_url())

    assert reponse.status_code == 200
    assert reponse.content == TUILE
    assert reponse.headers["content-type"] == "image/png"
    assert reponse.headers["cache-control"] == "public, max-age=86400"
    assert reponse.headers["x-content-type-options"] == "nosniff"
    assert amont.call_count == 1
    parametres = dict(amont.calls.last.request.url.params)
    assert (parametres["TILEMATRIX"], parametres["TILECOL"], parametres["TILEROW"]) == (
        "18",
        "132784",
        "90184",
    )
    assert parametres["LAYER"] == "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"


async def test_no_header_of_the_visitor_reaches_the_ign(client, amont):
    reponse = await client.get(_url(), headers=EN_TETES_VISITEUR)

    assert reponse.status_code == 200
    requete = amont.calls.last.request
    envoyees = {nom.lower(): valeur for nom, valeur in requete.headers.items()}
    assert envoyees["user-agent"] == "DataCommune (+https://datacommune.fr)"
    assert set(envoyees) == {
        "user-agent",
        "accept",
        "host",
        "accept-encoding",
        "connection",
    }
    # No value of the visitor anywhere in the outbound request, URL included.
    tout = str(requete.url) + "".join(envoyees.values())
    for valeur in EN_TETES_VISITEUR.values():
        assert valeur not in tout
    assert "203.0.113" not in tout
    assert requete.content == b""


async def test_the_query_string_of_the_visitor_is_ignored(client, amont):
    sans = await client.get(_url())
    attendue = amont.calls.last.request.url
    tuiles.CACHE.clear()

    avec = await client.get(
        _url() + "?LAYER=OTHER&TILEMATRIX=3&TILEMATRIXSET=LAMB93&STYLE=x"
        "&url=http://evil.example&host=evil.example&z=3&x=1&y=1"
    )

    assert sans.status_code == avec.status_code == 200
    assert amont.calls.last.request.url == attendue
    assert amont.calls.last.request.url.host == "data.geopf.fr"


# --- Bounded path, nothing of the input reflected ------------------------------


@pytest.mark.parametrize(
    ("chemin", "saisie"),
    [
        ("15/16250/11500", "16250"),
        ("20/524287/524286", "524287"),
        ("16/65536/23000", "65536"),
        ("16/32500/65536", "65536"),
        ("17/131072/46000", "131072"),
        ("19/524288/46000", "524288"),
        ("16/-7654/23000", "7654"),
        ("16/zzqqxx/23000", "zzqqxx"),
        ("16/32500/23000.75", "23000.75"),
        ("16/98765432109876543210/23000", "98765432109876543210"),
    ],
)
async def test_a_tile_out_of_bounds_is_a_constant_problem_without_any_call(
    client, amont, journal, chemin, saisie
):
    reponse = await client.get(f"/v1/carte/fond/{chemin}.png")

    assert reponse.status_code == 422
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    corps = reponse.json()
    assert corps["title"] == TITRE_VALIDATION
    assert corps["instance"] == GABARIT
    assert saisie not in reponse.text
    assert amont.call_count == 0
    assert journal == []


@pytest.mark.parametrize(
    "chemin",
    [
        "18/132784/90184",
        "18/132784/90184.jpg",
        "18/132784/90184.PNG",
        "18/132784/90184.png.exe",
        "18/132784/90184.png/suite",
        "18/132784.png",
    ],
)
async def test_another_suffix_or_shape_is_no_route(client, amont, journal, chemin):
    reponse = await client.get(f"/v1/carte/fond/{chemin}")

    assert reponse.status_code == 404
    assert not reponse.content.startswith(b"\x89PNG")
    assert "132784" not in reponse.text
    assert amont.call_count == 0
    assert journal == []
    assert _routes_de(f"/v1/carte/fond/{chemin}") == []


@pytest.mark.parametrize("chemin", ["16/65535/65535", "19/524287/0", "16/0/0"])
async def test_the_edges_of_the_grid_are_served(client, amont, chemin):
    assert (await client.get(f"/v1/carte/fond/{chemin}.png")).status_code == 200


def test_the_base_map_path_matches_its_route_only():
    assert _routes_de("/v1/carte/fond/16/1/2.png") == [GABARIT]
    assert _routes_de("/v1/carte/fond/plan.png") == [GABARIT_IMAGE]
    assert _routes_de("/v1/carte/parcelles/16/1/2") == [GABARIT_PARCELLES]
    # The image route answers ``fond`` as a key it refuses: never a tile.
    assert re.match(BAN_ID_PATTERN, "fond") is None


async def test_a_tile_request_never_reads_an_address(client, amont, monkeypatch):
    from unittest.mock import AsyncMock

    point = AsyncMock()
    monkeypatch.setattr(router, "point_de_la_cle", point)

    reponse = await client.get(_url())

    assert reponse.status_code == 200
    point.assert_not_awaited()
    assert len(proxy.CACHE) == 0


# --- Own bucket of the visitor --------------------------------------------------


async def test_the_visitor_bucket_is_its_own_and_answers_429_with_retry_after(
    client, amont, monkeypatch
):
    monkeypatch.setattr(settings, "carte_fond_rate_limit_per_ip", 2)
    # The other buckets are smaller: were one spent here, the second call would stop.
    monkeypatch.setattr(settings, "carte_rate_limit_per_ip", 1)
    monkeypatch.setattr(settings, "carte_parcelles_rate_limit_per_ip", 1)

    statuts = [(await client.get(_url())).status_code for _ in range(2)]
    refusee = await client.get(_url())

    assert statuts == [200, 200]
    assert refusee.status_code == 429
    assert re.fullmatch(r"[1-9][0-9]*", refusee.headers["retry-after"])
    assert refusee.headers["cache-control"] == "no-store"
    assert refusee.headers["content-type"].startswith("application/problem+json")
    assert not refusee.content.startswith(b"\x89PNG")
    assert any(cle.startswith("carte:fond:ip:") for cle in _buckets)
    assert not any(cle.startswith("carte:ip:") for cle in _buckets)
    assert not any(cle.startswith("carte:parcelles:ip:") for cle in _buckets)


async def test_the_visitor_bucket_is_per_visitor_and_per_64(client, amont, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "carte_fond_rate_limit_per_ip", 1)

    def depuis(adresse: str) -> dict[str, str]:
        return {"CF-Connecting-IP": adresse}

    assert (await client.get(_url(), headers=depuis("203.0.113.10"))).status_code == 200
    assert (await client.get(_url(), headers=depuis("203.0.113.10"))).status_code == 429
    assert (await client.get(_url(), headers=depuis("203.0.113.11"))).status_code == 200
    assert (
        await client.get(_url(), headers=depuis("2001:db8:1:2::1"))
    ).status_code == 200
    assert (
        await client.get(_url(), headers=depuis("2001:db8:1:2:aaaa::9"))
    ).status_code == 429
    assert "carte:fond:ip:2001:db8:1:2::/64" in _buckets


async def test_the_visitor_bucket_is_spent_before_any_read(
    client, amont, journal, monkeypatch
):
    monkeypatch.setattr(settings, "carte_fond_rate_limit_per_ip", 1)
    await client.get(_url())
    journal.clear()

    refusee = await client.get(_url(x=X + 1))

    assert refusee.status_code == 429
    assert journal == []
    assert amont.call_count == 1


async def test_the_tiles_do_not_spend_the_navigation_ceiling(
    client, amont, monkeypatch
):
    monkeypatch.setattr(settings, "rate_limit_per_ip", 2)

    statuts = [(await client.get(_url(x=X + i))).status_code for i in range(5)]

    assert statuts == [200] * 5
    assert not any(cle.startswith("ip:") for cle in _buckets)


def test_the_route_has_no_api_key_and_no_quota_dependency():
    from app.auth.dependencies import check_quota_hybrid

    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == GABARIT)
    appels = set(_appels(route.dependant))

    assert check_quota_hybrid not in appels
    assert router.limite_fond in appels
    assert router.limite_carte not in appels
    assert router.limite_parcelles not in appels
    assert "security" not in app.openapi()["paths"][GABARIT]["get"]


# --- Budget toward the upstream --------------------------------------------------


async def test_a_spent_upstream_budget_is_a_429_and_kept_tiles_are_still_served(
    client, amont, monkeypatch
):
    monkeypatch.setattr(settings, "carte_fond_rate_limit_amont", 2)

    assert (await client.get(_url(x=X))).status_code == 200
    assert (await client.get(_url(x=X + 1))).status_code == 200
    assert (await client.get(_url(x=X))).status_code == 200  # kept: no token
    refusee = await client.get(_url(x=X + 2))

    assert refusee.status_code == 429
    assert int(refusee.headers["retry-after"]) > 0
    assert refusee.headers["cache-control"] == "no-store"
    assert refusee.headers["content-type"].startswith("application/problem+json")
    assert amont.call_count == 2
    assert (await client.get(_url(x=X + 1))).status_code == 200
    # A limit is not an outage: the tile is asked as soon as the budget is back.
    assert len(tuiles.ECHECS) == 0
    assert "carte:fond:amont" in _buckets
    assert "carte:amont" not in _buckets


async def test_the_upstream_budget_is_global_across_visitors(
    client, amont, monkeypatch
):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "carte_fond_rate_limit_amont", 1)

    premiere = await client.get(_url(x=X), headers={"CF-Connecting-IP": "203.0.113.20"})
    seconde = await client.get(
        _url(x=X + 1), headers={"CF-Connecting-IP": "203.0.113.21"}
    )

    assert (premiere.status_code, seconde.status_code) == (200, 429)
    assert amont.call_count == 1


# --- Hosted territory, decided before any call -----------------------------------


async def test_the_territory_is_read_for_the_tile_and_its_margin(
    client, amont, session, monkeypatch
):
    monkeypatch.setattr(settings, "carte_fond_marge_deg", 0.035)

    await client.get(_url())

    assert session.parametres == [{"z": Z, "x": X, "y": Y, "marge": 0.035}]


async def test_a_tile_touching_no_departement_is_a_constant_404_without_any_call(
    client, amont, session
):
    session.codes = []

    reponse = await client.get(_url())

    assert reponse.status_code == 404
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    assert reponse.json() == REFUS
    assert amont.call_count == 0
    assert len(tuiles.CACHE) == len(tuiles.ECHECS) == 0
    assert "carte:fond:amont" not in _buckets


async def test_a_departement_that_does_not_host_the_ban_is_the_same_404(
    client, amont, session, perimetre
):
    perimetre[0] = SCOPE_44_85
    session.codes = ["49"]

    reponse = await client.get(_url())

    assert reponse.status_code == 404
    assert reponse.json() == REFUS
    assert amont.call_count == 0


async def test_one_hosted_departement_among_those_touched_is_enough(
    client, amont, session, perimetre
):
    perimetre[0] = SCOPE_44_85
    session.codes = ["49", "85"]

    assert (await client.get(_url())).status_code == 200
    assert amont.call_count == 1


async def test_without_the_ban_in_the_scope_nothing_is_read_nor_asked(
    client, amont, journal, perimetre
):
    perimetre[0] = SANS_BAN

    reponse = await client.get(_url())

    assert reponse.status_code == 404
    assert reponse.json() == REFUS
    assert journal == []
    assert amont.call_count == 0


async def test_a_kept_tile_is_refused_once_the_ban_left_the_scope(
    client, amont, journal, perimetre
):
    assert (await client.get(_url())).status_code == 200
    perimetre[0] = SANS_BAN

    assert (await client.get(_url())).status_code == 404
    assert amont.call_count == 1


async def test_a_failed_read_of_the_departements_is_a_503_without_any_call(
    client, amont, session
):
    session.panne = True

    panne = await client.get(_url())
    session.panne = False
    ensuite = await client.get(_url())

    assert panne.status_code == 503
    assert panne.headers["content-type"].startswith("application/problem+json")
    assert panne.headers["cache-control"] == "no-store"
    assert panne.json() == PANNE
    # The read failed: nothing was asked, nothing is remembered.
    assert ensuite.status_code == 200
    assert amont.call_count == 1


# --- Order of the I/O -------------------------------------------------------------


async def test_the_connection_is_given_back_before_the_ign_is_asked(
    client, amont, journal
):
    await client.get(_url())

    assert journal == ["lecture", "rollback", "ign"]


async def test_a_kept_tile_reads_nothing(client, amont, journal):
    await client.get(_url())
    journal.clear()

    reponse = await client.get(_url())

    assert reponse.status_code == 200
    assert reponse.content == TUILE
    assert journal == []
    assert amont.call_count == 1


async def test_concurrent_requests_give_their_connection_back_before_waiting(
    client, journal
):
    async def lent(request):
        await asyncio.sleep(0.05)
        journal.append("ign")
        return httpx.Response(200, content=TUILE, headers=EN_TETE_PNG)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).mock(side_effect=lent)
        reponses = await asyncio.gather(client.get(_url()), client.get(_url()))

    assert [r.status_code for r in reponses] == [200, 200]
    assert route.call_count == 1
    # Each request read the territory and released before the one call ended.
    assert journal.count("rollback") == 2
    assert journal[-1] == "ign"
    assert len(_buckets["carte:fond:amont"]) == 1
    assert tuiles.EN_COURS == {}


# --- An outage is a 503, never an image -------------------------------------------


@pytest.mark.parametrize(
    "refusee",
    [
        pytest.param(httpx.Response(500), id="status-500"),
        pytest.param(
            httpx.Response(302, headers={"Location": "https://evil.example/x.png"}),
            id="redirect",
        ),
        pytest.param(
            httpx.Response(200, content=IMAGE_448, headers=EN_TETE_PNG),
            id="png-of-448",
        ),
        pytest.param(
            httpx.Response(200, content=b"", headers=EN_TETE_PNG), id="empty-body"
        ),
        pytest.param(
            httpx.Response(200, content=TUILE[:-20], headers=EN_TETE_PNG),
            id="cut-png",
        ),
    ],
)
async def test_an_upstream_failure_is_a_503_and_never_an_image(client, refusee):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).mock(return_value=refusee)
        premiere = await client.get(_url())
        seconde = await client.get(_url())

    for reponse in (premiere, seconde):
        assert reponse.status_code == 503
        assert reponse.headers["content-type"].startswith("application/problem+json")
        assert reponse.headers["cache-control"] == "no-store"
        assert reponse.json() == PANNE
        assert not reponse.content.startswith(b"\x89PNG")
    # The second answer came from the short memory of the failure.
    assert route.call_count == 1
    assert len(tuiles.CACHE) == 0


async def test_a_timeout_is_a_503_then_the_tile_comes_back(client, horloge):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMTS).mock(
            side_effect=[
                httpx.ReadTimeout("lent"),
                httpx.Response(200, content=TUILE, headers=EN_TETE_PNG),
            ]
        )
        premiere = await client.get(_url())
        horloge[0] += settings.carte_fond_echec_ttl_secondes + 1
        seconde = await client.get(_url())

    assert premiere.status_code == 503
    assert premiere.json() == PANNE
    assert seconde.status_code == 200
    assert seconde.content == TUILE
    assert route.call_count == 2


async def test_refusal_limit_and_outage_are_three_distinct_answers(
    client, session, monkeypatch
):
    monkeypatch.setattr(settings, "carte_fond_rate_limit_amont", 1)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMTS).respond(500)
        panne = await client.get(_url(x=X))
        limite = await client.get(_url(x=X + 1))
        session.codes = []
        refus = await client.get(_url(x=X + 2))

    assert (panne.status_code, limite.status_code, refus.status_code) == (503, 429, 404)
    titres = {r.json()["title"] for r in (panne, limite, refus)}
    assert len(titres) == 3
    assert "retry-after" in limite.headers
    assert "retry-after" not in panne.headers and "retry-after" not in refus.headers


# --- OpenAPI ------------------------------------------------------------------------


def test_the_route_is_public_and_documented_in_ascii():
    operation = app.openapi()["paths"][GABARIT]["get"]

    assert set(operation["responses"]) >= {"200", "404", "422", "429", "503"}
    assert "image/png" in operation["responses"]["200"]["content"]
    assert operation["summary"].isascii() and operation["description"].isascii()
    assert "security" not in operation
    assert {p["name"] for p in operation["parameters"]} == {"z", "x", "y"}
    bornes = {p["name"]: p["schema"] for p in operation["parameters"]}
    assert (bornes["z"]["minimum"], bornes["z"]["maximum"]) == (16, 19)


def test_the_route_is_in_the_published_contract():
    depot = Path(__file__).resolve().parents[3]
    publie = json.loads(
        (depot / "contracts/openapi.v1.json").read_text(encoding="utf-8")
    )

    assert GABARIT in publie["paths"]
    assert publie["paths"][GABARIT] == app.openapi()["paths"][GABARIT]
