"""Tests for admin_revoke_all_keys helper + revoke-keys endpoint.

Covers: N active keys → revoked_count=N, 0 active keys still logs audit,
user-not-found 404, audit-failure atomicity, self-protection 422,
SQL WHERE clause inspection (no is_default filter).

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
_TARGET_ID = 8


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

    Simulates the execute sequence for admin_revoke_all_keys:
      execute(1) = user existence SELECT
      execute(2) = active_keys_count snapshot SELECT
      execute(3) = UPDATE api_keys SET revoked=TRUE, status='revoked' RETURNING id
    """

    def __init__(
        self,
        *,
        user_exists: bool = True,
        active_keys_count: int = 3,
        plan_name: str | None = None,
    ):
        self._call_count = 0
        self._user_exists = user_exists
        self._active_keys_count = active_keys_count
        self._plan_name = plan_name
        self.commit_called = False
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
            # Count snapshot SELECT
            result = MagicMock()
            result.mappings.return_value.first.return_value = _DictMapping(
                {"active_keys": self._active_keys_count}
            )
            return result

        if self._call_count == 3:
            # UPDATE ... RETURNING id
            result = MagicMock()
            # .all() returns a list of rows matching the revoked count
            result.all.return_value = [MagicMock()] * self._active_keys_count
            return result

        return MagicMock()

    async def commit(self):
        self.commit_called = True

    async def rollback(self):
        self.rollback_called = True

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


async def _post_revoke_keys(
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
        resp = await c.post(f"/v1/admin/users/{user_id}/revoke-keys", json=json)

    app.dependency_overrides.clear()
    return resp


def _get_update_sql_text(db: _TrackingDB) -> str:
    """Extract the SQL text from the 3rd execute call (the UPDATE statement)."""
    # execute_args[2] is (stmt, params) for the 3rd call
    stmt = db.execute_args[2][0]
    return str(stmt)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_revoke_keys_with_n_active_keys():
    """3 active keys → revoked_count=3, audit logged with action='revoke_api_keys'."""
    from app.admin.queries import admin_revoke_all_keys

    db = _TrackingDB(active_keys_count=3)
    admin = _make_admin()

    log_path = "app.admin.queries.log_admin_action"
    with patch(log_path, new_callable=AsyncMock) as mock_log:
        result = await admin_revoke_all_keys(
            db=db,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="revoke all keys for suspicious activity",
            request=MagicMock(),
        )

    assert result["revoked_count"] == 3

    mock_log.assert_awaited_once()
    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["action"] == "revoke_api_keys"
    assert call_kwargs["before"]["active_keys_count"] == 3
    assert call_kwargs["after"]["revoked_count"] == 3

    assert db.commit_called is True
    assert db.rollback_called is False


async def test_revoke_keys_rejects_internal_account():
    from app.admin.queries import admin_revoke_all_keys
    from app.common.exceptions import ErreurApiError

    db = _TrackingDB(plan_name="internal")

    with pytest.raises(ErreurApiError) as exc_info:
        await admin_revoke_all_keys(
            db=db,
            admin=_make_admin(),
            target_user_id=_TARGET_ID,
            reason="must remain managed by provisioner",
            request=MagicMock(),
        )

    assert exc_info.value.code == 422
    assert db.commit_called is False


async def test_revoke_keys_with_zero_active_keys_still_logs():
    """0 active keys → revoked_count=0, audit STILL written, commit called."""
    from app.admin.queries import admin_revoke_all_keys

    db = _TrackingDB(active_keys_count=0)
    admin = _make_admin()

    log_path = "app.admin.queries.log_admin_action"
    with patch(log_path, new_callable=AsyncMock) as mock_log:
        result = await admin_revoke_all_keys(
            db=db,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="revoke all keys precautionary measure",
            request=MagicMock(),
        )

    assert result["revoked_count"] == 0

    # Audit still written even with 0 revoked (admin action must be traceable)
    mock_log.assert_awaited_once()
    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["before"]["active_keys_count"] == 0
    assert call_kwargs["after"]["revoked_count"] == 0

    assert db.commit_called is True


async def test_revoke_keys_user_not_found_404():
    """Non-existent user → 404; log_admin_action and commit NOT called."""
    from app.admin.queries import admin_revoke_all_keys
    from app.common.exceptions import ErreurApiError

    db = _TrackingDB(user_exists=False)
    admin = _make_admin()

    log_path = "app.admin.queries.log_admin_action"
    with (
        patch(log_path, new_callable=AsyncMock) as mock_log,
        pytest.raises(ErreurApiError) as exc_info,
    ):
        await admin_revoke_all_keys(
            db=db,
            admin=admin,
            target_user_id=9999,
            reason="revoke keys for missing user id",
            request=MagicMock(),
        )

    assert exc_info.value.code == 404
    mock_log.assert_not_awaited()
    assert db.commit_called is False


async def test_revoke_keys_atomicity_audit_failure_no_commit():
    """If log_admin_action raises, db.commit() must NOT be called.

    Atomicity proof: revoke executed but not durably committed if audit fails.
    """
    from app.admin.queries import admin_revoke_all_keys

    db = _TrackingDB(active_keys_count=2)
    admin = _make_admin()

    with patch("app.admin.queries.log_admin_action") as mock_log:
        mock_log.side_effect = RuntimeError("audit boom")

        with pytest.raises(RuntimeError, match="audit boom"):
            await admin_revoke_all_keys(
                db=db,
                admin=admin,
                target_user_id=_TARGET_ID,
                reason="atomicity test for revoke keys",
                request=MagicMock(),
            )

    # CRITICAL: commit must NOT have been called
    assert db.commit_called is False, (
        "db.commit() was called despite audit failure — revoke is NOT atomic."
    )


async def test_revoke_keys_endpoint_self_protection_422():
    """POST /admin/users/{admin_id}/revoke-keys → 422 'cannot revoke own keys'."""
    resp = await _post_revoke_keys(
        user_id=_ADMIN_ID,
        json={"reason": "a" * 10},
        admin_id=_ADMIN_ID,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "cannot revoke own keys" in body.get("detail", "")


async def test_revoke_keys_sql_has_no_is_default_filter():
    """The UPDATE SQL must NOT filter out is_default keys.

    Research Open Question 1 decision: admin revokes ALL keys (including default).
    The WHERE clause must target active, non-revoked keys
    with NO 'AND NOT is_default' condition.
    """
    from app.admin.queries import admin_revoke_all_keys

    db = _TrackingDB(active_keys_count=1)
    admin = _make_admin()

    with patch("app.admin.queries.log_admin_action", new_callable=AsyncMock):
        await admin_revoke_all_keys(
            db=db,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="sql inspection test for is_default",
            request=MagicMock(),
        )

    # The UPDATE is the 3rd execute call
    update_sql = _get_update_sql_text(db)

    # Must NOT filter out is_default
    assert "is_default" not in update_sql.lower(), (
        f"UPDATE SQL should NOT filter is_default, but found it in: {update_sql!r}"
    )

    # Must have the correct WHERE conditions
    assert "not revoked" in update_sql.lower() or "NOT revoked" in update_sql, (
        f"UPDATE SQL should filter NOT revoked, got: {update_sql!r}"
    )
    assert "status = 'active'" in update_sql
    assert "status = 'revoked'" in update_sql
