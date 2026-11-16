"""Unit tests for Georisques proxy: all-down → 503 + Retry-After, partial → 200."""

import contextlib

import httpx
import pytest
import respx

from app.adresse import risques
from app.common.exceptions import UpstreamUnavailableError
from app.georisques.client import query_all_risks, query_polluted_sites, query_rga

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GEORISQUES_BASE = "https://georisques.gouv.fr/api/v1"

# Minimal valid Georisques API response shapes
GASPAR_RESPONSE = {"data": [{"type": "INONDATION"}], "results": 1}
RGA_RESPONSE = {"code": "E3", "libelle": "Exposition forte"}
RADON_RESPONSE = {"data": [{"classe": 2}], "results": 1}
ICPE_RESPONSE = {"data": [], "results": 0}

# Query params used in tests
DEFAULT_PARAMS = {"lat": 47.2184, "lon": -1.5536}


# ---------------------------------------------------------------------------
# Helper: build a mock httpx.Response
# ---------------------------------------------------------------------------


def _json_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Return a real httpx.Response with JSON body."""
    return httpx.Response(status_code=status_code, json=data)


# ---------------------------------------------------------------------------
# Client-level tests (direct call to query_all_risks)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_client_raises_upstream_unavailable_when_all_down():
    """query_all_risks must raise UpstreamUnavailableError when all 4 endpoints fail."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GEORISQUES_BASE}/gaspar/risques").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{GEORISQUES_BASE}/rga").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{GEORISQUES_BASE}/radon").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{GEORISQUES_BASE}/installations_classees").mock(
            side_effect=httpx.ConnectError("timeout")
        )

        with pytest.raises(UpstreamUnavailableError):
            await query_all_risks(**DEFAULT_PARAMS)


@pytest.mark.unit
async def test_client_call_count_all_down():
    """All 4 endpoints must be attempted even when all fail (sanity: no early abort)."""
    with respx.mock(assert_all_called=True) as mock:
        for path in ["/gaspar/risques", "/rga", "/radon", "/installations_classees"]:
            mock.get(f"{GEORISQUES_BASE}{path}").mock(
                side_effect=httpx.ConnectError("timeout")
            )

        with pytest.raises(UpstreamUnavailableError):
            await query_all_risks(**DEFAULT_PARAMS)

        # assert_all_called=True in respx ensures every route was hit


@pytest.mark.unit
async def test_client_partial_degradation_returns_data():
    """1 endpoint OK + 3 down → no exception, returns partial data (D-09)."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GEORISQUES_BASE}/gaspar/risques").mock(
            return_value=_json_response(GASPAR_RESPONSE)
        )
        mock.get(f"{GEORISQUES_BASE}/rga").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{GEORISQUES_BASE}/radon").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{GEORISQUES_BASE}/installations_classees").mock(
            side_effect=httpx.ConnectError("timeout")
        )

        result = await query_all_risks(**DEFAULT_PARAMS)

    # gaspar responded → risques_naturels populated
    assert result["risques_naturels"] is not None
    # others down → None (graceful partial degradation)
    assert result["argiles"] is None
    assert result["radon"] is None
    assert result["icpe"] is None


# ---------------------------------------------------------------------------
# Router-level tests (via ASGI client)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_router_503_when_all_upstream_down(client):
    """GET /v1/risques → 503 + Retry-After when all Georisques endpoints fail."""
    with respx.mock(assert_all_called=False) as mock:
        for path in ["/gaspar/risques", "/rga", "/radon", "/installations_classees"]:
            mock.get(f"{GEORISQUES_BASE}{path}").mock(
                side_effect=httpx.ConnectError("timeout")
            )

        resp = await client.get(
            "/v1/risques",
            params={"lat": 47.2184, "lon": -1.5536},
        )

    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
    assert resp.headers["Retry-After"] == "60"


@pytest.mark.unit
async def test_router_503_body_does_not_leak_upstream_error(client):
    """503 response body must not contain raw upstream error text (T-22-02-I)."""
    with respx.mock(assert_all_called=False) as mock:
        for path in ["/gaspar/risques", "/rga", "/radon", "/installations_classees"]:
            mock.get(f"{GEORISQUES_BASE}{path}").mock(
                side_effect=httpx.ConnectError("ConnectError raw upstream detail")
            )

        resp = await client.get(
            "/v1/risques",
            params={"lat": 47.2184, "lon": -1.5536},
        )

    assert resp.status_code == 503
    body_text = resp.text
    # Must not leak raw upstream error strings into client response
    assert "ConnectError" not in body_text
    assert "georisques.gouv.fr" not in body_text
    # Generic French message must be present
    assert "temporairement indisponible" in body_text


@pytest.mark.unit
async def test_router_200_with_partial_data_when_one_upstream_ok(client):
    """GET /v1/risques → 200 + partial data when 1 of 4 upstreams responds (D-09)."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GEORISQUES_BASE}/gaspar/risques").mock(
            return_value=_json_response(GASPAR_RESPONSE)
        )
        mock.get(f"{GEORISQUES_BASE}/rga").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{GEORISQUES_BASE}/radon").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        mock.get(f"{GEORISQUES_BASE}/installations_classees").mock(
            side_effect=httpx.ConnectError("timeout")
        )

        resp = await client.get(
            "/v1/risques",
            params={"lat": 47.2184, "lon": -1.5536},
        )

    assert resp.status_code == 200
    data = resp.json()
    # At least the risques_naturels field should be populated from gaspar
    assert data["donnees"]["risques_naturels"] is not None


# ---------------------------------------------------------------------------
# query_rga: landmark ③ of the address page reads /rga only (API CR-01)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_query_rga_calls_the_rga_endpoint_only():
    from app.georisques.client import query_rga

    # respx refuses any request without a route: GASPAR, radon and ICPE
    # would raise here.
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{GEORISQUES_BASE}/rga").mock(
            return_value=_json_response(
                {"codeExposition": "1", "exposition": "Exposition faible"}
            )
        )
        result = await query_rga(**DEFAULT_PARAMS)

    assert result == {"codeExposition": "1", "exposition": "Exposition faible"}
    assert route.calls.last.request.url.params["latlon"] == "-1.5536,47.2184"


@pytest.mark.unit
@pytest.mark.parametrize(
    "reponse",
    [
        {"side_effect": httpx.ConnectError("down")},
        {"side_effect": httpx.ReadTimeout("slow")},
        {"return_value": httpx.Response(503, json={})},
        {"return_value": httpx.Response(200, json=[1, 2])},
    ],
    ids=["connect", "timeout", "status_503", "not_an_object"],
)
async def test_query_rga_failure_is_an_upstream_outage(reponse):
    from app.georisques.client import query_rga

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GEORISQUES_BASE}/rga").mock(**reponse)
        with pytest.raises(UpstreamUnavailableError):
            await query_rga(**DEFAULT_PARAMS)


@pytest.mark.unit
async def test_query_rga_empty_answer_is_no_publication_not_an_outage():
    # Observed 2026-11-05 at 31 boulevard des Exemples, Nantes: 200 with no body.
    from app.georisques.client import query_rga

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{GEORISQUES_BASE}/rga").mock(
            return_value=httpx.Response(200, content=b"")
        )
        assert await query_rga(**DEFAULT_PARAMS) is None


@pytest.mark.unit
async def test_query_rga_is_bounded_by_its_deadline(monkeypatch):
    import asyncio

    from app.georisques import client

    async def lent(_request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"exposition": "Exposition faible"})

    monkeypatch.setattr(client, "RGA_DEADLINE_S", 0.05)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{GEORISQUES_BASE}/rga").mock(side_effect=lent)
        with pytest.raises(UpstreamUnavailableError):
            await client.query_rga(**DEFAULT_PARAMS)


# The upstream drops the connection when gzip is offered (httpx default, observed
# 2026-10-26): every Georisques client must ask for an identity body.
@pytest.mark.unit
@pytest.mark.parametrize(
    "appel",
    [
        lambda: query_all_risks(**DEFAULT_PARAMS),
        lambda: query_rga(**DEFAULT_PARAMS),
        lambda: query_polluted_sites(**DEFAULT_PARAMS),
        lambda: risques.lire_risques_commune("85191"),
    ],
    ids=[
        "query_all_risks",
        "query_rga",
        "query_polluted_sites",
        "lire_risques_commune",
    ],
)
async def test_georisques_requests_ask_for_identity_encoding(appel):
    envoyes: list[str] = []

    def repondre(request: httpx.Request) -> httpx.Response:
        envoyes.append(request.headers.get("accept-encoding", ""))
        if request.url.path.endswith("/rga"):
            return _json_response(RGA_RESPONSE)
        return _json_response({"data": [], "results": 0})

    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(side_effect=repondre)
        # The payload shape does not matter here, only the request headers.
        with contextlib.suppress(UpstreamUnavailableError):
            await appel()

    assert envoyes, "no Georisques request was sent"
    assert all(valeur == "identity" for valeur in envoyes), envoyes
