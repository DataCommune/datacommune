"""Tests for admin_reset_quota helper + reset-quota endpoint.

Covers: happy path (existing row reset), zero-row materialization, user-not-found 404,
audit-failure atomicity (commit NOT reached), self-protection 422,
short-reason 422.

Pattern: AsyncMock + _TrackingDB + dependency_overrides — no real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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
_TARGET_ID = 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_admin(uid: int = _ADMIN_ID) -> UserInfo:
    return UserInfo(
        id=uid,
        email=f"admin{uid}@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _DictMapping:
    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)


class _TrackingDB:
    """AsyncMock DB that tracks commit calls for atomicity proofs.

    Simulates the execute sequence for admin_reset_quota:
      execute(1) = user existence SELECT
      execute(2) = create missing zero row (existing counts unchanged)
      execute(3) = quota_usage_account locked snapshot SELECT
      execute(4) = UPDATE quota_usage_account SET request_count = 0
    """

    def __init__(
        self,
        *,
        user_exists: bool = True,
        quota_used: int | None = 137,
        plan_name: str | None = None,
    ):
        self._call_count = 0
        self._user_exists = user_exists
        self._quota_used = quota_used  # None = no row this month
        self._plan_name = plan_name
        self.commit_called = False
        self.commit_count = 0
        self.zero_row_created = False
        self.rollback_called = False
        self.execute_args: list = []

    async def execute(self, *args, **kwargs):
        self._call_count += 1
        self.execute_args.append(args)

        if self._call_count == 1:
            # User existence SELECT
            result = MagicMock()
            if self._user_exists:
                result.mappings.return_value.first.return_value = _DictMapping(
                    {"id": _TARGET_ID, "email": "u@x.fr", "plan_name": self._plan_name}
                )
            else:
                result.mappings.return_value.first.return_value = None
            return result

        if self._call_count == 2:
            # Materialization must preserve a preexisting nonzero snapshot.
            if self._quota_used is None:
                self._quota_used = 0
                self.zero_row_created = True
            return MagicMock()

        if self._call_count == 3:
            # Quota snapshot SELECT
            result = MagicMock()
            if self._quota_used is not None:
                result.mappings.return_value.first.return_value = _DictMapping(
                    {"quota_used": self._quota_used}
                )
            else:
                result.mappings.return_value.first.return_value = None
            return result

        # call 4+: UPDATE statement
        return MagicMock()

    async def commit(self):
        self.commit_called = True
        self.commit_count += 1

    async def rollback(self):
        self.rollback_called = True

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


async def _post_reset_quota(
    user_id: int,
    json: dict,
    admin_id: int = _ADMIN_ID,
    db=None,
):
    admin = _make_admin(uid=admin_id)
    app.dependency_overrides[get_current_user] = lambda: admin
    if db is not None:
        app.dependency_overrides[get_db] = lambda: db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(f"/v1/admin/users/{user_id}/reset-quota", json=json)

    app.dependency_overrides.clear()
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reset_quota_existing_row_zeroes_it():
    """Happy path: quota_used=137 before → UPDATE issued, audit logged, commit once."""
    from app.admin.queries import admin_reset_quota

    db = _TrackingDB(quota_used=137)
    admin = _make_admin()

    log_path = "app.admin.queries.log_admin_action"
    with patch(log_path, new_callable=AsyncMock) as mock_log:
        result = await admin_reset_quota(
            db=db,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="monthly reset for support ticket",
            request=MagicMock(),
        )

    # Result shape
    assert result["quota_used"] == 0

    # Audit log called with correct before/after
    mock_log.assert_awaited_once()
    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["action"] == "reset_quota"
    assert call_kwargs["before"]["quota_used"] == 137
    assert call_kwargs["after"]["quota_used"] == 0

    assert db._call_count == 4
    assert db.zero_row_created is False

    # Single commit, no rollback
    assert db.commit_called is True
    assert db.commit_count == 1
    assert db.rollback_called is False


async def test_reset_quota_rejects_internal_account():
    from app.admin.queries import admin_reset_quota
    from app.common.exceptions import ErreurApiError

    db = _TrackingDB(plan_name="internal")

    with pytest.raises(ErreurApiError) as exc_info:
        await admin_reset_quota(
            db=db,
            admin=_make_admin(),
            target_user_id=_TARGET_ID,
            reason="must keep internal usage analytics",
            request=MagicMock(),
        )

    assert exc_info.value.code == 422
    assert db.commit_called is False
    assert db._call_count == 1
    assert db.zero_row_created is False


async def test_reset_quota_no_usage_row_materializes_a_zero_counter():
    """A missing row becomes zero; public before/after accounting stays zero."""
    from app.admin.queries import admin_reset_quota

    db = _TrackingDB(quota_used=None)  # no row
    admin = _make_admin()

    log_path = "app.admin.queries.log_admin_action"
    with patch(log_path, new_callable=AsyncMock) as mock_log:
        result = await admin_reset_quota(
            db=db,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="reset for no-usage user this month",
            request=MagicMock(),
        )

    assert result["quota_used"] == 0

    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["before"]["quota_used"] == 0
    assert call_kwargs["after"]["quota_used"] == 0

    assert db._call_count == 4
    assert db.zero_row_created is True
    assert db.commit_called is True
    assert db.commit_count == 1


async def test_reset_quota_user_not_found_raises_404():
    """Non-existent user → 404; log_admin_action and commit NOT called."""
    from app.admin.queries import admin_reset_quota
    from app.common.exceptions import ErreurApiError

    db = _TrackingDB(user_exists=False)
    admin = _make_admin()

    with (
        patch("app.admin.queries.log_admin_action", new_callable=AsyncMock) as mock_log,
        pytest.raises(ErreurApiError) as exc_info,
    ):
        await admin_reset_quota(
            db=db,
            admin=admin,
            target_user_id=9999,
            reason="reset for missing user id",
            request=MagicMock(),
        )

    assert exc_info.value.code == 404
    mock_log.assert_not_awaited()
    assert db.commit_called is False


async def test_reset_quota_atomicity_audit_failure_rolls_back():
    """If log_admin_action raises, db.commit() must NOT be called.

    This is the atomicity proof: audit failure → no durable quota reset.
    """
    from app.admin.queries import admin_reset_quota

    db = _TrackingDB(quota_used=50)
    admin = _make_admin()

    with patch("app.admin.queries.log_admin_action") as mock_log:
        mock_log.side_effect = RuntimeError("audit boom")

        with pytest.raises(RuntimeError, match="audit boom"):
            await admin_reset_quota(
                db=db,
                admin=admin,
                target_user_id=_TARGET_ID,
                reason="atomicity test for reset quota",
                request=MagicMock(),
            )

    # CRITICAL: commit must NOT have been called
    assert db.commit_called is False, (
        "db.commit() was called despite audit failure — quota reset is NOT atomic."
    )


async def test_reset_quota_endpoint_self_protection_422():
    """POST /admin/users/{admin_id}/reset-quota → 422 'cannot reset own quota'."""
    resp = await _post_reset_quota(
        user_id=_ADMIN_ID,
        json={"reason": "a" * 10},
        admin_id=_ADMIN_ID,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "cannot reset own quota" in body.get("detail", "")


async def test_reset_quota_endpoint_short_reason_422():
    """reason shorter than 10 chars → 422 Pydantic validation error."""
    db = _TrackingDB()
    resp = await _post_reset_quota(
        user_id=_TARGET_ID,
        json={"reason": "short"},
        db=db,
    )
    assert resp.status_code == 422
    # Must not have hit the DB at all (Pydantic rejects before handler runs)
    assert db.commit_called is False
