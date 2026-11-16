"""Tests for POST /admin/users/bulk endpoint.

Covers: all-ok batch, partial failure (mix ok+error), self-in-batch 422,
>100 user_ids 422 Pydantic, empty list 422, short reason 422, invalid action 422,
action dispatch (revoke_keys routes to correct helper), unexpected exception,
non-admin 403.

Pattern: dependency_overrides + monkeypatch helpers on app.admin.users.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_ADMIN_ID = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(uid: int = _ADMIN_ID, is_admin: bool = True) -> UserInfo:
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


def _make_db() -> AsyncMock:
    """Minimal DB mock for bulk endpoint (helpers are patched)."""
    db = AsyncMock()
    db.rollback = AsyncMock()
    return db


async def _post_bulk(
    json: dict,
    admin_id: int = _ADMIN_ID,
    is_admin: bool = True,
    db=None,
):
    user = _make_user(uid=admin_id, is_admin=is_admin)
    app.dependency_overrides[get_current_user] = lambda: user
    if db is not None:
        app.dependency_overrides[get_db] = lambda: db
    else:
        app.dependency_overrides[get_db] = lambda: _make_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/v1/admin/users/bulk", json=json)

    app.dependency_overrides.clear()
    return resp


# ---------------------------------------------------------------------------
# Fixture: mock the three helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_helpers(monkeypatch):
    """Replace the three single-user helpers with AsyncMocks on app.admin.users."""
    reset_mock = AsyncMock(return_value={"quota_used": 0})
    revoke_mock = AsyncMock(return_value={"revoked_count": 3})
    resend_mock = AsyncMock(return_value={"email_sent": True})
    monkeypatch.setattr("app.admin.users.admin_reset_quota", reset_mock)
    monkeypatch.setattr("app.admin.users.admin_revoke_all_keys", revoke_mock)
    monkeypatch.setattr("app.admin.users.admin_resend_verification", resend_mock)
    return reset_mock, revoke_mock, resend_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_bulk_all_ok_reset_quota(mock_helpers):
    """3 users, reset_quota → all ok, 3 audit calls, 0 failed."""
    from app.common.exceptions import ErreurApiError  # noqa: F401 (ensure import works)

    reset_mock, _, _ = mock_helpers

    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": [2, 3, 4],
            "reason": "monthly reset for all team accounts",
        }
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["failed"] == 0
    assert all(r["status"] == "ok" for r in body["results"])
    assert {r["user_id"] for r in body["results"]} == {2, 3, 4}

    # Helper called 3 times with correct target_user_id values
    assert reset_mock.await_count == 3
    called_uids = {c.kwargs["target_user_id"] for c in reset_mock.call_args_list}
    assert called_uids == {2, 3, 4}


async def test_bulk_partial_failure_mixed_ok_and_error(mock_helpers, monkeypatch):
    """User 3 fails with 404, users 2 and 4 succeed — partial failure tolerated."""
    from app.common.exceptions import ErreurApiError

    reset_mock, _, _ = mock_helpers

    async def _side_effect(*, db, admin, target_user_id, reason, request):
        if target_user_id == 3:
            raise ErreurApiError(404, "Introuvable", "User 3 inexistant")
        return {"quota_used": 0}

    reset_mock.side_effect = _side_effect

    db = _make_db()
    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": [2, 3, 4],
            "reason": "bulk reset with one missing user",
        },
        db=db,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["failed"] == 1

    by_uid = {r["user_id"]: r for r in body["results"]}
    assert by_uid[2]["status"] == "ok"
    assert by_uid[3]["status"] == "error"
    assert "User 3 inexistant" in by_uid[3]["detail"]
    assert by_uid[4]["status"] == "ok"

    # rollback called once (for the failed user 3 iteration)
    db.rollback.assert_awaited_once()


async def test_bulk_self_in_batch_rejects_422_upfront(mock_helpers):
    """Admin in user_ids → 422 upfront; no helper called."""
    reset_mock, revoke_mock, resend_mock = mock_helpers

    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": [_ADMIN_ID, 2, 3],
            "reason": "should be rejected self in batch",
        }
    )

    assert resp.status_code == 422
    body = resp.json()
    assert "cannot target yourself in bulk action" in body.get("detail", "")

    # No helpers called — upfront rejection
    assert reset_mock.await_count == 0
    assert revoke_mock.await_count == 0
    assert resend_mock.await_count == 0


async def test_bulk_over_100_users_422_pydantic():
    """101 user_ids → 422 Pydantic max_length enforcement; no helpers called."""
    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": list(range(2, 103)),  # 101 ids, none is admin (1)
            "reason": "should fail pydantic max length check",
        }
    )
    assert resp.status_code == 422


async def test_bulk_empty_user_ids_422_pydantic():
    """Empty user_ids list → 422 Pydantic min_length=1 enforcement."""
    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": [],
            "reason": "should fail pydantic min length check",
        }
    )
    assert resp.status_code == 422


async def test_bulk_short_reason_422_pydantic():
    """reason shorter than 10 chars → 422 Pydantic."""
    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": [2, 3],
            "reason": "short",
        }
    )
    assert resp.status_code == 422


async def test_bulk_invalid_action_422_pydantic():
    """Unlisted action → 422 Pydantic Literal rejection."""
    resp = await _post_bulk(
        json={
            "action": "delete_user",
            "user_ids": [2, 3],
            "reason": "should fail literal validation check",
        }
    )
    assert resp.status_code == 422


async def test_bulk_revoke_keys_dispatches_to_revoke_helper(mock_helpers):
    """action='revoke_keys' → only revoke_mock called, not reset or resend."""
    reset_mock, revoke_mock, resend_mock = mock_helpers

    resp = await _post_bulk(
        json={
            "action": "revoke_keys",
            "user_ids": [2, 3],
            "reason": "revoke all keys for security audit",
        }
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["failed"] == 0

    # Only revoke_mock was called
    assert revoke_mock.await_count == 2
    assert reset_mock.await_count == 0
    assert resend_mock.await_count == 0


async def test_bulk_unexpected_exception_continues_batch(mock_helpers, monkeypatch):
    """RuntimeError on user 3 → detail starts 'Erreur interne:', batch continues."""
    reset_mock, _, _ = mock_helpers

    call_count = 0

    async def _side_effect(*, db, admin, target_user_id, reason, request):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # second call (user 3) raises
            raise RuntimeError("db boom")
        return {"quota_used": 0}

    reset_mock.side_effect = _side_effect

    db = _make_db()
    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": [2, 3, 4],
            "reason": "batch with unexpected exception midway",
        },
        db=db,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["failed"] == 1

    by_uid = {r["user_id"]: r for r in body["results"]}
    assert by_uid[2]["status"] == "ok"
    assert by_uid[3]["status"] == "error"
    assert by_uid[3]["detail"].startswith("Erreur interne:")
    assert by_uid[4]["status"] == "ok"

    # rollback called once for the RuntimeError iteration
    db.rollback.assert_awaited_once()


async def test_bulk_cannot_access_with_non_admin_403():
    """Non-admin user → require_admin raises 403."""
    resp = await _post_bulk(
        json={
            "action": "reset_quota",
            "user_ids": [2, 3],
            "reason": "should be rejected non admin user",
        },
        admin_id=99,
        is_admin=False,
    )
    assert resp.status_code == 403
