"""Tests for admin_resend_verification helper + resend-verification endpoint.

Covers: unverified user → email scheduled + audit logged, already verified → 422,
user-not-found 404, cooldown window → 429, separate users no interference,
cooldown expiry, fire-and-forget (email runs after commit via asyncio.create_task),
short-reason 422.

Pattern: AsyncMock + dependency_overrides + module-level cooldown dict reset.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.main import app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_ADMIN_ID = 1
_TARGET_ID = 2
_TARGET_ID_B = 3


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


def _make_db_for_resend(
    *,
    user_exists: bool = True,
    is_verified: bool = False,
    email: str = "u@x.fr",
    uid: int = _TARGET_ID,
) -> AsyncMock:
    """Build a mock DB for admin_resend_verification.

    execute(1) = SELECT id, email, is_verified FROM users WHERE id = :uid
    """
    db = AsyncMock()

    user_result = MagicMock()
    if user_exists:
        user_result.mappings.return_value.first.return_value = _DictMapping(
            {"id": uid, "email": email, "is_verified": is_verified}
        )
    else:
        user_result.mappings.return_value.first.return_value = None

    db.execute.return_value = user_result
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Autouse fixture: reset module-level cooldown dict between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cooldown():
    """Clear the module-level _resend_verif_last_sent dict before each test."""
    import app.admin.queries as mod

    mod._resend_verif_last_sent.clear()
    yield
    mod._resend_verif_last_sent.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_resend_unverified_user_schedules_email_and_logs():
    """Unverified user: audit logged, commit called, email task fires after commit."""
    from app.admin.queries import admin_resend_verification

    db = _make_db_for_resend(is_verified=False, email="u@x.fr")
    admin = _make_admin()

    mock_send = AsyncMock()
    log_path = "app.admin.queries.log_admin_action"
    token_path = "app.admin.queries.create_verification_token"
    with (
        patch(log_path, new_callable=AsyncMock) as mock_log,
        patch(token_path, return_value="TOKEN-u@x.fr"),
        patch("app.admin.queries.send_verification_email", mock_send),
    ):
        result = await admin_resend_verification(
            db=db,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="resend verification for customer support",
            request=MagicMock(),
        )

        # Let the scheduled asyncio task run
        await asyncio.sleep(0)

    # Return value
    assert result["email_sent"] is True
    assert result["email"] == "u@x.fr"

    # Audit logged with correct before/after
    mock_log.assert_awaited_once()
    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["action"] == "resend_verification"
    assert call_kwargs["before"] == {"is_verified": False}
    assert call_kwargs["after"]["email_sent"] is True
    assert call_kwargs["after"]["email"] == "u@x.fr"

    # Commit called
    db.commit.assert_awaited_once()

    # Email task ran with correct args
    mock_send.assert_awaited_once_with("u@x.fr", "TOKEN-u@x.fr")


async def test_resend_already_verified_raises_422():
    """Already-verified user → 422; no audit, no commit, no email."""
    from app.admin.queries import admin_resend_verification
    from app.common.exceptions import ErreurApiError

    db = _make_db_for_resend(is_verified=True, email="verified@x.fr")
    admin = _make_admin()

    mock_send = AsyncMock()
    log_path = "app.admin.queries.log_admin_action"
    with (
        patch(log_path, new_callable=AsyncMock) as mock_log,
        patch("app.admin.queries.send_verification_email", mock_send),
        pytest.raises(ErreurApiError) as exc_info,
    ):
        await admin_resend_verification(
            db=db,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="should fail already verified",
            request=MagicMock(),
        )

    assert exc_info.value.code == 422
    assert "vérifié" in exc_info.value.detail

    mock_log.assert_not_awaited()
    db.commit.assert_not_awaited()
    mock_send.assert_not_awaited()


async def test_resend_user_not_found_404():
    """Non-existent user → 404; no audit, no commit, no email."""
    from app.admin.queries import admin_resend_verification
    from app.common.exceptions import ErreurApiError

    db = _make_db_for_resend(user_exists=False)
    admin = _make_admin()

    mock_send = AsyncMock()
    log_path = "app.admin.queries.log_admin_action"
    with (
        patch(log_path, new_callable=AsyncMock) as mock_log,
        patch("app.admin.queries.send_verification_email", mock_send),
        pytest.raises(ErreurApiError) as exc_info,
    ):
        await admin_resend_verification(
            db=db,
            admin=admin,
            target_user_id=9999,
            reason="user does not exist in the system",
            request=MagicMock(),
        )

    assert exc_info.value.code == 404
    mock_log.assert_not_awaited()
    db.commit.assert_not_awaited()
    mock_send.assert_not_awaited()


async def test_resend_cooldown_blocks_second_attempt():
    """Second attempt for same user within 5min → 429; audit called only once total."""
    from app.admin.queries import admin_resend_verification
    from app.common.exceptions import ErreurApiError

    def _make_fresh_db():
        return _make_db_for_resend(is_verified=False, email="u@x.fr")

    db1 = _make_fresh_db()
    db2 = _make_fresh_db()
    admin = _make_admin()

    with (
        patch("app.admin.queries.log_admin_action", new_callable=AsyncMock) as mock_log,
        patch("app.admin.queries.create_verification_token", return_value="TOKEN"),
        patch("app.admin.queries.send_verification_email", AsyncMock()),
    ):
        # First call succeeds
        await admin_resend_verification(
            db=db1,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="first send for customer account",
            request=MagicMock(),
        )

        # Second call immediately → cooldown
        with pytest.raises(ErreurApiError) as exc_info:
            await admin_resend_verification(
                db=db2,
                admin=admin,
                target_user_id=_TARGET_ID,
                reason="second send attempt for customer",
                request=MagicMock(),
            )

    assert exc_info.value.code == 429
    assert "Renvoi possible dans" in exc_info.value.detail

    # Audit logged only once (for the first call)
    assert mock_log.await_count == 1

    # Cooldown dict has the user_id entry
    import app.admin.queries as mod

    assert _TARGET_ID in mod._resend_verif_last_sent


async def test_resend_cooldown_separate_users_do_not_interfere():
    """Cooldown for user 2 does not block user 3 — separate per-user tracking."""
    from app.admin.queries import admin_resend_verification

    db_a = _make_db_for_resend(is_verified=False, email="a@x.fr", uid=_TARGET_ID)
    db_b = _make_db_for_resend(is_verified=False, email="b@x.fr", uid=_TARGET_ID_B)
    admin = _make_admin()

    log_path = "app.admin.queries.log_admin_action"
    token_path = "app.admin.queries.create_verification_token"
    with (
        patch(log_path, new_callable=AsyncMock) as mock_log,
        patch(token_path, side_effect=lambda e: f"TOKEN-{e}"),
        patch("app.admin.queries.send_verification_email", AsyncMock()),
    ):
        res_a = await admin_resend_verification(
            db=db_a,
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="resend for user A customer support",
            request=MagicMock(),
        )
        res_b = await admin_resend_verification(
            db=db_b,
            admin=admin,
            target_user_id=_TARGET_ID_B,
            reason="resend for user B customer support",
            request=MagicMock(),
        )

    assert res_a["email_sent"] is True
    assert res_b["email_sent"] is True
    # Both audit rows written
    assert mock_log.await_count == 2


async def test_resend_cooldown_expires_after_5_min():
    """After 5m01s, cooldown expires and a second send succeeds."""
    from app.admin.queries import admin_resend_verification

    base_time = 1000.0

    def _make_fresh_db():
        return _make_db_for_resend(is_verified=False, email="u@x.fr")

    admin = _make_admin()

    call_count = 0

    def _monotonic():
        nonlocal call_count
        call_count += 1
        # First call = base_time (cooldown not yet set — first send)
        # Subsequent calls = base_time + 5m01s (cooldown expired)
        if call_count <= 1:
            return base_time
        return base_time + 5 * 60 + 1

    with (
        patch("app.admin.queries.time.monotonic", side_effect=_monotonic),
        patch("app.admin.queries.log_admin_action", new_callable=AsyncMock),
        patch("app.admin.queries.create_verification_token", return_value="TOKEN"),
        patch("app.admin.queries.send_verification_email", AsyncMock()),
    ):
        # First call
        await admin_resend_verification(
            db=_make_fresh_db(),
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="first send before cooldown starts",
            request=MagicMock(),
        )

        # Second call after cooldown expires
        result = await admin_resend_verification(
            db=_make_fresh_db(),
            admin=admin,
            target_user_id=_TARGET_ID,
            reason="second send after cooldown expires",
            request=MagicMock(),
        )

    assert result["email_sent"] is True


async def test_resend_endpoint_short_reason_422():
    """POST with reason shorter than 10 chars → 422 Pydantic validation error."""
    admin = _make_admin()
    app.dependency_overrides[get_current_user] = lambda: admin

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            f"/v1/admin/users/{_TARGET_ID}/resend-verification",
            json={"reason": "short"},
        )

    app.dependency_overrides.clear()

    assert resp.status_code == 422
