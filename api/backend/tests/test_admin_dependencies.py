"""Tests for admin dependencies: require_admin — 403, 429, order of checks.

Covers:
- 403 returned for non-admin users (before rate check)
- 200 returned for admin users with a valid endpoint
- 429 returned for admin after 50 requests in the same rate window
- Non-admin never gets 429 (403 fires first, no rate bucket consumed)
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app
from app.rate_limit import _check_rate

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _make_user(uid: int = 1, is_admin: bool = False) -> UserInfo:
    return UserInfo(
        id=uid,
        email=f"user{uid}@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=is_admin,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _mock_db() -> AsyncMock:
    """Return a mock async DB session that responds to list/detail calls.

    db.execute is async (awaitable). The result it returns must be a plain
    MagicMock so that .mappings().all() is a sync call chain, not a coroutine.
    """
    db = AsyncMock()

    # list_admin_users: execute #1 = rows, execute #2 = count
    # rows_result is a MagicMock (not AsyncMock) so .mappings().all() is sync
    rows_result = MagicMock()
    rows_result.mappings.return_value.all.return_value = []

    count_result = MagicMock()
    count_result.scalar_one.return_value = 0

    db.execute.side_effect = [rows_result, count_result]
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def admin_client():
    """AsyncClient where get_current_user returns an admin user."""
    user = _make_user(uid=42, is_admin=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
async def non_admin_client():
    """AsyncClient where get_current_user returns a non-admin user."""
    user = _make_user(uid=99, is_admin=False)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_require_admin_returns_403_for_non_admin(non_admin_client):
    """Non-admin user gets 403 from GET /admin/users."""
    resp = await non_admin_client.get("/v1/admin/users")
    assert resp.status_code == 403
    body = resp.json()
    assert "Droits administrateur requis" in body.get("detail", "")


async def test_require_admin_allows_admin(admin_client):
    """Admin user gets 200 from GET /admin/users."""
    resp = await admin_client.get("/v1/admin/users")
    assert resp.status_code == 200


async def test_require_admin_rate_limit_429(admin_client):
    """After 50 rapid admin requests, the 51st returns 429.

    We fill the rate bucket directly using _check_rate to avoid 50 real HTTP
    calls, then verify the endpoint returns 429 on the next request.
    """
    admin_id = 42
    bucket = f"admin:{admin_id}"

    # Exhaust the 50-request budget by calling _check_rate directly
    for _ in range(50):
        _check_rate(bucket, 50)

    # Next request via the actual endpoint should hit 429
    resp = await admin_client.get("/v1/admin/users")
    assert resp.status_code == 429


async def test_non_admin_hits_403_not_429(non_admin_client):
    """Non-admin users always get 403, never 429 (rate check follows is_admin check).

    Flood the non-admin user's bucket, assert 403 persists (not 429).
    """
    uid = 99
    bucket = f"admin:{uid}"

    # Pre-fill the bucket as if 100 requests were made
    for _ in range(100):
        _check_rate(bucket, 50)

    # Should still 403, not 429 — is_admin check fires first
    resp = await non_admin_client.get("/v1/admin/users")
    assert resp.status_code == 403
    body = resp.json()
    assert "Droits administrateur requis" in body.get("detail", "")
