"""KI-P12: every API response carries X-Robots-Tag: noindex, nofollow."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app

pytestmark = pytest.mark.unit

_NO_INDEX = "noindex, nofollow"


@pytest.fixture
async def raw_client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_success_and_unknown_route_are_noindex(raw_client):
    live = await raw_client.get("/v1/health/live")
    missing = await raw_client.get("/v1/no-such-route")

    assert live.status_code == 200
    assert live.headers["X-Robots-Tag"] == _NO_INDEX
    assert missing.status_code == 404
    assert missing.headers["X-Robots-Tag"] == _NO_INDEX


async def test_cors_preflight_is_noindex(raw_client):
    preflight = await raw_client.options(
        "/v1/dvf/adresse",
        headers={
            "Origin": settings.cors_origins[0],
            "Access-Control-Request-Method": "GET",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["X-Robots-Tag"] == _NO_INDEX


async def test_rate_limit_refusal_is_noindex(raw_client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_ip", 1)
    await raw_client.get("/v1/no-such-route")
    refused = await raw_client.get("/v1/no-such-route")

    assert refused.status_code == 429
    assert refused.headers["X-Robots-Tag"] == _NO_INDEX
