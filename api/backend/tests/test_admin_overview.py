"""Phase 17 — /admin/overview + /admin/users/{id}/heatmap tests.

Covers: 403 non-admin, overview shape + explicitly unavailable MRR,
heatmap shape + retention_days, heatmap 404 missing user,
heatmap empty days for user with no activity.

Pattern: AsyncMock + dependency_overrides + ASGITransport — no real DB.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.admin.schemas import AdminOverviewResponse
from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("unsupported_amount", [0, 39])
def test_overview_schema_rejects_unsupported_revenue(unsupported_amount):
    with pytest.raises(ValidationError):
        AdminOverviewResponse(
            users_count=1,
            active_keys_count=0,
            requests_24h=0,
            mrr_eur_estimated=unsupported_amount,
            mrr_status="unavailable",
            mrr_reason="billing_amounts_not_persisted",
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_ADMIN_ID = 1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_admin(uid: int = _ADMIN_ID, is_admin: bool = True) -> UserInfo:
    return UserInfo(
        id=uid,
        email=f"admin{uid}@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=is_admin,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _DictMapping:
    """Minimal mapping that supports dict() via keys() + __getitem__."""

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)


def _scalar(value) -> MagicMock:
    """MagicMock result: .scalar_one() and .scalar_one_or_none() return value."""
    r = MagicMock()
    r.scalar_one.return_value = value
    r.scalar_one_or_none.return_value = value
    return r


def _mappings(rows: list[dict]) -> MagicMock:
    """MagicMock result: .mappings().all() returns [_DictMapping, ...]."""
    r = MagicMock()
    r.mappings.return_value.all.return_value = [_DictMapping(row) for row in rows]
    return r


def _fake_db(*side_effects) -> AsyncMock:
    """DB mock: consecutive execute() calls return the provided side_effects."""
    db = AsyncMock()
    db.execute.side_effect = list(side_effects)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests — GET /admin/overview
# ---------------------------------------------------------------------------


async def test_admin_overview_403_non_admin():
    """Non-admin user gets 403 on GET /admin/overview."""
    app.dependency_overrides[get_current_user] = lambda: _make_admin(is_admin=False)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/overview")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


async def test_admin_overview_shape_and_unavailable_mrr():
    """Operational counters do not imply a monetary total."""
    db = _fake_db(
        _scalar(42),  # users_count
        _scalar(7),  # active_keys_count
        _scalar(1234),  # requests_24h
    )

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/overview")
        assert r.status_code == 200
        data = r.json()
        assert data["users_count"] == 42
        assert data["active_keys_count"] == 7
        assert data["requests_24h"] == 1234
        assert data["mrr_eur_estimated"] is None
        assert data["mrr_status"] == "unavailable"
        assert data["mrr_reason"] == "billing_amounts_not_persisted"
    finally:
        app.dependency_overrides.clear()


async def test_admin_overview_mrr_unavailable_even_with_no_subscriptions():
    """Missing local billing evidence never becomes a false zero."""
    db = _fake_db(
        _scalar(10),  # users_count
        _scalar(3),  # active_keys_count
        _scalar(500),  # requests_24h
    )

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/overview")
        assert r.status_code == 200
        assert r.json()["mrr_eur_estimated"] is None
    finally:
        app.dependency_overrides.clear()


async def test_admin_overview_no_commit():
    """GET /admin/overview must NOT call db.commit (read-only endpoint)."""
    db = _fake_db(
        _scalar(0),
        _scalar(0),
        _scalar(0),
    )

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/overview")
        assert r.status_code == 200
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests — GET /admin/users/{user_id}/heatmap
# ---------------------------------------------------------------------------


async def test_admin_user_heatmap_404_missing_user():
    """GET /admin/users/{id}/heatmap returns 404 when user does not exist."""
    db = _fake_db(_scalar(None))  # scalar_one_or_none returns None = no user

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/users/999999/heatmap")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_admin_user_heatmap_shape():
    """GET /admin/users/{id}/heatmap returns days + retention_days=7."""
    # The router calls row["day"].isoformat() — day must be a date object
    day_rows = [
        {"day": date(2026, 4, 15), "count": 100},
        {"day": date(2026, 4, 14), "count": 50},
    ]
    db = _fake_db(
        _scalar(42),  # user existence check
        _mappings(day_rows),  # date group query
    )

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/users/42/heatmap")
        assert r.status_code == 200
        data = r.json()
        assert data["retention_days"] == 7
        assert len(data["days"]) == 2
        # days are sorted DESC by DB — first row is most recent
        assert data["days"][0]["count"] == 100
        assert data["days"][1]["count"] == 50
    finally:
        app.dependency_overrides.clear()


async def test_admin_user_heatmap_empty_when_no_activity():
    """Heatmap for existing user with no activity returns days=[], retention_days=7."""
    db = _fake_db(
        _scalar(7),  # user exists
        _mappings([]),  # no activity rows
    )

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/users/7/heatmap")
        assert r.status_code == 200
        data = r.json()
        assert data["days"] == []
        assert data["retention_days"] == 7
    finally:
        app.dependency_overrides.clear()
