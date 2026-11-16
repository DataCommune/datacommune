"""Unit tests for Urbanisme proxy: all-down → 503 + Retry-After, partial → 200."""

import httpx
import pytest
import respx

from app.common.exceptions import UpstreamUnavailableError
from app.urbanisme.client import query_urbanisme

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APICARTO_BASE = "https://apicarto.ign.fr/api/gpu"

# Minimal valid API Carto GPU response shapes (GeoJSON FeatureCollection)
ZONE_URBA_RESPONSE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"typezone": "U", "libelle": "Zone urbaine"},
            "geometry": None,
        }
    ],
}
PRESCRIPTION_RESPONSE = {"type": "FeatureCollection", "features": []}

# Query params used in tests
DEFAULT_PARAMS = {"lat": 48.8566, "lon": 2.3522}


# ---------------------------------------------------------------------------
# Helper: build a mock httpx.Response
# ---------------------------------------------------------------------------


def _json_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Return a real httpx.Response with JSON body."""
    return httpx.Response(status_code=status_code, json=data)


# ---------------------------------------------------------------------------
# Client-level tests (direct call to query_urbanisme)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_client_raises_upstream_unavailable_when_all_down():
    """query_urbanisme must raise UpstreamUnavailableError when both endpoints fail."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{APICARTO_BASE}/zone-urba").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{APICARTO_BASE}/prescription-surf").mock(
            side_effect=httpx.ConnectError("timeout")
        )

        with pytest.raises(UpstreamUnavailableError):
            await query_urbanisme(**DEFAULT_PARAMS)


@pytest.mark.unit
async def test_client_call_count_all_down():
    """Both endpoints must be attempted even when all fail (sanity: no early abort)."""
    with respx.mock(assert_all_called=True) as mock:
        for path in ["/zone-urba", "/prescription-surf"]:
            mock.get(f"{APICARTO_BASE}{path}").mock(
                side_effect=httpx.ConnectError("timeout")
            )

        with pytest.raises(UpstreamUnavailableError):
            await query_urbanisme(**DEFAULT_PARAMS)

        # assert_all_called=True in respx ensures every route was hit


@pytest.mark.unit
async def test_client_partial_degradation_returns_data():
    """1 endpoint OK + 1 down → no exception, returns partial data (D-09)."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{APICARTO_BASE}/zone-urba").mock(
            return_value=_json_response(ZONE_URBA_RESPONSE)
        )
        mock.get(f"{APICARTO_BASE}/prescription-surf").mock(
            side_effect=httpx.ConnectError("timeout")
        )

        result = await query_urbanisme(**DEFAULT_PARAMS)

    # zone-urba responded → zones_plu populated
    assert len(result["zones_plu"]) == 1
    # A failed prescription source is unknown, never an official empty list.
    assert result["prescriptions"] is None
    assert result["sources"]["prescriptions"]["statut"] == "indisponible"


# ---------------------------------------------------------------------------
# Router-level tests (via ASGI client)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_router_503_when_all_upstream_down(client):
    """GET /v1/urbanisme → 503 + Retry-After when all API Carto endpoints fail."""
    with respx.mock(assert_all_called=False) as mock:
        for path in ["/zone-urba", "/prescription-surf"]:
            mock.get(f"{APICARTO_BASE}{path}").mock(
                side_effect=httpx.ConnectError("timeout")
            )

        resp = await client.get(
            "/v1/urbanisme",
            params={"lat": 48.8566, "lon": 2.3522},
        )

    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
    assert resp.headers["Retry-After"] == "60"


@pytest.mark.unit
async def test_router_503_body_does_not_leak_upstream_error(client):
    """503 response body must not contain raw upstream error text (T-22-02-I)."""
    with respx.mock(assert_all_called=False) as mock:
        for path in ["/zone-urba", "/prescription-surf"]:
            mock.get(f"{APICARTO_BASE}{path}").mock(
                side_effect=httpx.ConnectError("ConnectError raw upstream detail")
            )

        resp = await client.get(
            "/v1/urbanisme",
            params={"lat": 48.8566, "lon": 2.3522},
        )

    assert resp.status_code == 503
    body_text = resp.text
    # Must not leak raw upstream error strings into client response
    assert "ConnectError" not in body_text
    assert "apicarto.ign.fr" not in body_text
    # Generic French message must be present
    assert "temporairement indisponible" in body_text


@pytest.mark.unit
async def test_router_200_with_partial_data_when_one_upstream_ok(client):
    """GET /v1/urbanisme → 200 + partial data when 1 of 2 upstreams responds (D-09)."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{APICARTO_BASE}/zone-urba").mock(
            return_value=_json_response(ZONE_URBA_RESPONSE)
        )
        mock.get(f"{APICARTO_BASE}/prescription-surf").mock(
            side_effect=httpx.ConnectError("timeout")
        )

        resp = await client.get(
            "/v1/urbanisme",
            params={"lat": 48.8566, "lon": 2.3522},
        )

    assert resp.status_code == 200
    data = resp.json()
    # zones_plu populated from zone-urba
    assert len(data["donnees"]["zones_plu"]) == 1
    # prescriptions empty (endpoint was down)
    assert data["donnees"]["prescriptions"] is None
    assert data["statut"] == "partiel"
