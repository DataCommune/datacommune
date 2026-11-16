"""Unit tests for RNB proxy: upstream failure → 503 + Retry-After (H2).

Regression: before H2, an upstream 5xx raised httpx.HTTPStatusError that
escaped the router (no try/except) → bare 500 without Retry-After.
A 404 stays "no building found" (→ []), not an upstream failure.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import HTTPException

from app.common.exceptions import UpstreamUnavailableError
from app.rnb import client as rnb_client
from app.rnb.client import query_by_coords

RNB_CLOSEST = "https://rnb-api.beta.gouv.fr/api/alpha/buildings/closest/"

BUILDINGS_RESPONSE = {
    "results": [{"rnb_id": "ABCD1234", "status": "constructed", "is_active": True}]
}

COORDS_PARAMS = {"lat": 48.8566, "lon": 2.3522}


# -- Client-level tests -----------------------------------------------------


@pytest.mark.unit
async def test_client_raises_on_upstream_5xx():
    """An upstream 5xx must raise UpstreamUnavailableError."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(RNB_CLOSEST).mock(return_value=httpx.Response(500))
        with pytest.raises(UpstreamUnavailableError):
            await query_by_coords(48.8566, 2.3522)


@pytest.mark.unit
async def test_client_404_returns_empty_list():
    """A 404 is 'no building found', NOT an upstream failure → []."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(RNB_CLOSEST).mock(return_value=httpx.Response(404))
        result = await query_by_coords(48.8566, 2.3522)
    assert result == []


@pytest.mark.unit
async def test_query_by_parcelle_interroge_la_parcelle_sans_parametre(monkeypatch):
    """The public /v1/rnb proxy keeps its payload: never any query parameter.

    The ``with_plots`` opt-in (``withPlots=1``, the BAN keys of every
    entrance) was removed with its last caller: Lot C1 replaced that live
    per-parcel widening call by a local ``rnb_liens`` join fed in bulk by the
    departmental RNB export. This client now serves the proxy alone, so the
    absence of a parameter IS the contract, not a default.
    """
    mock = AsyncMock(return_value=[])
    monkeypatch.setattr(rnb_client, "_get", mock)

    await rnb_client.query_by_parcelle("44109000MR0307")

    url = mock.await_args.args[0]
    assert url.endswith("/buildings/plot/44109000MR0307/")
    assert mock.await_args.kwargs.get("params") is None


@pytest.mark.unit
async def test_client_raises_on_429():
    """A 429 must raise UpstreamUnavailableError."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(RNB_CLOSEST).mock(return_value=httpx.Response(429))
        with pytest.raises(UpstreamUnavailableError):
            await query_by_coords(48.8566, 2.3522)


# -- LB-01: input format validation (path-injection defense) -----------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "malformed",
    [
        "../../admin/settings",
        "ABCD1234?with=query",
        "ABCD1234#fragment",
        "ABCD 1234",
        "ABCD-1234",
        "ABCD1234/",
    ],
)
async def test_query_by_rnb_id_rejecte_id_malforme(malformed, monkeypatch):
    """A malformed rnb_id must be rejected (HTTPException 422) BEFORE any
    upstream URL is built — no path traversal / query injection into
    rnb-api.beta.gouv.fr."""
    mock = AsyncMock()
    monkeypatch.setattr(rnb_client, "_get", mock)

    with pytest.raises(HTTPException) as exc_info:
        await rnb_client.query_by_rnb_id(malformed)

    assert exc_info.value.status_code == 422
    mock.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    "malformed",
    [
        "44109000MR0307/extra",
        "44109000MR030?7",
        "44109000mr0307",
        "44109000MR030",
        "",
    ],
)
async def test_query_by_parcelle_rejette_id_malforme(malformed, monkeypatch):
    """A parcelle_id that is not exactly [0-9A-Z]{14} must be rejected before
    the upstream call."""
    mock = AsyncMock()
    monkeypatch.setattr(rnb_client, "_get", mock)

    with pytest.raises(HTTPException) as exc_info:
        await rnb_client.query_by_parcelle(malformed)

    assert exc_info.value.status_code == 422
    mock.assert_not_awaited()


@pytest.mark.unit
async def test_router_rnb_id_malforme_sans_appel_upstream(client):
    """GET /v1/rnb?rnb_id=<traversal> → 422, zero request to the upstream."""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r"rnb-api\.beta\.gouv\.fr.*").mock(
            return_value=httpx.Response(200, json=BUILDINGS_RESPONSE)
        )
        resp = await client.get("/v1/rnb", params={"rnb_id": "../admin/settings"})

    assert resp.status_code == 422
    assert not route.called


# -- Router-level tests -----------------------------------------------------


@pytest.mark.unit
async def test_router_503_on_upstream_5xx(client):
    """GET /v1/rnb → 503 + Retry-After when upstream 5xx (was: bare 500)."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(RNB_CLOSEST).mock(return_value=httpx.Response(500))
        resp = await client.get("/v1/rnb", params=COORDS_PARAMS)

    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "60"


@pytest.mark.unit
async def test_router_503_body_does_not_leak_upstream(client):
    """503 body must stay generic, not leak upstream host/status."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(RNB_CLOSEST).mock(return_value=httpx.Response(500))
        resp = await client.get("/v1/rnb", params=COORDS_PARAMS)

    assert resp.status_code == 503
    assert "rnb-api" not in resp.text
    assert "temporairement indisponible" in resp.text


@pytest.mark.unit
async def test_router_200_on_success(client):
    """GET /v1/rnb → 200 with building data on upstream success."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(RNB_CLOSEST).mock(
            return_value=httpx.Response(200, json=BUILDINGS_RESPONSE)
        )
        resp = await client.get("/v1/rnb", params=COORDS_PARAMS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["donnees"]["total"] is None
    assert data["donnees"]["nombre_retournes"] == 1
    assert data["donnees"]["couverture"]["complet"] is None
    assert data["donnees"]["batiments"][0]["rnb_id"] == "ABCD1234"
