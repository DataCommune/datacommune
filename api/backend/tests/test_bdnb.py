"""Unit tests for BDNB proxy: TTL cache + 503 fallback."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bdnb.client import (
    _adresse_cache,
    _geocode_cache,
    _parcelle_cache,
    query_batiment_by_adresse,
    query_batiment_by_parcelle,
)
from app.common.exceptions import UpstreamUnavailableError


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear BDNB caches between tests."""
    _parcelle_cache.clear()
    _adresse_cache.clear()
    _geocode_cache.clear()
    yield
    _parcelle_cache.clear()
    _adresse_cache.clear()
    _geocode_cache.clear()


# -- Minimal valid responses for mocking ------------------------------------

BATIMENT_RESPONSE = [
    {
        "batiment_groupe_id": "bg-001",
        "code_commune_insee": "75102",
        "code_departement_insee": "75",
        "libelle_adr_principale_ban": "1 Rue de la Paix",
        "annee_construction": 1850,
        "classe_bilan_dpe": "D",
    }
]


def _mock_response(data, status_code=200):
    """Build a mock httpx.Response (json() is sync, so use MagicMock)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _mock_429_response():
    """Build a mock httpx.Response with 429 status."""
    resp = MagicMock()
    resp.status_code = 429
    resp.json.return_value = {"error": "rate limited"}
    return resp


def _setup_mock_client(mock_cls, mock_resp):
    """Configure AsyncClient context manager mock."""
    ctx = AsyncMock()
    ctx.get.return_value = mock_resp
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


# -- Cache tests ------------------------------------------------------------


@pytest.mark.unit
async def test_query_batiment_by_parcelle_cached():
    """Second identical call should be served from cache (httpx called once)."""
    mock_resp = _mock_response(BATIMENT_RESPONSE)

    with patch("app.bdnb.client.httpx.AsyncClient") as mock_cls:
        ctx = _setup_mock_client(mock_cls, mock_resp)

        r1 = await query_batiment_by_parcelle("75101000AB0001")
        r2 = await query_batiment_by_parcelle("75101000AB0001")

        assert r1 == BATIMENT_RESPONSE
        assert r2 == BATIMENT_RESPONSE
        # httpx should only be called once (second was cache hit)
        assert ctx.get.call_count == 1


@pytest.mark.unit
async def test_each_request_leaving_for_the_api_is_counted_without_its_key(caplog):
    """04.7-19: one ``bdnb_upstream_call`` line per request that leaves (a cache
    hit leaves nothing), named by its function, never by its parameter."""
    mock_resp = _mock_response(BATIMENT_RESPONSE)

    with (
        patch("app.bdnb.client.httpx.AsyncClient") as mock_cls,
        caplog.at_level("INFO", logger="app.bdnb.client"),
    ):
        _setup_mock_client(mock_cls, mock_resp)
        await query_batiment_by_parcelle("75101000AB0001")
        await query_batiment_by_parcelle("75101000AB0001")
        await query_batiment_by_adresse("75102_7560_00001")

    appels = [r for r in caplog.records if r.getMessage() == "bdnb_upstream_call"]
    assert [r.fonction for r in appels] == [
        "query_batiment_by_parcelle",
        "query_batiment_by_adresse",
    ]
    for record in appels:
        assert "75101000AB0001" not in str(record.__dict__)
        assert "75102_7560_00001" not in str(record.__dict__)


@pytest.mark.unit
async def test_query_batiment_by_adresse_cached():
    """Second identical call should be served from cache (httpx called once)."""
    mock_resp = _mock_response(BATIMENT_RESPONSE)

    with patch("app.bdnb.client.httpx.AsyncClient") as mock_cls:
        ctx = _setup_mock_client(mock_cls, mock_resp)

        r1 = await query_batiment_by_adresse("75102_7560_00001")
        r2 = await query_batiment_by_adresse("75102_7560_00001")

        assert r1 == BATIMENT_RESPONSE
        assert r2 == BATIMENT_RESPONSE
        assert ctx.get.call_count == 1


@pytest.mark.unit
async def test_parcelle_cache_miss_then_hit_and_purge():
    """D-06: prove the ALREADY-PRESENT parcelle cache: empty -> miss populates
    one entry (one upstream call) -> hit serves without a second call ->
    state purges between tests (fixture) and is explicitly clearable.
    """
    # The autouse fixture cleared the cache before this test.
    assert len(_parcelle_cache) == 0

    mock_resp = _mock_response(BATIMENT_RESPONSE)
    with patch("app.bdnb.client.httpx.AsyncClient") as mock_cls:
        ctx = _setup_mock_client(mock_cls, mock_resp)

        # miss: populates exactly one cache entry via one upstream call
        await query_batiment_by_parcelle("75101000AB0001")
        assert ctx.get.call_count == 1
        assert len(_parcelle_cache) == 1

        # hit: same key served from cache, no additional upstream call
        await query_batiment_by_parcelle("75101000AB0001")
        assert ctx.get.call_count == 1
        assert len(_parcelle_cache) == 1

    # purge proof
    _parcelle_cache.clear()
    assert len(_parcelle_cache) == 0


@pytest.mark.unit
async def test_query_batiment_429_raises():
    """429 should raise UpstreamUnavailableError, NOT be cached."""
    mock_resp = _mock_429_response()

    with patch("app.bdnb.client.httpx.AsyncClient") as mock_cls:
        ctx = _setup_mock_client(mock_cls, mock_resp)

        with pytest.raises(UpstreamUnavailableError):
            await query_batiment_by_parcelle("75101000AB0001")

        # Call again — should also raise (error NOT cached)
        with pytest.raises(UpstreamUnavailableError):
            await query_batiment_by_parcelle("75101000AB0001")

        # httpx called twice (no caching of errors)
        assert ctx.get.call_count == 2


# -- Router tests -----------------------------------------------------------


@pytest.mark.unit
async def test_router_503_on_upstream_down(client):
    """GET /v1/bdnb should return 503 when upstream is down."""
    with patch(
        "app.bdnb.router.query_batiment_by_parcelle",
        side_effect=UpstreamUnavailableError("BDNB", "timeout"),
    ):
        resp = await client.get(
            "/v1/bdnb",
            params={"parcelle_id": "75101000AB0001"},
        )

    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


@pytest.mark.unit
async def test_router_returns_data_parcelle(client):
    """GET /v1/bdnb should return 200 with valid parcelle data."""
    with patch(
        "app.bdnb.router.query_batiment_by_parcelle",
        new_callable=AsyncMock,
        return_value=BATIMENT_RESPONSE,
    ):
        resp = await client.get(
            "/v1/bdnb",
            params={"parcelle_id": "75101000AB0001"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["donnees"]["total"] is None
    assert data["donnees"]["nombre_retournes"] == 1
    assert data["donnees"]["couverture"]["complet"] is None
    assert data["donnees"]["batiments"][0]["batiment_groupe_id"] == "bg-001"
