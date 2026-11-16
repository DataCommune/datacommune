"""Unit tests: /pollutions is fail-closed (ADR-0004 D1).

Before ADR-0004 a down Georisques SSP returned HTTP 200 with all fields null —
indistinguishable from "no polluted site nearby", the worst possible lie for a
data-confidence product. It must now surface as a 503 the client can retry on.
The nominal (upstream OK) path must be untouched.
"""

import httpx
import pytest
import respx

from app.common.exceptions import UpstreamUnavailableError
from app.georisques.client import query_polluted_sites

GEORISQUES_BASE = "https://georisques.gouv.fr/api/v1"
SSP_URL = f"{GEORISQUES_BASE}/ssp"
DEFAULT_PARAMS = {"lat": 48.8566, "lon": 2.3522}

SSP_RESPONSE = {
    "casias": {"data": [{"nom": "Ancien site industriel"}], "results": 1},
    "instructions": {"data": [], "results": 0},
    "conclusions_sis": {"data": [], "results": 0},
    "conclusions_sup": {"data": [], "results": 0},
}


# -- Client-level -----------------------------------------------------------


@pytest.mark.unit
async def test_client_raises_when_upstream_down():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SSP_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(UpstreamUnavailableError):
            await query_polluted_sites(**DEFAULT_PARAMS)


@pytest.mark.unit
async def test_client_raises_on_invalid_body():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SSP_URL).mock(return_value=httpx.Response(200, text="<html>nope"))
        with pytest.raises(UpstreamUnavailableError):
            await query_polluted_sites(**DEFAULT_PARAMS)


# -- Router-level -----------------------------------------------------------


@pytest.mark.unit
async def test_router_503_not_200_when_upstream_down(client):
    """Down upstream -> 503 + Retry-After, NEVER a reassuring 200-with-nulls."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SSP_URL).mock(side_effect=httpx.ConnectError("down"))
        resp = await client.get("/v1/pollutions", params=DEFAULT_PARAMS)

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "60"
    assert "temporairement indisponible" in resp.text


@pytest.mark.unit
async def test_router_200_unchanged_on_valid_response(client):
    """Nominal path is untouched: valid upstream -> 200 with the parsed data."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SSP_URL).mock(return_value=httpx.Response(200, json=SSP_RESPONSE))
        resp = await client.get("/v1/pollutions", params=DEFAULT_PARAMS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["donnees"]["casias"] == [{"nom": "Ancien site industriel"}]
