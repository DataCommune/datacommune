"""Requests without any key (Ianis, 2026-11-12): open by configuration, per visitor IP.

Off by default: without ``KEYLESS_ACCESS_ENABLED`` a request with neither key nor
session keeps its 401. When on, it shares the per-IP buckets of the public site key,
so alternating between the two never doubles a visitor's allowance. The real
limiter is played; no database is touched.
"""

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import rate_limit
from app.auth import dependencies
from app.common.exceptions import ErreurApiError
from app.config import settings

pytestmark = pytest.mark.unit


def _request(ip: str) -> Request:
    return Request({"type": "http", "client": (ip, 40000), "headers": []})


@pytest.mark.asyncio
async def test_keyless_request_is_refused_by_default():
    assert settings.keyless_access_enabled is False
    with pytest.raises(ErreurApiError) as refused:
        await dependencies.check_quota_hybrid(_request("198.51.100.20"), None, None)
    assert refused.value.code == 401


@pytest.mark.asyncio
async def test_keyless_request_uses_the_per_ip_buckets(monkeypatch):
    monkeypatch.setattr(settings, "keyless_access_enabled", True)
    monkeypatch.setattr(settings, "anon_rate_limit_per_ip_minute", 2)
    reserve = AsyncMock()
    monkeypatch.setattr(dependencies, "reserve_quota", reserve)

    req = _request("198.51.100.21")
    await dependencies.check_quota_hybrid(req, None, None)
    assert req.state.api_key_id is None
    await dependencies.check_quota_hybrid(_request("198.51.100.21"), None, None)

    with pytest.raises(ErreurApiError) as refused:
        await dependencies.check_quota_hybrid(_request("198.51.100.21"), None, None)
    assert refused.value.code == 429
    assert int(refused.value.headers["Retry-After"]) > 0
    # Same bucket as the public site key, never a bucket of its own.
    assert len(rate_limit._buckets["anon:ip:198.51.100.21"]) == 2
    # No key row exists for a keyless call: nothing is reserved in the database.
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyless_daily_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "keyless_access_enabled", True)
    monkeypatch.setattr(settings, "anon_rate_limit_per_ip_day", 1)
    await dependencies.check_quota_hybrid(_request("198.51.100.22"), None, None)

    with pytest.raises(ErreurApiError) as refused:
        await dependencies.check_quota_hybrid(_request("198.51.100.22"), None, None)
    assert refused.value.code == 429
    assert refused.value.message == "Limite quotidienne atteinte"


@pytest.mark.asyncio
async def test_keyless_ipv6_visitor_cannot_rotate_inside_its_64(monkeypatch):
    # One machine owns a whole /64: changing the address inside it must not open
    # a fresh bucket (same identity as the map and report buckets, cle_visiteur).
    monkeypatch.setattr(settings, "keyless_access_enabled", True)
    monkeypatch.setattr(settings, "anon_rate_limit_per_ip_minute", 2)
    await dependencies.check_quota_hybrid(_request("2001:db8:7:1::1"), None, None)
    await dependencies.check_quota_hybrid(_request("2001:db8:7:1::2"), None, None)

    with pytest.raises(ErreurApiError) as refused:
        await dependencies.check_quota_hybrid(
            _request("2001:db8:7:1:ffff::3"), None, None
        )
    assert refused.value.code == 429
    # Another /64 is another visitor.
    await dependencies.check_quota_hybrid(_request("2001:db8:7:2::1"), None, None)
