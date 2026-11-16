"""Tests for admin users endpoints: list, detail, grant-plan, toggle-admin.

Covers: pagination, search, filters, self-protection 422, grant-plan happy path,
re-grant upsert, toggle happy path.

All tests use dependency_overrides to bypass DB and auth — no real DB needed.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.billing.plans import get_quota_for_plan
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_ADMIN_ID = 1
_TARGET_ID = 42


def _make_user(uid: int = _ADMIN_ID, is_admin: bool = True) -> UserInfo:
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
    """Minimal mapping compatible with dict()."""

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)


def _user_row(
    uid: int = _TARGET_ID,
    email: str = "user@example.com",
    plan: str = "free",
    is_admin: bool = False,
    email_verified: bool = True,
) -> dict:
    return {
        "id": uid,
        "email": email,
        "name": None,
        "is_admin": is_admin,
        "email_verified": email_verified,
        "created_at": _NOW,
        "last_login": None,
        "plan": plan,
        "quota_used": 0,
        "quota_monthly": get_quota_for_plan(plan),
        "api_keys_count": 0,
    }


def _make_list_db(rows: list[dict], total: int) -> AsyncMock:
    """Mock DB for list_admin_users — two executes: rows, count.

    db.execute returns are plain MagicMock so .mappings().all() is a sync chain.
    """
    db = AsyncMock()

    rows_result = MagicMock()
    rows_result.mappings.return_value.all.return_value = [_DictMapping(r) for r in rows]

    count_result = MagicMock()
    count_result.scalar_one.return_value = total

    db.execute.side_effect = [rows_result, count_result]
    return db


def _make_detail_db(
    user: dict,
    api_keys: list[dict] | None = None,
    subscription: dict | None = None,
    recent_activity: list[dict] | None = None,
) -> AsyncMock:
    """Mock DB for get_admin_user_detail — four executes: user, keys, sub, activity.

    db.execute returns are plain MagicMock so .mappings().first()/.all() are sync.
    """
    db = AsyncMock()

    user_result = MagicMock()
    user_result.mappings.return_value.first.return_value = _DictMapping(user)

    keys_result = MagicMock()
    keys_result.mappings.return_value.all.return_value = [
        _DictMapping(k) for k in (api_keys or [])
    ]

    sub_result = MagicMock()
    sub_result.mappings.return_value.first.return_value = (
        _DictMapping(subscription) if subscription else None
    )

    activity_result = MagicMock()
    activity_result.mappings.return_value.all.return_value = [
        _DictMapping(a) for a in (recent_activity or [])
    ]

    db.execute.side_effect = [user_result, keys_result, sub_result, activity_result]
    return db


def _make_grant_db(*, exists: bool = True, current_plan: str = "free") -> AsyncMock:
    """Mock DB for admin_grant_plan.

    Executes: before_snapshot, upsert_sub, update_keys, audit.
    """
    db = AsyncMock()

    before_result = MagicMock()
    before_result.mappings.return_value.first.return_value = (
        _DictMapping(
            {"plan": current_plan, "stripe_subscription_id": None, "status": None}
        )
        if exists
        else None
    )

    # Remaining execute calls (upsert, update keys, audit INSERT) return generic mocks
    generic = MagicMock()
    written_plan = current_plan

    async def execute(statement, params=None):
        nonlocal written_plan
        sql = str(statement)
        if "pg_advisory_xact_lock" in sql:
            return generic
        if "FROM users u" in sql:
            return before_result
        if "INSERT INTO subscriptions" in sql:
            written_plan = params["plan"]
        if "FROM subscriptions" in sql and "SELECT id, user_id" in sql:
            result = MagicMock()
            result.mappings.return_value.first.return_value = {
                "plan_name": written_plan
            }
            return result
        return generic

    db.execute.side_effect = execute
    db.commit = AsyncMock()
    return db


def _make_toggle_db(*, exists: bool = True, plan_name: str | None = None) -> AsyncMock:
    """Mock DB for admin_toggle_is_admin — executes: before, update, audit."""
    db = AsyncMock()

    before_result = MagicMock()
    before_result.mappings.return_value.first.return_value = (
        _DictMapping({"is_admin": False, "plan_name": plan_name}) if exists else None
    )

    update_result = MagicMock()
    update_result.rowcount = 1 if exists else 0

    generic = MagicMock()
    db.execute.side_effect = [before_result, update_result, generic]
    db.commit = AsyncMock()
    return db


def _override(uid: int = _ADMIN_ID, is_admin: bool = True, db_factory=None):
    user = _make_user(uid=uid, is_admin=is_admin)
    app.dependency_overrides[get_current_user] = lambda: user
    if db_factory is not None:
        app.dependency_overrides[get_db] = db_factory


async def _get(path: str, params: dict | None = None) -> tuple:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(path, params=params)
    return resp


async def _post(path: str, json: dict) -> tuple:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(path, json=json)
    return resp


async def _patch(path: str, json: dict):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.patch(path, json=json)
    return resp


# ---------------------------------------------------------------------------
# List users
# ---------------------------------------------------------------------------


async def test_list_users_returns_paginated_rows():
    """Seed 3 users → GET /admin/users returns total=3, 3 rows."""
    rows = [
        _user_row(uid=1, email="a@example.com"),
        _user_row(uid=2, email="b@example.com"),
        _user_row(uid=3, email="c@example.com"),
    ]
    _override(db_factory=lambda: _make_list_db(rows, 3))
    resp = await _get("/v1/admin/users")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["rows"]) == 3


async def test_list_users_search_email():
    """Search 'alice' → only alice row returned."""
    alice_row = _user_row(uid=5, email="alice@example.com")
    _override(db_factory=lambda: _make_list_db([alice_row], 1))
    resp = await _get("/v1/admin/users", params={"search": "alice"})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert "alice" in body["rows"][0]["email"]


async def test_list_users_filter_plan():
    """Filter plan=pro → only pro rows returned."""
    pro_row = _user_row(uid=10, plan="pro")
    _override(db_factory=lambda: _make_list_db([pro_row], 1))
    resp = await _get("/v1/admin/users", params={"plan": "pro"})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["plan"] == "pro"


async def test_list_users_filter_is_admin():
    """Filter is_admin=true → admin rows only."""
    admin_row = _user_row(uid=11, is_admin=True)
    _override(db_factory=lambda: _make_list_db([admin_row], 1))
    resp = await _get("/v1/admin/users", params={"is_admin": "true"})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["is_admin"] is True


async def test_list_users_filter_email_verified():
    """Filter email_verified=false → unverified rows only."""
    unverified_row = _user_row(uid=12, email_verified=False)
    _override(db_factory=lambda: _make_list_db([unverified_row], 1))
    resp = await _get("/v1/admin/users", params={"email_verified": "false"})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["email_verified"] is False


async def test_list_users_page_size_and_offset():
    """page_size=3, page=2 → rows 4-6 returned (simulated by mock returning 3 rows)."""
    rows = [_user_row(uid=i) for i in range(4, 7)]
    _override(db_factory=lambda: _make_list_db(rows, 10))
    resp = await _get("/v1/admin/users", params={"page": 2, "page_size": 3})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10
    assert len(body["rows"]) == 3
    assert body["page"] == 2
    assert body["page_size"] == 3


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


async def test_detail_returns_user_with_keys_and_subscription():
    """User detail endpoint returns FLAT user fields + api_keys + subscription.

    Regression guard: 15-VERIFICATION Gap 1 — frontend expects flat shape.
    Breaking this test means the drawer's detail.email / detail.id access
    will return undefined at runtime.
    """
    user = _user_row(uid=_TARGET_ID, plan="pro")
    key = {
        "id": 1,
        "prefix": "dc_live_",
        "label": "my key",
        "quota_used": 10,
        "quota_monthly": 5000,
        "last_used_at": None,
        "revoked": False,
        "is_default": True,
        "created_at": _NOW,
    }
    sub = {
        "plan_name": "pro",
        "status": "active",
        "current_period_end": None,
        "source": "manual_grant",
        "stripe_subscription_id": f"manual_grant_{_TARGET_ID}",
    }
    _override(db_factory=lambda: _make_detail_db(user, [key], sub))
    resp = await _get(f"/v1/admin/users/{_TARGET_ID}")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    # Flat user fields at root (no more body["user"])
    assert "user" not in body
    assert body["id"] == _TARGET_ID
    assert body["email"] == user["email"]
    assert body["is_admin"] == user["is_admin"]
    assert body["plan"] == "pro"
    # Nested relations still present
    assert len(body["api_keys"]) == 1
    assert body["subscription"]["plan_name"] == "pro"
    assert body["subscription"]["source"] == "manual_grant"
    expected_sub_id = f"manual_grant_{_TARGET_ID}"
    assert body["subscription"]["stripe_subscription_id"] == expected_sub_id
    # recent_activity is a list (shape tested separately)
    assert isinstance(body["recent_activity"], list)


async def test_detail_recent_activity_shape():
    """Regression guard: 15-VERIFICATION Gap 2.

    recent_activity entries MUST expose id, action, reason, created_at
    (real admin_audit_log columns) — NOT the old SQL aliases {action, at, detail}.
    Frontend uses act.id as Svelte each-block key; renaming breaks the drawer.
    """
    user = _user_row(uid=_TARGET_ID, plan="free")
    activity = [
        {
            "id": 42,
            "action": "grant_plan",
            "reason": "customer upgrade after demo call",
            "created_at": _NOW,
        },
        {
            "id": 41,
            "action": "toggle_is_admin",
            "reason": "promoted per CTO request",
            "created_at": _NOW,
        },
    ]
    _override(
        db_factory=lambda: _make_detail_db(user, [], None, recent_activity=activity)
    )
    resp = await _get(f"/v1/admin/users/{_TARGET_ID}")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["recent_activity"]) == 2
    first = body["recent_activity"][0]
    assert set(first.keys()) == {"id", "action", "reason", "created_at"}
    assert first["id"] == 42
    assert first["action"] == "grant_plan"
    assert first["reason"] == "customer upgrade after demo call"
    assert first["created_at"] is not None
    # Explicit negative assertions — old aliases must NOT appear
    assert "at" not in first
    assert "detail" not in first


async def test_detail_404_on_missing_user():
    """GET /admin/users/{id} for non-existent user → 404."""
    db = AsyncMock()
    # user query returns None — plain MagicMock for sync .mappings().first()
    user_result = MagicMock()
    user_result.mappings.return_value.first.return_value = None
    db.execute.return_value = user_result

    _override(db_factory=lambda: db)
    resp = await _get("/v1/admin/users/99999")
    app.dependency_overrides.clear()

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Grant-plan endpoint
# ---------------------------------------------------------------------------


async def test_grant_plan_happy_path():
    """POST grant-plan with valid plan + long enough reason → 200 correct response."""
    _override(uid=_ADMIN_ID, db_factory=_make_grant_db)
    resp = await _post(
        f"/v1/admin/users/{_TARGET_ID}/grant-plan",
        json={"plan": "pro", "reason": "support request for upgrade", "end_date": None},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["plan"] == "pro"
    assert body["user_id"] == _TARGET_ID
    assert body["after"]["plan"] == "pro"
    assert body["after"]["stripe_subscription_id"] == f"manual_grant_{_TARGET_ID}"
    assert body["after"]["quota_monthly"] == get_quota_for_plan("pro")


async def test_grant_plan_rejects_short_reason():
    """reason shorter than 10 chars → 422 validation error."""
    _override(uid=_ADMIN_ID, db_factory=_make_grant_db)
    resp = await _post(
        f"/v1/admin/users/{_TARGET_ID}/grant-plan",
        json={"plan": "pro", "reason": "short", "end_date": None},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422


async def test_grant_plan_self_returns_422():
    """Admin tries to grant plan to themselves → 422 with self-protection message."""
    _override(uid=_ADMIN_ID, db_factory=_make_grant_db)
    resp = await _post(
        f"/v1/admin/users/{_ADMIN_ID}/grant-plan",
        json={
            "plan": "pro",
            "reason": "should be rejected self-grant",
            "end_date": None,
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422
    body = resp.json()
    assert "cannot grant plan to yourself" in body.get("detail", "")


async def test_grant_plan_rejects_silent_downgrade():
    """Grant on a Pro user with lower plan and no force_downgrade → 422."""
    db = _make_grant_db(current_plan="business")
    _override(uid=_ADMIN_ID, db_factory=lambda: db)
    resp = await _post(
        f"/v1/admin/users/{_TARGET_ID}/grant-plan",
        json={"plan": "free", "reason": "customer cancelled support", "end_date": None},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422
    body = resp.json()
    assert (
        "Downgrade" in body.get("title", "")
        or "downgrade" in body.get("detail", "").lower()
    )
    # No commit because the downgrade check runs before any write
    assert db.commit.await_count == 0


async def test_grant_plan_rejects_internal_account():
    """Generic plan grants must not replace the protected internal plan."""
    db = _make_grant_db(current_plan="internal")
    _override(uid=_ADMIN_ID, db_factory=lambda: db)
    resp = await _post(
        f"/v1/admin/users/{_TARGET_ID}/grant-plan",
        json={
            "plan": "enterprise",
            "reason": "attempt to replace protected plan",
            "end_date": None,
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert db.commit.await_count == 0
    snapshot_sql = str(db.execute.await_args_list[1].args[0])
    assert "status = 'active'" in snapshot_sql
    assert "ORDER BY (plan_name = 'internal') DESC" in snapshot_sql


async def test_grant_plan_accepts_forced_downgrade():
    """Same downgrade with force_downgrade=True → 200."""
    db = _make_grant_db(current_plan="business")
    _override(uid=_ADMIN_ID, db_factory=lambda: db)
    resp = await _post(
        f"/v1/admin/users/{_TARGET_ID}/grant-plan",
        json={
            "plan": "free",
            "reason": "customer cancelled support",
            "end_date": None,
            "force_downgrade": True,
        },
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert db.commit.await_count == 1


async def test_grant_plan_missing_user_returns_404():
    """Granting a plan to an unknown user returns 404 and does not commit."""
    db = _make_grant_db(exists=False)
    _override(uid=_ADMIN_ID, db_factory=lambda: db)
    resp = await _post(
        "/v1/admin/users/99999/grant-plan",
        json={"plan": "pro", "reason": "support request for upgrade", "end_date": None},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 404
    assert db.commit.await_count == 0


async def test_grant_plan_re_grant_uses_on_conflict():
    """Second grant-plan call also returns 200 (upsert, no unique error)."""
    # Both calls go through, DB mock always succeeds
    _override(uid=_ADMIN_ID, db_factory=_make_grant_db)
    resp1 = await _post(
        f"/v1/admin/users/{_TARGET_ID}/grant-plan",
        json={
            "plan": "pro",
            "reason": "first grant for user upgrade",
            "end_date": None,
        },
    )
    app.dependency_overrides.clear()

    _override(uid=_ADMIN_ID, db_factory=_make_grant_db)
    resp2 = await _post(
        f"/v1/admin/users/{_TARGET_ID}/grant-plan",
        json={
            "plan": "business",
            "reason": "upgrade to business tier",
            "end_date": None,
        },
    )
    app.dependency_overrides.clear()

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Second call should reflect the new plan
    assert resp2.json()["plan"] == "business"


# ---------------------------------------------------------------------------
# Toggle-admin endpoint
# ---------------------------------------------------------------------------


async def test_toggle_admin_happy_path():
    """PATCH /{id}/admin → 200, returns new is_admin value."""
    _override(uid=_ADMIN_ID, db_factory=_make_toggle_db)
    resp = await _patch(
        f"/v1/admin/users/{_TARGET_ID}/admin",
        json={"is_admin": True, "reason": "promote to admin for dashboard access"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["is_admin"] is True
    assert body["user_id"] == _TARGET_ID


async def test_toggle_admin_self_returns_422():
    """Admin tries to toggle their own admin status → 422 (self-protection)."""
    _override(uid=_ADMIN_ID, db_factory=_make_toggle_db)
    resp = await _patch(
        f"/v1/admin/users/{_ADMIN_ID}/admin",
        json={"is_admin": False, "reason": "should be blocked self-demotion"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422
    body = resp.json()
    assert "cannot modify own admin status" in body.get("detail", "")


async def test_toggle_admin_rejects_internal_account_demotion():
    db = _make_toggle_db(plan_name="internal")
    _override(uid=_ADMIN_ID, db_factory=lambda: db)
    resp = await _patch(
        f"/v1/admin/users/{_TARGET_ID}/admin",
        json={"is_admin": False, "reason": "must remain an internal admin"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert db.commit.await_count == 0


async def test_toggle_admin_missing_user_returns_404():
    """Toggling admin on an unknown user returns 404 and does not commit."""
    db = _make_toggle_db(exists=False)
    _override(uid=_ADMIN_ID, db_factory=lambda: db)
    resp = await _patch(
        "/v1/admin/users/99999/admin",
        json={"is_admin": True, "reason": "promote to admin for dashboard access"},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 404
    assert db.commit.await_count == 0
