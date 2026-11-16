"""Actual proxy HTTP/Resolve boundaries with isolated malformed upstreams."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.bdnb import client as bdnb
from app.resolve import multi
from app.sirene.router import router as sirene_router
from tests.test_cor11_12_proxy_http import app as proxy_app  # noqa: F401

pytestmark = pytest.mark.unit
SIRENE = "https://recherche-entreprises.api.gouv.fr/near_point"
GEOCODE = f"{bdnb.BDNB_BASE}/geocodage"
BUILDING = f"{bdnb.BDNB_BASE}/donnees/batiment_groupe_complet/adresse"
ADDRESS = "1 rue synthetique 44109"
KEY = "44109_0001_00001"
VALID_GEOCODE = {"features": [{"properties": {"cle_interop_adr": KEY}}]}
VALID_BUILDINGS = [
    {"batiment_groupe_id": "SYN-BUILDING", "code_commune_insee": "44109"}
]
SIRENE_INVALID = [
    pytest.param({}, id="missing-results"),
    pytest.param({"error": "upstream failure"}, id="error-envelope"),
    pytest.param({"results": {}}, id="object-instead-of-list"),
    pytest.param({"results": "broken"}, id="string-instead-of-list"),
    pytest.param({"results": [None]}, id="null-item"),
    pytest.param({"results": [17]}, id="numeric-item"),
]
GEOCODE_INVALID = [
    pytest.param({}, id="missing-features"),
    pytest.param({"features": {}}, id="object-instead-of-list"),
    pytest.param({"features": "broken"}, id="string-instead-of-list"),
    pytest.param({"features": [None]}, id="null-feature"),
    pytest.param({"features": [{"properties": None}]}, id="null-properties"),
]


@pytest.fixture
def app(proxy_app, monkeypatch):  # noqa: F811
    proxy_app.include_router(sirene_router)
    monkeypatch.setattr(multi, "guard_publication", AsyncMock())
    monkeypatch.setattr(
        multi,
        "equipements_par_point",
        AsyncMock(return_value=[{"id": "SYN-AUTONOMOUS", "nom": "Equipment witness"}]),
    )
    bdnb._geocode_cache.clear()
    yield proxy_app
    bdnb._geocode_cache.clear()


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local.test"
    )


@pytest.mark.parametrize("body", SIRENE_INVALID)
async def test_sirene_malformed_200_is_502(app, body):
    with respx.mock as upstream:
        route = upstream.get(SIRENE).respond(200, json=body)
        async with client(app) as api:
            direct = await api.get("/v1/entreprises", params={"lat": 47.2, "lon": -1.5})
    assert route.call_count == 1
    assert direct.status_code == 502


@pytest.mark.parametrize("body", SIRENE_INVALID)
async def test_resolve_malformed_sirene_is_error_and_keeps_autonomous_data(app, body):
    with respx.mock as upstream:
        route = upstream.get(SIRENE).respond(200, json=body)
        async with client(app) as api:
            resolved = await api.get(
                "/v1/resolve",
                params={"q": "47.2,-1.5", "datasets": "entreprises,equipements"},
            )
    assert route.call_count == 1
    assert resolved.status_code == 200
    document = resolved.json()["donnees"][0]
    assert document["datasets"]["equipements"] == [
        {"id": "SYN-AUTONOMOUS", "nom": "Equipment witness"}
    ]
    assert document["dataset_states"]["entreprises"]["presence"] == "error"
    assert any(e["dataset"] == "entreprises" for e in document["erreurs"])


async def test_valid_sirene_payload_reaches_both_public_consumers(app):
    company = {"siren": "123456789", "nom_complet": "Synthetic company"}
    with respx.mock as upstream:
        upstream.get(SIRENE).respond(200, json={"results": [company]})
        async with client(app) as api:
            direct = await api.get("/v1/entreprises", params={"lat": 47.2, "lon": -1.5})
            resolved = await api.get(
                "/v1/resolve", params={"q": "47.2,-1.5", "datasets": "entreprises"}
            )
    assert direct.status_code == 200
    assert direct.json()["donnees"][0]["siren"] == company["siren"]
    document = resolved.json()["donnees"][0]
    assert document["datasets"]["entreprises"] == [company]
    assert document["dataset_states"]["entreprises"]["presence"] == "value"


async def test_sirene_real_empty_is_not_an_upstream_failure(app):
    with respx.mock as upstream:
        upstream.get(SIRENE).respond(200, json={"results": []})
        async with client(app) as api:
            direct = await api.get("/v1/entreprises", params={"lat": 47.2, "lon": -1.5})
            resolved = await api.get(
                "/v1/resolve", params={"q": "47.2,-1.5", "datasets": "entreprises"}
            )
    assert direct.status_code == 200 and direct.json()["donnees"] == []
    document = resolved.json()["donnees"][0]
    assert document["dataset_states"]["entreprises"]["presence"] == "absent"
    assert document["dataset_states"]["entreprises"]["absence_reason"] == "not_reported"
    assert document["datasets"]["entreprises"] == []
    assert not document["erreurs"]


@pytest.mark.parametrize("body", GEOCODE_INVALID)
async def test_bdnb_malformed_geocode_is_503_not_business_absence(app, body):
    with respx.mock as upstream:
        upstream.get(GEOCODE).respond(200, json=body)
        async with client(app) as api:
            response = await api.get("/v1/bdnb", params={"adresse": ADDRESS})
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "60"


@pytest.mark.parametrize(
    "body",
    [{"features": []}, {"features": [{"properties": {}}]}, {"features": [{}]}],
    ids=[
        "real-empty",
        "valid-feature-without-ban-key",
        "valid-feature-without-properties",
    ],
)
async def test_bdnb_valid_absence_keeps_business_404(app, body):
    with respx.mock as upstream:
        upstream.get(GEOCODE).respond(200, json=body)
        async with client(app) as api:
            response = await api.get("/v1/bdnb", params={"adresse": ADDRESS})
    assert response.status_code == 404


async def test_valid_bdnb_geocode_requests_exact_ban_key_and_building(app):
    with respx.mock as upstream:
        geocode = upstream.get(GEOCODE).respond(200, json=VALID_GEOCODE)
        buildings = upstream.get(BUILDING).respond(200, json=VALID_BUILDINGS)
        async with client(app) as api:
            response = await api.get("/v1/bdnb", params={"adresse": ADDRESS})
    assert response.status_code == 200
    assert geocode.call_count == buildings.call_count == 1
    assert buildings.calls[0].request.url.params["cle_interop_adr"] == f"eq.{KEY}"
    assert (
        response.json()["donnees"]["batiments"][0]["batiment_groupe_id"]
        == "SYN-BUILDING"
    )


@pytest.mark.parametrize("body", GEOCODE_INVALID)
async def test_invalid_geocode_is_not_cached_and_next_valid_body_fetches_building(
    app, body
):
    with respx.mock as upstream:
        geocode = upstream.get(GEOCODE).mock(
            side_effect=[
                httpx.Response(200, json=body),
                httpx.Response(200, json=VALID_GEOCODE),
            ]
        )
        buildings = upstream.get(BUILDING).respond(200, json=VALID_BUILDINGS)
        async with client(app) as api:
            first = await api.get("/v1/bdnb", params={"adresse": ADDRESS})
            second = await api.get("/v1/bdnb", params={"adresse": ADDRESS})
    assert geocode.call_count == 2, (first.status_code, second.status_code)
    assert second.status_code == 200
    assert first.status_code == 503
    assert buildings.call_count == 1
    assert buildings.calls[0].request.url.params["cle_interop_adr"] == f"eq.{KEY}"
    assert (
        second.json()["donnees"]["batiments"][0]["batiment_groupe_id"] == "SYN-BUILDING"
    )
