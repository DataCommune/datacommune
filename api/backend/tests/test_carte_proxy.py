"""04.7-17: ``GET /v1/carte/{ban_id}/{couche}.png``, the IGN map proxy with cache.

No network: the IGN is replaced by respx and the database by a session that
returns rows (the real ``point_de_la_cle`` reads them; only the SQL is
simulated). What is proven here is the privacy boundary (nothing of the visitor
reaches the IGN, the request is built by the server alone), the shape of what
is accepted and cached, the three rate buckets and the order of the I/O.
"""

import asyncio
import gzip
import math
import struct
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from cachetools import TTLCache
from httpx import ASGITransport, AsyncClient

from app.adresse.relations import PointBan
from app.adresse.service import (
    AdresseInconnueError,
    AdresseIndisponibleError,
    VoieSansNumeroError,
)
from app.adresse.service import point_de_la_cle as vrai_point_de_la_cle
from app.carte import proxy, router
from app.carte.proxy import COUCHES, emprise_3857
from app.config import settings
from app.database import get_db
from app.main import app
from app.rate_limit import _bucket_windows, _buckets
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE, catalogue_scope

pytestmark = pytest.mark.unit

BAN_ID = "44109_zz04_00004"
AUTRE_BAN_ID = "85191_0420_00012"
WMS = "https://data.geopf.fr/wms-r/wms"
DEUX_MO = 2 * 1024 * 1024
# Point of the 31/10 probe (04.7-17-sonde-wms.md), recovered from its BBOX.
SONDE_LON, SONDE_LAT = -1.5536416737764172, 47.22214029730324
SONDE_BBOX = (-173171.7, 5978187.6, -172729.5, 5978629.8)


def _chunk(type_: bytes, data: bytes, crc: int | None = None) -> bytes:
    if crc is None:
        crc = zlib.crc32(type_ + data)
    return struct.pack(">I", len(data)) + type_ + data + struct.pack(">I", crc)


def png_uni(largeur: int = 448, hauteur: int = 448, remplissage: int = 0) -> bytes:
    """A real single-colour PNG in pure Python; *remplissage* bytes of tEXt data."""
    ihdr = struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0)
    idat = zlib.compress((b"\x00" + b"\xc8\xb4\x96" * largeur) * hauteur)
    extra = _chunk(b"tEXt", b"k\x00" + b"x" * (remplissage - 2)) if remplissage else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + extra
        + _chunk(b"IEND", b"")
    )


def png_de_taille(octets: int) -> bytes:
    """A valid 448 x 448 PNG of exactly *octets* bytes."""
    return png_uni(remplissage=octets - len(png_uni()) - 12)


PNG = png_uni()
# One BAN row of Nantes, as ``_ADRESSES_PAR_CLE`` returns it.
LIGNE = {
    "numero": 4,
    "rep": None,
    "nom_voie": "rue Test",
    "code_postal": "44000",
    "code_commune": "44109",
    "nom_commune": "Nantes",
    "lon": -1.5536,
    "lat": 47.2184,
}

# Everything a visitor (or a proxy in front of the API) may send.
EN_TETES_VISITEUR = {
    "X-Forwarded-For": "203.0.113.5",
    "CF-Connecting-IP": "203.0.113.1",
    "X-Real-IP": "203.0.113.4",
    "Forwarded": "for=203.0.113.6",
    "True-Client-IP": "203.0.113.7",
    "User-Agent": "Visiteur/1.0 (secret-agent)",
    "Cookie": "dc_session=0123456789abcdef",
    "Referer": "https://visiteur.example/adresse/44109_zz04_00004",
    "Authorization": "Bearer jeton-visiteur",
    "X-API-Key": "dc_live_clef-visiteur",
    "Accept-Language": "fr-FR",
    "Origin": "https://visiteur.example",
}


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class SessionFactice:
    """Only the SQL is simulated: ``execute`` returns *lignes*; calls are journaled."""

    def __init__(self, journal: list[str], lignes: list[dict]) -> None:
        self.journal = journal
        self.lignes = lignes

    def begin_nested(self):
        return _Savepoint()

    async def execute(self, statement, params):
        self.journal.append("lecture")
        return [SimpleNamespace(_mapping=dict(ligne)) for ligne in self.lignes]

    async def rollback(self):
        self.journal.append("rollback")

    @property
    def lectures(self) -> int:
        return self.journal.count("lecture")


def _vider():
    proxy.CACHE.clear()
    proxy.NEGATIF.clear()
    proxy.EN_COURS.clear()
    _buckets.clear()
    _bucket_windows.clear()


@pytest.fixture(autouse=True)
def etat_propre(monkeypatch):
    """Empty caches and rate buckets; the BAN point is stubbed (see ``reel``)."""
    _vider()
    point = AsyncMock(return_value=PointBan(lon=-1.5536, lat=47.2184))
    monkeypatch.setattr(router, "point_de_la_cle", point)
    yield point
    _vider()


@pytest.fixture()
def reel(monkeypatch):
    """The real ``point_de_la_cle``: the route reads rows from the fake session."""
    monkeypatch.setattr(router, "point_de_la_cle", vrai_point_de_la_cle)


@pytest.fixture()
def journal() -> list[str]:
    return []


@pytest.fixture()
def session(journal) -> SessionFactice:
    return SessionFactice(journal, [LIGNE])


@pytest.fixture()
async def client(session):
    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def ign(journal):
    def _repond(request):
        journal.append("ign")
        return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})

    with respx.mock(assert_all_called=False) as mock:
        yield mock.get(WMS).mock(side_effect=_repond)


def _url(ban_id: str = BAN_ID, couche: str = "plan") -> str:
    return f"/v1/carte/{ban_id}/{couche}.png"


# --- emprise_3857 ----------------------------------------------------------


def test_emprise_matches_the_bbox_of_the_wms_probe():
    bbox = emprise_3857(SONDE_LON, SONDE_LAT)
    # The probe rounded its box to 0.1 m and used a half side 0.1 % wider.
    assert bbox == pytest.approx(SONDE_BBOX, abs=0.5)


def test_emprise_at_the_equator_is_300_m_wide_and_centered():
    assert emprise_3857(0.0, 0.0) == pytest.approx((-150.0, -150.0, 150.0, 150.0))


def test_emprise_is_300_ground_metres_whatever_the_latitude():
    for lat in (0.0, 47.2, 60.0):
        x0, y0, x1, y1 = emprise_3857(2.0, lat)
        facteur = 1 / math.cos(math.radians(lat))
        assert (x1 - x0) == pytest.approx(300 * facteur)
        assert (y1 - y0) == pytest.approx(300 * facteur, rel=1e-3)


# --- Happy path and outbound request ---------------------------------------


@pytest.mark.parametrize(
    ("couche", "nom_ign"),
    [
        ("plan", "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"),
        ("parcelles", "CADASTRALPARCELS.PARCELLAIRE_EXPRESS"),
    ],
)
async def test_serves_the_png_with_its_headers(client, ign, couche, nom_ign):
    reponse = await client.get(_url(couche=couche))

    assert reponse.status_code == 200
    assert reponse.content == PNG
    assert reponse.headers["content-type"] == "image/png"
    assert reponse.headers["cache-control"] == "public, max-age=86400"
    assert reponse.headers["x-content-type-options"] == "nosniff"
    assert ign.call_count == 1
    assert dict(ign.calls.last.request.url.params)["LAYERS"] == nom_ign
    assert COUCHES[couche] == nom_ign


async def test_the_outbound_request_is_built_by_the_server_alone(client, ign):
    await client.get(_url())

    requete = ign.calls.last.request
    x0, y0, x1, y1 = emprise_3857(-1.5536, 47.2184)
    assert requete.method == "GET"
    assert requete.url.scheme == "https"
    assert requete.url.host == "data.geopf.fr"
    assert requete.url.path == "/wms-r/wms"
    assert dict(requete.url.params) == {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2",
        "STYLES": "",
        "CRS": "EPSG:3857",
        "BBOX": f"{x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f}",
        "WIDTH": "448",
        "HEIGHT": "448",
        "FORMAT": "image/png",
    }


async def test_no_header_of_the_visitor_reaches_the_ign(client, ign):
    reponse = await client.get(_url(), headers=EN_TETES_VISITEUR)

    assert reponse.status_code == 200
    requete = ign.calls.last.request
    envoyees = {nom.lower(): valeur for nom, valeur in requete.headers.items()}
    # Fixed headers, plus the three httpx adds itself on every request.
    assert envoyees["user-agent"] == "DataCommune (+https://datacommune.fr)"
    assert envoyees["accept"] == "image/png"
    assert envoyees["host"] == "data.geopf.fr"
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


async def test_the_query_string_of_the_visitor_is_ignored(client, ign):
    sans = await client.get(_url())
    attendue = dict(ign.calls.last.request.url.params)
    proxy.CACHE.clear()

    avec = await client.get(
        _url() + "?LAYERS=OTHER&BBOX=0,0,1,1&WIDTH=4000&url=http://evil.example"
        "&couche=parcelles&host=evil.example"
    )

    assert sans.status_code == avec.status_code == 200
    assert dict(ign.calls.last.request.url.params) == attendue
    assert ign.calls.last.request.url.host == "data.geopf.fr"


async def test_the_wms_base_is_a_setting(client, ign, monkeypatch):
    monkeypatch.setattr(settings, "carte_wms_url", "https://wms.example.test/x")
    with respx.mock(assert_all_called=False) as mock:
        autre = mock.get("https://wms.example.test/x").respond(
            200, content=PNG, headers={"Content-Type": "image/png"}
        )
        reponse = await client.get(_url())
    assert reponse.status_code == 200
    assert autre.call_count == 1


# --- Input validation and BAN lookup ---------------------------------------


@pytest.mark.parametrize("couche", ["satellite", "PLAN", "plan.png", "..", "%2e%2e"])
async def test_a_layer_out_of_the_allowlist_is_refused(client, ign, couche):
    reponse = await client.get(f"/v1/carte/{BAN_ID}/{couche}.png")

    assert reponse.status_code in (404, 422)
    assert ign.call_count == 0


@pytest.mark.parametrize("ban_id", ["pas-une-cle", "44109_zz04_00004 x", "1" * 60])
async def test_an_invalid_ban_key_is_refused(client, ign, ban_id):
    reponse = await client.get(_url(ban_id))

    assert reponse.status_code in (404, 422)
    assert ign.call_count == 0


@pytest.mark.parametrize(
    ("erreur", "statut", "code"),
    [
        (AdresseInconnueError(), 404, "adresse_inconnue"),
        (VoieSansNumeroError(), 404, "voie_sans_numero"),
        (AdresseIndisponibleError(), 503, "adresse_indisponible"),
    ],
)
async def test_address_errors_are_constant_problems(
    client, ign, etat_propre, erreur, statut, code
):
    etat_propre.side_effect = erreur

    reponse = await client.get(_url())

    assert reponse.status_code == statut
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.json()["code"] == code
    assert BAN_ID not in reponse.text
    assert ign.call_count == 0


async def test_out_of_coverage_or_without_point_is_a_404(client, ign, etat_propre):
    etat_propre.return_value = None

    reponse = await client.get(_url())

    assert reponse.status_code == 404
    assert reponse.json()["code"] == "adresse_inconnue"
    assert ign.call_count == 0


async def test_the_ban_point_is_read_with_the_hosting_of_the_key(
    client, ign, etat_propre
):
    await client.get(_url())

    _, ban_id, hosting = etat_propre.call_args.args
    assert ban_id == BAN_ID
    assert hosting.hostings["ban"].etat == "heberge"


# --- Acceptance of the IGN answer ------------------------------------------


def _reponses_refusees():
    return [
        pytest.param(
            httpx.Response(500, content=PNG, headers={"Content-Type": "image/png"}),
            id="status-500",
        ),
        pytest.param(
            httpx.Response(204, headers={"Content-Type": "image/png"}), id="status-204"
        ),
        pytest.param(
            httpx.Response(302, headers={"Location": "https://evil.example/x.png"}),
            id="redirect",
        ),
        pytest.param(
            httpx.Response(200, content=PNG, headers={"Content-Type": "text/html"}),
            id="content-type-html",
        ),
        pytest.param(httpx.Response(200, content=PNG), id="content-type-missing"),
        pytest.param(
            httpx.Response(
                200,
                content=b"<ServiceExceptionReport/>" * 10,
                headers={"Content-Type": "image/png"},
            ),
            id="body-without-png-signature",
        ),
        pytest.param(
            httpx.Response(
                200,
                content=png_de_taille(DEUX_MO + 1),
                headers={"Content-Type": "image/png"},
            ),
            id="body-over-2-mo",
        ),
        pytest.param(
            httpx.Response(200, content=b"", headers={"Content-Type": "image/png"}),
            id="empty-body",
        ),
    ]


@pytest.mark.parametrize("refusee", _reponses_refusees())
async def test_an_unacceptable_answer_is_a_503_and_is_not_kept(client, refusee):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMS).mock(
            side_effect=[
                refusee,
                httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"}),
            ]
        )
        premiere = await client.get(_url())
        seconde = await client.get(_url())

    assert premiere.status_code == 503
    assert premiere.headers["content-type"].startswith("application/problem+json")
    assert premiere.headers["cache-control"] == "no-store"
    assert not premiere.content.startswith(b"\x89PNG")
    assert BAN_ID not in premiere.text
    # The outage was not stored: the next visitor asks the IGN again and gets the image.
    assert route.call_count == 2
    assert seconde.status_code == 200
    assert seconde.content == PNG


@pytest.mark.parametrize(
    "erreur",
    [
        httpx.ReadTimeout("lent"),
        httpx.ConnectTimeout("lent"),
        httpx.ConnectError("coupe"),
        httpx.RemoteProtocolError("casse"),
    ],
)
async def test_a_network_error_or_timeout_is_a_503_and_is_not_kept(client, erreur):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMS).mock(
            side_effect=[
                erreur,
                httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"}),
            ]
        )
        premiere = await client.get(_url())
        seconde = await client.get(_url())

    assert premiere.status_code == 503
    assert premiere.headers["content-type"].startswith("application/problem+json")
    assert route.call_count == 2
    assert seconde.status_code == 200


async def test_an_image_of_exactly_2_mo_is_accepted(client):
    corps = png_de_taille(DEUX_MO)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMS).respond(200, content=corps, headers={"Content-Type": "image/png"})
        reponse = await client.get(_url())

    assert len(corps) == DEUX_MO
    assert reponse.status_code == 200


async def test_a_content_type_parameter_is_tolerated(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMS).respond(
            200, content=PNG, headers={"Content-Type": "image/png; charset=binary"}
        )
        reponse = await client.get(_url())

    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "image/png"


def test_the_timeout_is_short_and_redirects_are_not_followed():
    assert proxy.TIMEOUT.read <= 5.0
    assert proxy.TIMEOUT.connect <= 3.0


# --- Cache -----------------------------------------------------------------


async def test_a_second_call_does_not_call_the_ign(client, ign):
    premiere = await client.get(_url())
    seconde = await client.get(_url())

    assert premiere.content == seconde.content == PNG
    assert ign.call_count == 1


async def test_the_cache_key_is_the_address_and_the_layer(client, ign):
    await client.get(_url(BAN_ID, "plan"))
    await client.get(_url(BAN_ID, "parcelles"))
    await client.get(_url(AUTRE_BAN_ID, "plan"))
    await client.get(_url(BAN_ID, "plan"))

    assert ign.call_count == 3
    assert set(proxy.CACHE) == {
        (BAN_ID, "plan"),
        (BAN_ID, "parcelles"),
        (AUTRE_BAN_ID, "plan"),
    }


def test_the_cache_is_bounded_in_bytes_for_21_days():
    assert isinstance(proxy.CACHE, TTLCache)
    assert proxy.CACHE.maxsize == 64 * 1024 * 1024
    assert proxy.CACHE.ttl == 1_814_400
    proxy.CACHE[("k", "plan")] = b"x" * 1000
    assert proxy.CACHE.currsize == 1000


def test_the_byte_bound_evicts_the_oldest_image():
    cache = proxy.nouveau_cache(octets=250, ttl_secondes=60)
    cache[("a", "plan")] = b"x" * 100
    cache[("b", "plan")] = b"x" * 100
    cache[("c", "plan")] = b"x" * 100

    assert ("a", "plan") not in cache
    assert set(cache) == {("b", "plan"), ("c", "plan")}
    assert cache.currsize <= 250


async def test_an_evicted_image_is_asked_again(client, ign, monkeypatch):
    petit = proxy.nouveau_cache(octets=len(PNG) + 10, ttl_secondes=60)
    monkeypatch.setattr(proxy, "CACHE", petit)

    await client.get(_url(BAN_ID))
    await client.get(_url(AUTRE_BAN_ID))
    await client.get(_url(BAN_ID))

    assert ign.call_count == 3
    assert list(petit) == [(BAN_ID, "plan")]


# --- Rate buckets ----------------------------------------------------------


async def test_the_ip_bucket_admits_60_a_minute_then_refuses(client, ign):
    statuts = [(await client.get(_url())).status_code for _ in range(60)]
    refusee = await client.get(_url())

    assert statuts == [200] * 60
    assert refusee.status_code == 429
    assert int(refusee.headers["retry-after"]) > 0
    assert refusee.headers["cache-control"] == "no-store"
    assert not refusee.content.startswith(b"\x89PNG")
    # The 60 first hits came from the cache but one: the IGN was asked once.
    assert ign.call_count == 1
    assert any(key.startswith("carte:ip:") for key in _buckets)


async def test_the_ip_bucket_is_per_visitor(client, ign, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "carte_rate_limit_per_ip", 2)
    un = {"CF-Connecting-IP": "203.0.113.10"}
    autre = {"CF-Connecting-IP": "203.0.113.11"}

    assert (await client.get(_url(), headers=un)).status_code == 200
    assert (await client.get(_url(), headers=un)).status_code == 200
    assert (await client.get(_url(), headers=un)).status_code == 429
    assert (await client.get(_url(), headers=autre)).status_code == 200


async def test_the_ip_bucket_is_consumed_before_any_read(
    client, ign, etat_propre, monkeypatch
):
    monkeypatch.setattr(settings, "carte_rate_limit_per_ip", 1)
    await client.get(_url())
    etat_propre.reset_mock()

    refusee = await client.get(_url(AUTRE_BAN_ID))

    assert refusee.status_code == 429
    etat_propre.assert_not_called()
    assert ign.call_count == 1


async def test_the_upstream_bucket_counts_cache_misses_only(client, ign, monkeypatch):
    monkeypatch.setattr(settings, "carte_rate_limit_amont", 2)
    ids = ["44109_zz04_00004", "44109_zz04_00005", "44109_zz04_00006"]

    assert (await client.get(_url(ids[0]))).status_code == 200
    assert (await client.get(_url(ids[1]))).status_code == 200
    # A cache hit does not consume a token of the upstream bucket.
    assert (await client.get(_url(ids[0]))).status_code == 200
    refusee = await client.get(_url(ids[2]))

    assert refusee.status_code == 429
    assert int(refusee.headers["retry-after"]) > 0
    assert refusee.headers["cache-control"] == "no-store"
    assert ign.call_count == 2
    # What is cached is still served while the IGN is protected.
    assert (await client.get(_url(ids[1]))).status_code == 200
    assert "carte:amont" in _buckets


async def test_the_upstream_bucket_is_global_across_visitors(client, ign, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "carte_rate_limit_amont", 1)

    premiere = await client.get(
        _url(BAN_ID), headers={"CF-Connecting-IP": "203.0.113.20"}
    )
    seconde = await client.get(
        _url(AUTRE_BAN_ID), headers={"CF-Connecting-IP": "203.0.113.21"}
    )

    assert premiere.status_code == 200
    assert seconde.status_code == 429
    assert ign.call_count == 1


def test_the_default_limits_are_the_planned_ones():
    assert settings.carte_rate_limit_per_ip == 60
    assert settings.carte_rate_limit_amont == 600


# --- OpenAPI ---------------------------------------------------------------


def test_the_route_is_public_and_documented():
    spec = app.openapi()
    operation = spec["paths"]["/v1/carte/{ban_id}/{couche}.png"]["get"]

    assert set(operation["responses"]) >= {"200", "404", "429", "503"}
    assert "image/png" in operation["responses"]["200"]["content"]
    assert operation["summary"].isascii() and operation["description"].isascii()
    assert "security" not in operation
    parametres = {p["name"] for p in operation["parameters"]}
    assert parametres == {"ban_id", "couche"}


def test_the_route_is_mounted_without_the_api_key_dependency():
    from fastapi.routing import APIRoute

    from app.auth.dependencies import check_quota_hybrid

    route = next(
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == "/v1/carte/{ban_id}/{couche}.png"
    )

    def appels(dependant):
        for dep in dependant.dependencies:
            yield dep.call
            yield from appels(dep)

    assert check_quota_hybrid not in set(appels(route.dependant))


# --- Review fixes: the real reader, scope on a hit, encoding, PNG structure ----


async def test_the_route_runs_through_the_real_point_reader(
    client, ign, reel, session, journal
):
    """P1: the route declares only ``ban``, not the ``cadastre`` of the identity."""
    reponse = await client.get(_url())

    assert reponse.status_code == 200
    assert reponse.content == PNG
    assert session.lectures == 1


async def test_the_transaction_is_released_before_the_ign_call(
    client, ign, reel, journal
):
    await client.get(_url())

    assert journal == ["lecture", "rollback", "ign"]


@pytest.mark.parametrize(
    ("ban_id", "lignes", "code", "lectures"),
    [
        (BAN_ID, [], "adresse_inconnue", 1),
        ("44109_4476", [LIGNE], "voie_sans_numero", 0),
        ("99999_4476_00004", [LIGNE], "adresse_inconnue", 0),
        (BAN_ID, [{**LIGNE, "lon": None, "lat": None}], "adresse_inconnue", 1),
        (BAN_ID, [LIGNE, {**LIGNE, "lon": -1.6}], "adresse_inconnue", 1),
    ],
    ids=["unknown-row", "street-key", "unknown-territory", "no-position", "two-points"],
)
async def test_the_real_reader_refuses_with_a_404(
    client, ign, reel, session, ban_id, lignes, code, lectures
):
    session.lignes = lignes

    reponse = await client.get(_url(ban_id))

    assert reponse.status_code == 404
    assert reponse.json()["code"] == code
    assert session.lectures == lectures
    assert ign.call_count == 0


async def test_a_cache_hit_is_refused_once_the_address_left_the_scope(
    client, ign, reel, session
):
    proxy.CACHE[(BAN_ID, "plan")] = PNG
    app.dependency_overrides[get_instance_scope] = lambda: catalogue_scope(("85",))

    reponse = await client.get(_url())

    assert reponse.status_code == 404
    assert not reponse.content.startswith(b"\x89PNG")
    assert session.lectures == 0
    assert ign.call_count == 0


async def test_an_address_out_of_scope_is_a_404_without_any_read(
    client, ign, reel, session
):
    app.dependency_overrides[get_instance_scope] = lambda: catalogue_scope(("85",))

    reponse = await client.get(_url())

    assert reponse.status_code == 404
    assert session.lectures == 0


# Encoding and size ---------------------------------------------------------------


async def test_the_ign_is_asked_for_an_identity_encoding(client, ign):
    await client.get(_url())

    assert ign.calls.last.request.headers["accept-encoding"] == "identity"


async def test_a_compressed_answer_is_refused_before_it_is_read(client):
    bombe = gzip.compress(PNG)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMS).respond(
            200,
            content=bombe,
            headers={"Content-Type": "image/png", "Content-Encoding": "gzip"},
        )
        reponse = await client.get(_url())

    assert reponse.status_code == 503
    assert route.call_count == 1
    assert (BAN_ID, "plan") not in proxy.CACHE


async def test_an_identity_content_encoding_is_accepted(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMS).respond(
            200,
            content=PNG,
            headers={"Content-Type": "image/png", "Content-Encoding": "identity"},
        )
        reponse = await client.get(_url())

    assert reponse.status_code == 200


async def test_the_size_is_checked_before_a_chunk_is_appended(client, monkeypatch):
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

    monkeypatch.setattr(proxy, "bytearray", EspionOctets, raising=False)
    monkeypatch.setattr(settings, "carte_image_max_octets", 100)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMS).respond(
            200, content=morceaux(), headers={"Content-Type": "image/png"}
        )
        reponse = await client.get(_url())

    assert reponse.status_code == 503
    assert EspionOctets.plus_grand <= 100


# PNG structure -------------------------------------------------------------------


def _corrompre(png: bytes, position: int) -> bytes:
    return png[:position] + bytes([png[position] ^ 0xFF]) + png[position + 1 :]


def _png_sans_idat(taille: int = 448) -> bytes:
    ihdr = struct.pack(">IIBBBBB", taille, taille, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")


def _png_idat_avant_ihdr(taille: int = 448) -> bytes:
    ihdr = struct.pack(">IIBBBBB", taille, taille, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IDAT", b"x")
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IEND", b"")
    )


def _png_idat(flux: bytes, entrelace: int = 0, taille: int = 448) -> bytes:
    """A square RGB PNG of *taille* whose chunks are right, around the given IDAT."""
    ihdr = struct.pack(">IIBBBBB", taille, taille, 8, 2, 0, 0, entrelace)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", flux)
        + _chunk(b"IEND", b"")
    )


def _png_invalides(taille: int = 448) -> dict[str, bytes]:
    """Every malformed PNG of the suite, built around a square image of *taille*.

    448 is the static map; 256 is a tile of the base map (04.10-04): the same
    malformations must be refused at the size the caller asks for.
    """
    png = png_uni(taille, taille)
    ligne = b"\x00" + b"\xc8\xb4\x96" * taille
    n = len(png)
    invalides = {
        "signature-only": png[:8],
        "empty": b"",
        "iend-crc-wrong": _corrompre(png, n - 1),
        "ihdr-crc-wrong": _corrompre(png, 8 + 8 + 13),
        "idat-crc-wrong": _corrompre(png, n - 12 - 1),
        "idat-data-flipped": _corrompre(png, 8 + 25 + 8 + 4),
        f"width-{taille - 1}": png_uni(taille - 1, taille),
        f"height-{taille - 1}": png_uni(taille, taille - 1),
        "1x1": png_uni(1, 1),
        "no-idat": _png_sans_idat(taille),
        "idat-before-ihdr": _png_idat_avant_ihdr(taille),
        "trailing-byte": png + b"\x00",
        "trailing-chunk": png + _chunk(b"tEXt", b"k\x00v"),
        "no-iend": png[: n - 12],
        "length-lies": png[:8] + struct.pack(">I", 2**31 - 1) + png[12:],
        # Chunks and CRC right, pixels wrong (review Codex n° 2).
        "idat-not-zlib": _png_idat(b"\x00" * 40, taille=taille),
        "idat-short": _png_idat(zlib.compress(ligne * (taille - 1)), taille=taille),
        "idat-long": _png_idat(zlib.compress(ligne * (taille + 1)), taille=taille),
        "idat-stream-cut": _png_idat(zlib.compress(ligne * taille)[:-4], taille=taille),
        "interlaced": _png_idat(
            zlib.compress(ligne * taille), entrelace=1, taille=taille
        ),
        # The right size, but a row filter PNG does not know (review Codex n° 3).
        "unknown-row-filter": _png_idat(
            zlib.compress(b"\x05" + ligne[1:] + ligne * (taille - 1)), taille=taille
        ),
    }
    for coupe in (4, 9, 12, 16, 20, 25, 33, 37, 45, n // 2, n - 13, n - 5, n - 1):
        invalides[f"cut-{coupe}"] = png[:coupe]
    return invalides


def test_a_complete_png_of_448_is_valid():
    assert proxy.png_valide(PNG)
    assert proxy.png_valide(png_de_taille(DEUX_MO))


@pytest.mark.parametrize("nom", sorted(_png_invalides()))
def test_a_malformed_png_is_invalid(nom):
    assert not proxy.png_valide(_png_invalides()[nom])


# The size is a parameter (04.10-04): 448 unless asked, 256 for a tile ------------

TUILE_PX = 256


def test_a_complete_png_of_256_is_valid_only_when_256_is_asked():
    tuile = png_uni(TUILE_PX, TUILE_PX)

    assert proxy.png_valide(tuile, TUILE_PX)
    assert not proxy.png_valide(tuile)
    assert not proxy.png_valide(tuile, 448)
    # And the static image is no tile.
    assert not proxy.png_valide(PNG, TUILE_PX)
    assert proxy.png_valide(PNG, 448)


@pytest.mark.parametrize("nom", sorted(_png_invalides(TUILE_PX)))
def test_a_malformed_png_of_256_is_invalid_at_both_sizes(nom):
    corps = _png_invalides(TUILE_PX)[nom]

    assert not proxy.png_valide(corps, TUILE_PX)
    assert not proxy.png_valide(corps)


@pytest.mark.parametrize("nom", sorted(_png_invalides()))
def test_a_malformed_png_of_448_is_invalid_at_256_too(nom):
    assert not proxy.png_valide(_png_invalides()[nom], TUILE_PX)


@pytest.mark.parametrize(
    "nom",
    ["signature-only", "idat-crc-wrong", "width-447", "cut-33", "unknown-row-filter"],
)
async def test_a_malformed_png_is_a_503_and_is_not_kept(client, nom):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMS).respond(
            200, content=_png_invalides()[nom], headers={"Content-Type": "image/png"}
        )
        reponse = await client.get(_url())

    assert reponse.status_code == 503
    assert len(proxy.CACHE) == 0


# Resolution bucket and negative cache ---------------------------------------------


async def test_the_resolution_bucket_counts_cache_misses_before_the_read(
    client, ign, etat_propre, monkeypatch
):
    monkeypatch.setattr(settings, "carte_rate_limit_resolution", 2)
    ids = ["44109_zz04_00004", "44109_zz04_00005", "44109_zz04_00006"]

    assert (await client.get(_url(ids[0]))).status_code == 200
    assert (await client.get(_url(ids[1]))).status_code == 200
    assert (await client.get(_url(ids[0]))).status_code == 200  # a hit: no token
    refusee = await client.get(_url(ids[2]))

    assert refusee.status_code == 429
    assert int(refusee.headers["retry-after"]) > 0
    assert refusee.headers["cache-control"] == "no-store"
    assert etat_propre.call_count == 2
    assert "carte:resolution" in _buckets


def test_the_default_resolution_limit_is_600():
    assert settings.carte_rate_limit_resolution == 600


async def test_an_unknown_key_is_a_404_kept_briefly(client, ign, reel, session):
    session.lignes = []

    premiere = await client.get(_url(BAN_ID, "plan"))
    seconde = await client.get(_url(BAN_ID, "parcelles"))

    assert premiere.status_code == seconde.status_code == 404
    assert seconde.json()["code"] == "adresse_inconnue"
    assert session.lectures == 1
    assert len(_buckets["carte:resolution"]) == 1


async def test_a_street_key_is_a_404_kept_briefly(client, ign, reel, session):
    premiere = await client.get(_url("44109_4476"))
    seconde = await client.get(_url("44109_4476"))

    assert premiere.status_code == seconde.status_code == 404
    assert seconde.json()["code"] == "voie_sans_numero"
    assert len(_buckets["carte:resolution"]) == 1


async def test_the_negative_cache_is_consulted_before_the_resolution_bucket(
    client, ign, reel, session, monkeypatch
):
    session.lignes = []
    monkeypatch.setattr(settings, "carte_rate_limit_resolution", 1)
    await client.get(_url())

    seconde = await client.get(_url())

    assert seconde.status_code == 404  # not 429: repeating a known 404 costs no token


async def test_an_outage_is_never_kept_as_a_404(client, ign, etat_propre):
    etat_propre.side_effect = [
        AdresseIndisponibleError(),
        PointBan(lon=-1.5536, lat=47.2184),
    ]

    premiere = await client.get(_url())
    seconde = await client.get(_url())

    assert premiere.status_code == 503
    assert seconde.status_code == 200
    assert len(proxy.NEGATIF) == 0


def test_the_negative_cache_is_short_and_bounded():
    assert isinstance(proxy.NEGATIF, TTLCache)
    assert proxy.NEGATIF.maxsize == 10_000
    assert proxy.NEGATIF.ttl == 600


async def test_the_negative_cache_evicts_beyond_its_bound(
    client, ign, reel, session, monkeypatch
):
    session.lignes = []
    monkeypatch.setattr(proxy, "NEGATIF", TTLCache(maxsize=2, ttl=600))
    ids = ["44109_zz04_00004", "44109_zz04_00005", "44109_zz04_00006"]
    for ban_id in ids:
        await client.get(_url(ban_id))

    await client.get(_url(ids[0]))

    assert session.lectures == 4
    assert len(proxy.NEGATIF) == 2


# Visitor bucket by /64, navigation ceiling ----------------------------------------


async def test_ipv6_addresses_of_one_64_share_the_visitor_bucket(
    client, ign, monkeypatch
):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "carte_rate_limit_per_ip", 2)

    def depuis(adresse: str) -> dict[str, str]:
        return {"CF-Connecting-IP": adresse}

    premiere = await client.get(_url(), headers=depuis("2001:db8:1:2::1"))
    seconde = await client.get(
        _url(), headers=depuis("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    )
    refusee = await client.get(_url(), headers=depuis("2001:db8:1:2:ffff::9"))
    autre_reseau = await client.get(_url(), headers=depuis("2001:db8:1:3::1"))

    assert premiere.status_code == seconde.status_code == 200
    assert refusee.status_code == 429
    assert autre_reseau.status_code == 200
    assert "carte:ip:2001:db8:1:2::/64" in _buckets


async def test_an_ipv4_address_keeps_its_own_bucket(client, ign, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)

    await client.get(_url(), headers={"CF-Connecting-IP": "203.0.113.10"})
    await client.get(_url(), headers={"CF-Connecting-IP": "::ffff:203.0.113.11"})

    assert "carte:ip:203.0.113.10" in _buckets
    assert "carte:ip:203.0.113.11" in _buckets


async def test_the_map_does_not_spend_the_navigation_ceiling(client, ign, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_ip", 2)

    statuts = [(await client.get(_url())).status_code for _ in range(5)]

    assert statuts == [200] * 5
    assert not any(cle.startswith("ip:") for cle in _buckets)


# Concurrent misses -----------------------------------------------------------------


async def test_concurrent_misses_on_one_key_make_one_ign_call(client, session):
    async def lent(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMS).mock(side_effect=lent)
        reponses = await asyncio.gather(client.get(_url()), client.get(_url()))

    assert [r.status_code for r in reponses] == [200, 200]
    assert route.call_count == 1
    assert len(_buckets["carte:amont"]) == 1
    assert proxy.EN_COURS == {}


async def test_a_waiter_gives_its_connection_back_before_waiting(client, reel, journal):
    async def lent(request):
        await asyncio.sleep(0.05)
        journal.append("ign")
        return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(WMS).mock(side_effect=lent)
        reponses = await asyncio.gather(client.get(_url()), client.get(_url()))

    assert [r.status_code for r in reponses] == [200, 200]
    assert journal.count("ign") == 1
    # The producer releases after its read, the waiter before it waits.
    assert journal.count("rollback") == 2
    assert journal.index("rollback") < journal.index("ign")
    assert journal[1:].index("rollback") + 1 < journal.index("ign")


async def test_a_failed_shared_call_reaches_every_waiter_and_is_dropped(client):
    async def lent(request):
        await asyncio.sleep(0.05)
        raise httpx.ConnectError("coupe")

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(WMS).mock(side_effect=lent)
        reponses = await asyncio.gather(client.get(_url()), client.get(_url()))
        assert [r.status_code for r in reponses] == [503, 503]
        assert route.call_count == 1
        assert proxy.EN_COURS == {}
        route.side_effect = None
        route.return_value = httpx.Response(
            200, content=PNG, headers={"Content-Type": "image/png"}
        )
        apres = await client.get(_url())

    assert apres.status_code == 200
    assert route.call_count == 2


# Layer parameters ---------------------------------------------------------------------


async def test_only_the_parcel_layer_asks_for_transparency(client, ign):
    await client.get(_url(couche="plan"))
    plan = dict(ign.calls.last.request.url.params)
    await client.get(_url(couche="parcelles"))
    parcelles = dict(ign.calls.last.request.url.params)

    assert "TRANSPARENT" not in plan
    assert parcelles["TRANSPARENT"] == "TRUE"
    commun = {k: v for k, v in parcelles.items() if k not in ("TRANSPARENT", "LAYERS")}
    assert commun == {k: v for k, v in plan.items() if k != "LAYERS"}
