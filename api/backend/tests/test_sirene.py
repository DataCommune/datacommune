"""Unit tests for Sirene proxy: upstream failure → 503 + Retry-After (H2).

Regression: before H2, an upstream 5xx raised httpx.HTTPStatusError that
escaped the router (no try/except) → bare 500 without Retry-After.
"""

import httpx
import pytest
import respx

from app.common.exceptions import UpstreamUnavailableError
from app.sirene.client import entreprises_a_proximite

SIRENE_URL = "https://recherche-entreprises.api.gouv.fr/near_point"

ENTREPRISE_RESPONSE = {
    "results": [
        {"siren": "443061841", "nom_complet": "GOOGLE FRANCE", "distance": 0.5}
    ],
    "total_results": 1,
}

DEFAULT_PARAMS = {"lat": 47.2184, "lon": -1.5536}


# -- Client-level tests -----------------------------------------------------


@pytest.mark.unit
async def test_client_raises_on_upstream_5xx():
    """An upstream 5xx must raise UpstreamUnavailableError, not let it escape."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SIRENE_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(UpstreamUnavailableError):
            await entreprises_a_proximite(47.2184, -1.5536)


@pytest.mark.unit
async def test_client_raises_on_429():
    """A 429 must raise UpstreamUnavailableError."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SIRENE_URL).mock(return_value=httpx.Response(429))
        with pytest.raises(UpstreamUnavailableError):
            await entreprises_a_proximite(47.2184, -1.5536)


@pytest.mark.unit
async def test_client_raises_on_connect_error():
    """A connection error must raise UpstreamUnavailableError."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SIRENE_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(UpstreamUnavailableError):
            await entreprises_a_proximite(47.2184, -1.5536)


# -- Router-level tests -----------------------------------------------------


@pytest.mark.unit
async def test_router_503_on_upstream_5xx(client):
    """GET /v1/entreprises → 503 + Retry-After when upstream 5xx (was: bare 500)."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SIRENE_URL).mock(return_value=httpx.Response(503))
        resp = await client.get("/v1/entreprises", params=DEFAULT_PARAMS)

    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "60"


@pytest.mark.unit
async def test_router_503_body_does_not_leak_upstream(client):
    """503 body must stay generic, not leak upstream host/status."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SIRENE_URL).mock(return_value=httpx.Response(500))
        resp = await client.get("/v1/entreprises", params=DEFAULT_PARAMS)

    assert resp.status_code == 503
    assert "recherche-entreprises" not in resp.text
    assert "temporairement indisponible" in resp.text


@pytest.mark.unit
async def test_router_200_on_success(client):
    """GET /v1/entreprises → 200 with results on upstream success."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SIRENE_URL).mock(
            return_value=httpx.Response(200, json=ENTREPRISE_RESPONSE)
        )
        resp = await client.get("/v1/entreprises", params=DEFAULT_PARAMS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["donnees"][0]["siren"] == "443061841"
