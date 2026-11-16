"""The public dc_anon_ key is limited per visitor IP, never per key.

Every visitor of the site sends the same key: a per-key bucket would make the
whole world share one plan allowance (10 req/min, 500 req/month on free).
The real limiter is played; only the database is replaced.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import rate_limit
from app.auth import dependencies
from app.auth.models import ApiKeyInfo
from app.billing.plans import UNLIMITED_QUOTA
from app.common.exceptions import ErreurApiError
from app.config import settings

pytestmark = pytest.mark.unit

_ANON_ID = 2
_LIVE_ID = 7


def _key(*, key_id: int, prefix: str | None, user_id: int | None = None):
    return ApiKeyInfo(
        id=key_id,
        name="frontend public anon key",
        quota_monthly=500,
        revoked=False,
        created_at=datetime.now(UTC),
        user_id=user_id,
        key_prefix=prefix,
    )


_ANON = _key(key_id=_ANON_ID, prefix="dc_anon_LbOh4Ghg...")


def _request(ip: str) -> Request:
    return Request({"type": "http", "client": (ip, 40000), "headers": []})


@pytest.fixture
def reserve(monkeypatch):
    mock = AsyncMock(return_value=10_000)
    monkeypatch.setattr(dependencies, "reserve_quota", mock)
    return mock


@pytest.mark.asyncio
async def test_two_visitors_of_the_anon_key_have_separate_counters(
    monkeypatch, reserve
):
    monkeypatch.setattr(settings, "anon_rate_limit_per_ip_minute", 3)
    for _ in range(3):
        await dependencies._enforce_quota(_request("198.51.100.1"), _ANON, None)

    with pytest.raises(ErreurApiError) as refused:
        await dependencies._enforce_quota(_request("198.51.100.1"), _ANON, None)
    assert refused.value.code == 429
    assert refused.value.message == "Limite de debit atteinte"
    # The frontend shows its anonymous rate-limit message only with Retry-After.
    assert int(refused.value.headers["Retry-After"]) > 0

    # Another visitor of the same key is untouched by the first one's burst.
    await dependencies._enforce_quota(_request("203.0.113.9"), _ANON, None)
    assert f"plan:{_ANON_ID}" not in rate_limit._buckets
    assert len(rate_limit._buckets["anon:ip:198.51.100.1"]) == 3
    assert len(rate_limit._buckets["anon:ip:203.0.113.9"]) == 1


@pytest.mark.asyncio
async def test_anon_visitor_over_the_daily_ceiling_gets_429(monkeypatch, reserve):
    monkeypatch.setattr(settings, "anon_rate_limit_per_ip_day", 2)
    for _ in range(2):
        await dependencies._enforce_quota(_request("198.51.100.2"), _ANON, None)

    with pytest.raises(ErreurApiError) as refused:
        await dependencies._enforce_quota(_request("198.51.100.2"), _ANON, None)
    assert refused.value.code == 429
    assert refused.value.message == "Limite quotidienne atteinte"
    assert refused.value.headers["Retry-After"]
    assert rate_limit._bucket_windows["anon:ip-day:198.51.100.2"] == 86_400


@pytest.mark.asyncio
async def test_anon_key_monthly_row_is_counted_but_never_blocks(reserve):
    # quota_monthly=500 and the row already at 10 000: no monthly refusal.
    result = await dependencies._enforce_quota(_request("198.51.100.3"), _ANON, None)

    assert result is _ANON
    reserve.assert_awaited_once()
    kwargs = reserve.await_args.kwargs
    assert kwargs["key_id"] == _ANON_ID
    assert kwargs["user_id"] is None
    assert kwargs["limit"] == UNLIMITED_QUOTA


@pytest.mark.parametrize(
    ("prefix", "user_id"),
    [
        ("dc_live_abcdefgh...", None),  # nominative CLI key
        (None, None),  # legacy key without stored prefix
        ("dc_anon_abcdefgh...", 42),  # owned key: never the shared public key
    ],
)
@pytest.mark.asyncio
async def test_other_keys_keep_their_per_key_plan_bucket(monkeypatch, prefix, user_id):
    key = _key(key_id=_LIVE_ID, prefix=prefix, user_id=user_id)
    monkeypatch.setattr(
        dependencies, "get_active_subscription", AsyncMock(return_value=None)
    )
    reserve = AsyncMock(return_value=None)
    monkeypatch.setattr(dependencies, "reserve_quota", reserve)

    for _ in range(10):  # free plan / 500-quota rate: 10 req/min per key
        with pytest.raises(ErreurApiError) as exhausted:
            await dependencies._enforce_quota(_request("198.51.100.4"), key, None)
        assert exhausted.value.message == "Quota mensuel epuise"
    assert reserve.await_args.kwargs["limit"] == 500

    with pytest.raises(ErreurApiError) as throttled:
        await dependencies._enforce_quota(_request("203.0.113.4"), key, None)
    assert throttled.value.message == "Limite de debit atteinte"
    assert len(rate_limit._buckets[f"plan:{_LIVE_ID}"]) == 10
    assert not [b for b in rate_limit._buckets if b.startswith("anon:")]


def test_purge_keeps_a_bucket_while_its_own_window_is_open(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(rate_limit, "_last_cleanup", clock[0])
    rate_limit._check_rate("ip:minute", 5)
    rate_limit._check_rate("anon:ip-day:day", 5, 86_400)

    clock[0] += 3 * 3600  # past the minute window, inside the day window
    rate_limit._check_rate("other", 5)

    assert "ip:minute" not in rate_limit._buckets
    assert len(rate_limit._buckets["anon:ip-day:day"]) == 1


def test_window_longer_than_a_day_is_refused():
    with pytest.raises(ValueError):
        rate_limit._check_rate("too-long", 1, 86_401)
