"""Tests for GET /admin/audit endpoint.

Covers: empty list, inserted rows, 4 filter axes, pagination, 403 for non-admin.

These are smoke-level tests: they override get_db with an AsyncMock that
returns controlled data, so no real DB connection is required.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_user(uid: int = 1, is_admin: bool = True) -> UserInfo:
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


def _make_audit_row(
    row_id: int = 1,
    admin_user_id: int = 1,
    admin_email: str | None = "admin@example.com",
    action: str = "grant_plan",
    target_type: str = "user",
    target_id: str = "42",
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "test reason",
    ip: str | None = "127.0.0.1",
    user_agent: str | None = "pytest",
    created_at: datetime | None = None,
) -> dict:
    return {
        "id": row_id,
        "admin_user_id": admin_user_id,
        "admin_email": admin_email,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "before": before or {"plan": "free"},
        "after": after or {"plan": "pro"},
        "reason": reason,
        "ip": ip,
        "user_agent": user_agent,
        "created_at": created_at or _NOW,
    }


def _db_returning(rows: list[dict], total: int) -> AsyncMock:
    """Create an AsyncMock DB session returning rows then total for two execute() calls.

    db.execute is async; its return value must be a plain MagicMock so that
    .mappings().all() is a sync call chain, not a coroutine chain.
    """
    db = AsyncMock()

    # First execute: rows — plain MagicMock so .mappings().all() is sync
    row_result = MagicMock()
    row_result.mappings.return_value.all.return_value = [_DictMapping(r) for r in rows]

    # Second execute: count — plain MagicMock
    count_result = MagicMock()
    count_result.scalar_one.return_value = total

    db.execute.side_effect = [row_result, count_result]
    return db


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _override_admin(uid: int = 1):
    user = _make_user(uid=uid, is_admin=True)
    app.dependency_overrides[get_current_user] = lambda: user


def _override_non_admin(uid: int = 99):
    user = _make_user(uid=uid, is_admin=False)
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture()
def audit_client():
    """Admin client with configurable DB mock."""
    _override_admin()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    yield client
    import asyncio

    asyncio.get_event_loop().run_until_complete(client.aclose())
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_audit_list_empty():
    """No rows → total=0, rows=[]."""
    _override_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning([], 0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/audit")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["rows"] == []


async def test_audit_list_returns_inserted_rows():
    """Two inserted rows → GET returns 2 with correct fields."""
    row1 = _make_audit_row(row_id=1, action="grant_plan", target_id="10")
    row2 = _make_audit_row(row_id=2, action="toggle_is_admin", target_id="20")

    _override_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning([row1, row2], 2)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/audit")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["rows"]) == 2
    assert body["rows"][0]["action"] == "grant_plan"
    assert body["rows"][0]["admin_email"] == "admin@example.com"
    assert isinstance(body["rows"][0]["before"], dict)
    assert isinstance(body["rows"][0]["after"], dict)
    assert body["rows"][0]["ip"] == "127.0.0.1"
    assert body["rows"][1]["action"] == "toggle_is_admin"


async def test_audit_filter_by_action():
    """Filter action=grant_plan → only matching row returned."""
    row = _make_audit_row(row_id=1, action="grant_plan")

    _override_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning([row], 1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/audit?action=grant_plan")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["action"] == "grant_plan"


async def test_audit_filter_by_admin():
    """Filter admin_user_id → only rows by that admin."""
    row = _make_audit_row(row_id=1, admin_user_id=7, admin_email="admin7@example.com")

    _override_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning([row], 1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/audit?admin_user_id=7")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["admin_user_id"] == 7


async def test_audit_filter_by_target():
    """Filter target_type + target_id → matching row returned."""
    row = _make_audit_row(row_id=1, target_type="user", target_id="42")

    _override_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning([row], 1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/audit?target_type=user&target_id=42")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["target_type"] == "user"
    assert body["rows"][0]["target_id"] == "42"


async def test_audit_filter_by_date_range():
    """date_from / date_to filter — rows within range returned."""
    row = _make_audit_row(
        row_id=1, created_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)
    )

    _override_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning([row], 1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/v1/admin/audit?date_from=2026-03-01T00:00:00Z&date_to=2026-03-31T23:59:59Z"
        )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


async def test_audit_pagination():
    """page=3, page_size=20 → third page of 55 total rows returns 15 rows."""
    # Simulate 15 rows on page 3 (rows 41-55 of 55 total)
    rows_page3 = [_make_audit_row(row_id=i) for i in range(41, 56)]

    _override_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning(rows_page3, 55)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/audit?page=3&page_size=20")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 55
    assert len(body["rows"]) == 15
    assert body["page"] == 3
    assert body["page_size"] == 20


def test_end_of_day_normalization():
    """Pure-midnight date_to is bumped to end-of-day so whole-day events match."""
    from datetime import datetime as _dt

    from app.admin.audit import _end_of_day_if_midnight

    assert _end_of_day_if_midnight(None) is None
    # Midnight → 23:59:59.999999 same day
    assert _end_of_day_if_midnight(_dt(2026, 4, 15, 0, 0, 0)) == _dt(
        2026, 4, 15, 23, 59, 59, 999999
    )
    # Non-midnight → unchanged
    assert _end_of_day_if_midnight(_dt(2026, 4, 15, 14, 30, 0)) == _dt(
        2026, 4, 15, 14, 30, 0
    )
    # Non-zero microsecond is not midnight — leave alone
    assert _end_of_day_if_midnight(_dt(2026, 4, 15, 0, 0, 0, 1)) == _dt(
        2026, 4, 15, 0, 0, 0, 1
    )


async def test_audit_non_admin_403():
    """Non-admin user gets 403 on GET /admin/audit."""
    _override_non_admin()
    app.dependency_overrides[get_db] = lambda: _db_returning([], 0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/admin/audit")
    app.dependency_overrides.clear()

    assert resp.status_code == 403
    body = resp.json()
    assert "Droits administrateur requis" in body.get("detail", "")
