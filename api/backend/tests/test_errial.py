"""Unit tests for ERRIAL proxy: TTL cache + 503 fallback."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.common.exceptions import UpstreamUnavailableError
from app.errial.client import (
    _avis_cache,
    _resolve_cache,
    query_avis,
    resolve_parcelle_by_coords,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear ERRIAL caches between tests."""
    _resolve_cache.clear()
    _avis_cache.clear()
    yield
    _resolve_cache.clear()
    _avis_cache.clear()


# -- Minimal valid responses for mocking ------------------------------------

CADASTRE_FEATURE = {
    "type": "Feature",
    "properties": {"code": "000-AB-0069", "commune": "75101"},
}
CADASTRE_RESPONSE = [json.dumps(CADASTRE_FEATURE)]

AVIS_RESPONSE = {
    "summary": {
        "adresse": "1 Rue de la Paix",
        "codeParcelle": "000-AB-0069@75101",
        "commune": {
            "codeINSEE": "75101",
            "nomCommune": "Paris 1er",
            "codePostal": "75001",
            "codeDepartement": "75",
        },
    },
    "planPreventionRisquesDTOs": [],
    "azis": [],
    "niveauArgile": 0,
    "hasCentraleNucleaire": False,
    "hasReculTraitCote": False,
    "hasOLD": False,
    "hasZoneUrbaine": True,
    "zonePlanExpositionBruit": None,
    "geogCanalisations": [],
    "hasError": False,
}


def _mock_response(data, status_code=200):
    """Build a mock httpx.Response (json() is sync, so use MagicMock)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


# -- Cache tests ------------------------------------------------------------


@pytest.mark.unit
async def test_query_avis_cached():
    """Second identical call should be served from cache (httpx called once)."""
    mock_resp = _mock_response(AVIS_RESPONSE)

    with patch("app.errial.client.httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.get.return_value = mock_resp
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        r1 = await query_avis("75101", "000-AB-0069@75101")
        r2 = await query_avis("75101", "000-AB-0069@75101")

        assert r1 == AVIS_RESPONSE
        assert r2 == AVIS_RESPONSE
        # httpx should only be called once (second was cache hit)
        assert ctx.get.call_count == 1


@pytest.mark.unit
async def test_avis_cache_miss_then_hit_and_purge():
    """D-06: prove the ALREADY-PRESENT avis cache: empty -> miss populates one
    entry (one upstream call) -> hit serves without a second call -> state
    purges between tests (fixture) and is explicitly clearable.
    """
    # The autouse fixture cleared the cache before this test.
    assert len(_avis_cache) == 0

    mock_resp = _mock_response(AVIS_RESPONSE)
    with patch("app.errial.client.httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.get.return_value = mock_resp
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # miss: populates exactly one cache entry via one upstream call
        await query_avis("75101", "000-AB-0069@75101")
        assert ctx.get.call_count == 1
        assert len(_avis_cache) == 1

        # hit: same key served from cache, no additional upstream call
        await query_avis("75101", "000-AB-0069@75101")
        assert ctx.get.call_count == 1
        assert len(_avis_cache) == 1

    # purge proof
    _avis_cache.clear()
    assert len(_avis_cache) == 0


@pytest.mark.unit
async def test_query_avis_upstream_down_raises():
    """ConnectError should raise UpstreamUnavailableError, not be cached."""
    with patch("app.errial.client.httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.get.side_effect = httpx.ConnectError("Connection refused")
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(UpstreamUnavailableError):
            await query_avis("75101", "000-AB-0069@75101")

        # Call again -- should also raise (error NOT cached)
        with pytest.raises(UpstreamUnavailableError):
            await query_avis("75101", "000-AB-0069@75101")

        # httpx called twice (no caching of errors)
        assert ctx.get.call_count == 2


@pytest.mark.unit
async def test_resolve_parcelle_rounds_coords():
    """Coordinates should be rounded to 5 decimals before the API call."""
    mock_resp = _mock_response(CADASTRE_RESPONSE)

    with patch("app.errial.client.httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.get.return_value = mock_resp
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await resolve_parcelle_by_coords(48.856601234, 2.352200567)

        assert result == ("75101", "000-AB-0069@75101")
        # Check the URL used rounded coordinates
        call_url = ctx.get.call_args[0][0]
        assert "2.3522" in call_url
        assert "48.8566" in call_url


# -- Router tests -----------------------------------------------------------


@pytest.mark.unit
async def test_router_503_on_upstream_down(client):
    """GET /v1/errial should return 503 when upstream is down."""
    with patch(
        "app.errial.router.query_avis",
        side_effect=UpstreamUnavailableError("ERRIAL avis", "timeout"),
    ):
        resp = await client.get(
            "/v1/errial",
            params={"code_insee": "75101", "code_parcelle": "000-AB-0069"},
        )

    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


@pytest.mark.unit
async def test_router_returns_data(client):
    """GET /v1/errial should return 200 with valid data."""
    with patch(
        "app.errial.router.query_avis",
        new_callable=AsyncMock,
        return_value=AVIS_RESPONSE,
    ):
        resp = await client.get(
            "/v1/errial",
            params={"code_insee": "75101", "code_parcelle": "000-AB-0069"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["donnees"]["commune"]["code_insee"] == "75101"
