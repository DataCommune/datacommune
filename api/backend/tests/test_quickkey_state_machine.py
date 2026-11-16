"""Tests for quick-key state machine (api_keys.status column, migration 064).

Covers:
  T1: count_user_active_keys excludes pending and revoked keys
  T2: validate_api_key (lookup_api_key) rejects status='pending' as 401
  T3: insert_user_api_key uses CAST(:status AS VARCHAR) form (STATE:129)
  T4: _deliver_quick_key happy path — key transitions pending -> active
  T5: _deliver_quick_key email failure — key transitions pending -> revoked (no ghost)
  T6: _deliver_quick_key cap-of-3 — returns early, no new key inserted
  T7: quick-key endpoint cooldown — second request within window returns 202 no-op

Pattern: AsyncMock + _TrackingDB (STATE:96-111). No real DB or network calls.
All tests are unit-level; _deliver_quick_key is called directly, bypassing HTTP.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.queries import (
    count_user_active_keys,
    get_default_key_for_user,
    insert_user_api_key,
    lookup_api_key,
    revoke_user_key,
    update_api_key_status,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
_USER_ID = 42
_KEY_ID = 99


# ---------------------------------------------------------------------------
# _TrackingDB — minimal async DB mock for inspecting SQL + tracking commits
# ---------------------------------------------------------------------------


class _TrackingDB:
    """Tracks execute() calls and commit() for SQL inspection + atomicity proofs.

    execute_args: list of positional args per execute call.
    execute_results: per-call return values (configurable via constructor).
    commit_called: True if db.commit() was ever awaited.
    """

    def __init__(self, *, execute_results: list | None = None):
        self.execute_args: list = []
        self._results = execute_results or []
        self.commit_called = False

    async def execute(self, *args, **kwargs):
        self.execute_args.append(args)
        idx = len(self.execute_args) - 1
        if idx < len(self._results):
            return self._results[idx]
        return MagicMock()

    async def commit(self):
        self.commit_called = True

    async def rollback(self):
        pass

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


def _scalar_result(value):
    """Return a mock result where .scalar_one() returns value."""
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _mappings_result(row_dict: dict | None):
    """Return a mock result where .mappings().first() returns a mapping or None."""
    result = MagicMock()
    if row_dict is None:
        result.mappings.return_value.first.return_value = None
    else:
        m = MagicMock()
        m.__iter__ = lambda s: iter(row_dict.items())
        # Make dict(row) work via keys() + __getitem__
        m.keys = lambda: row_dict.keys()
        m.__getitem__ = lambda s, k: row_dict[k]
        result.mappings.return_value.first.return_value = m
    return result


# ---------------------------------------------------------------------------
# T1: count_user_active_keys excludes pending and revoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_active_keys_excludes_pending_and_revoked():
    """The key cap must reject both revoked representations."""
    db = _TrackingDB(execute_results=[_scalar_result(1)])
    result = await count_user_active_keys(db, user_id=_USER_ID)
    assert result == 1
    sql_text = str(db.execute_args[0][0])
    assert "status = 'active'" in sql_text
    assert "NOT revoked" in sql_text
    assert "NOT is_default" in sql_text


@pytest.mark.asyncio
async def test_revoke_user_key_synchronizes_legacy_and_status_columns():
    """A user revocation must update both state representations."""
    result = MagicMock()
    result.rowcount = 1
    db = _TrackingDB(execute_results=[result])

    revoked = await revoke_user_key(db, key_id=_KEY_ID, user_id=_USER_ID)

    assert revoked is True
    sql_text = str(db.execute_args[0][0])
    assert "revoked = TRUE" in sql_text
    assert "status = 'revoked'" in sql_text
    assert db.commit_called is True


@pytest.mark.asyncio
async def test_default_key_requires_both_active_states():
    """A stale default key must not be returned when either state rejects it."""
    db = _TrackingDB(execute_results=[_mappings_result(None)])

    row = await get_default_key_for_user(db, user_id=_USER_ID)

    assert row is None
    sql_text = str(db.execute_args[0][0])
    assert "status = 'active'" in sql_text
    assert "NOT revoked" in sql_text


# ---------------------------------------------------------------------------
# T2: validate_api_key rejects status='pending' (via lookup_api_key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_api_key_rejects_pending_key():
    """lookup_api_key must return None for a key with status='pending'.

    The WHERE clause includes status='active'; a pending key won't match,
    so lookup returns None and the auth dependency raises 401/403.
    """
    # Simulate DB returning None (no row matches WHERE status='active')
    db = _TrackingDB(execute_results=[_mappings_result(None)])
    row = await lookup_api_key(db, key_hash="deadbeef")
    assert row is None, "pending key (status != 'active') must not authenticate"
    # Confirm the SQL has the status filter
    sql_text = str(db.execute_args[0][0])
    assert "status" in sql_text
    assert "active" in sql_text


@pytest.mark.asyncio
async def test_lookup_api_key_accepts_active_key():
    """lookup_api_key returns a dict for an active key."""
    key_row = {
        "id": _KEY_ID,
        "name": "test-key",
        "quota_monthly": 500,
        "revoked": False,
        "created_at": _NOW,
        "user_id": _USER_ID,
        "status": "active",
    }
    db = _TrackingDB(execute_results=[_mappings_result(key_row)])
    row = await lookup_api_key(db, key_hash="abc123")
    assert row is not None
    assert row["status"] == "active"


# ---------------------------------------------------------------------------
# T3: insert_user_api_key uses CAST(:status AS VARCHAR) form (STATE:129)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_uses_cast_form():
    """Regression: asyncpg-compatible cast syntax in insert_user_api_key.

    asyncpg cannot parse :name::type double-colon casts in SQLAlchemy text().
    The correct form is CAST(:status AS VARCHAR) — this test proves it.
    """
    db = _TrackingDB(execute_results=[_scalar_result(_KEY_ID)])
    await insert_user_api_key(
        db,
        key_hash="hashed",
        name="test-key",
        quota=500,
        user_id=_USER_ID,
        key_prefix="dc_anon_abc...",
        environment="anon",
        status="pending",
    )
    sql_text = str(db.execute_args[0][0])
    assert "CAST" in sql_text, "Must use CAST(:status AS VARCHAR) not :status::varchar"
    assert "::" not in sql_text, (
        "Double-colon cast must NOT appear (asyncpg incompatible)"
    )
    assert db.execute_args[0][1]["revoked"] is False
    assert db.commit_called is True


@pytest.mark.asyncio
async def test_update_api_key_status_cast_form():
    """update_api_key_status must also use CAST(:s AS VARCHAR) form."""
    db = _TrackingDB()
    await update_api_key_status(db, key_id=_KEY_ID, new_status="active")
    sql_text = str(db.execute_args[0][0])
    assert "CAST" in sql_text
    assert "::" not in sql_text
    assert "revoked = :revoked" in sql_text
    assert db.execute_args[0][1]["revoked"] is False


@pytest.mark.asyncio
async def test_update_revoked_status_sets_legacy_flag():
    """The quick-key failure path must synchronize the legacy boolean."""
    db = _TrackingDB()
    await update_api_key_status(db, key_id=_KEY_ID, new_status="revoked")

    assert db.execute_args[0][1]["revoked"] is True


# ---------------------------------------------------------------------------
# T4: _deliver_quick_key happy path — pending -> active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_quick_key_happy_path_activates_key():
    """Valid email flow: key inserted as pending, then activated after email send."""
    import app.auth.router as router_mod

    # We'll track status transitions via mock db
    committed_statuses: list[str] = []

    class _QuickkeyDB:
        """Simulates the DB session used by _deliver_quick_key.

        Call sequence (new user):
          execute(1) = get_user_by_email -> None (new user)
          execute(2) = create_user -> user_id
          execute(3) = insert_user_api_key -> key_id (status='pending')
          execute(4) = verify_user_email (UPDATE is_verified), once the mail left
          execute(5) = update_api_key_status(status='active')
        """

        def __init__(self):
            self._call = 0
            self.committed = []
            self.last_status: str | None = None

        async def execute(self, stmt, params=None, **kwargs):
            self._call += 1
            # Track status transitions
            if params and "s" in params:
                self.last_status = params["s"]
                committed_statuses.append(params["s"])
            result = MagicMock()
            if self._call == 1:
                # get_user_by_email -> None (new user)
                result.mappings.return_value.first.return_value = None
            elif self._call == 2:
                # create_user -> user_id
                result.scalar_one.return_value = _USER_ID
            elif self._call == 3:
                # insert_user_api_key -> key_id
                result.scalar_one.return_value = _KEY_ID
            else:
                result.scalar_one.return_value = None
                result.rowcount = 1
            return result

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    mock_db = _QuickkeyDB()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    email_mock = AsyncMock(return_value=None)

    # Clear cooldown state before test
    router_mod._QUICKKEY_PENDING.discard("test@example.com")
    router_mod._QUICKKEY_COOLDOWN.pop("test@example.com", None)

    with (
        patch("app.auth.router.async_session", return_value=mock_session_ctx),
        patch("app.auth.router.send_api_key_email", new=email_mock),
    ):
        await router_mod._deliver_quick_key("test@example.com")

    email_mock.assert_awaited_once()
    # The last status transition should be 'active'
    assert "active" in committed_statuses, f"Expected 'active' in {committed_statuses}"
    assert "revoked" not in committed_statuses, "No revoked transition on success path"


# ---------------------------------------------------------------------------
# T5: _deliver_quick_key email failure — pending -> revoked (no ghost active key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_quick_key_email_failure_revokes_key():
    """Email failure: key must be flipped to revoked, NOT left as pending/active."""
    import app.auth.router as router_mod

    committed_statuses: list[str] = []

    class _QuickkeyDB:
        def __init__(self):
            self._call = 0

        async def execute(self, stmt, params=None, **kwargs):
            self._call += 1
            if params and "s" in params:
                committed_statuses.append(params["s"])
            result = MagicMock()
            if self._call == 1:
                result.mappings.return_value.first.return_value = None  # new user
            elif self._call == 2:
                result.scalar_one.return_value = _USER_ID  # create_user
            elif self._call == 3:
                result.scalar_one.return_value = _KEY_ID  # insert (status='pending')
            else:
                result.scalar_one.return_value = None
                result.rowcount = 1
            return result

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    mock_db = _QuickkeyDB()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    email_mock = AsyncMock(side_effect=RuntimeError("SMTP down"))

    router_mod._QUICKKEY_PENDING.discard("fail@example.com")
    router_mod._QUICKKEY_COOLDOWN.pop("fail@example.com", None)

    with (
        patch("app.auth.router.async_session", return_value=mock_session_ctx),
        patch("app.auth.router.send_api_key_email", new=email_mock),
    ):
        await router_mod._deliver_quick_key("fail@example.com")

    # Endpoint returns 202 regardless — function must not raise
    assert "revoked" in committed_statuses, (
        f"Ghost active key detected! Statuses: {committed_statuses}. "
        "Email failure must transition key to revoked."
    )
    assert "active" not in committed_statuses, (
        "Key must NOT be activated when email fails"
    )


# ---------------------------------------------------------------------------
# T6: _deliver_quick_key cap-of-3 — returns early, no insert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_quick_key_cap_of_three_returns_early():
    """count_user_active_keys >= 3 must abort without inserting a new key."""
    import app.auth.router as router_mod

    calls: list[str] = []

    class _QuickkeyDB:
        def __init__(self):
            self._call = 0

        async def execute(self, stmt, params=None, **kwargs):
            self._call += 1
            calls.append(str(stmt)[:40])
            result = MagicMock()
            if self._call == 1:
                # get_user_by_email -> existing quickkey user
                m = MagicMock()
                m.keys = lambda: [
                    "id",
                    "email",
                    "password_hash",
                    "name",
                    "company",
                    "is_verified",
                    "is_admin",
                    "created_at",
                    "updated_at",
                ]
                m.__getitem__ = lambda s, k: {
                    "id": _USER_ID,
                    "email": "cap@example.com",
                    "password_hash": "!quickkey:abc",
                    "name": None,
                    "company": None,
                    "is_verified": True,
                    "is_admin": False,
                    "created_at": _NOW,
                    "updated_at": _NOW,
                }[k]
                result.mappings.return_value.first.return_value = m
            elif self._call == 2:
                # count_user_active_keys -> 3 (cap)
                result.scalar_one.return_value = 3
            return result

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    mock_db = _QuickkeyDB()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    email_mock = AsyncMock()

    router_mod._QUICKKEY_PENDING.discard("cap@example.com")

    with (
        patch("app.auth.router.async_session", return_value=mock_session_ctx),
        patch("app.auth.router.send_api_key_email", new=email_mock),
    ):
        await router_mod._deliver_quick_key("cap@example.com")

    # Email must NOT be called (early return)
    email_mock.assert_not_awaited()
    # Only 2 execute calls (get_user + count) — no INSERT
    assert mock_db._call == 2, (
        f"Expected 2 execute calls (get_user + count), got {mock_db._call}"
    )


# ---------------------------------------------------------------------------
# T7: quick-key endpoint cooldown — second request within window is no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_window_returns_202_no_op():
    """Second quick-key request for same email within cooldown returns 202 no-op."""
    from httpx import ASGITransport, AsyncClient

    import app.auth.router as router_mod
    from app.main import app

    email = "cooldown@example.com"

    # Pre-set cooldown (simulate recent successful request)
    router_mod._QUICKKEY_COOLDOWN[email] = _time.monotonic()
    router_mod._QUICKKEY_PENDING.discard(email)

    fire_and_forget_calls: list = []

    with patch(
        "app.auth.router.fire_and_forget",
        side_effect=lambda *a, **kw: fire_and_forget_calls.append(a),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/auth/quick-key",
                json={"email": email},
            )

    assert resp.status_code == 202
    assert len(fire_and_forget_calls) == 0, (
        "fire_and_forget must NOT be called within cooldown window"
    )

    # Cleanup
    router_mod._QUICKKEY_COOLDOWN.pop(email, None)


# ---------------------------------------------------------------------------
# T8: anon-quota exemption safety guard (D-10 precondition, paired with D-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_quota_never_limits_high_quota_anon_key():
    """Safety guard for the Plan 07 anon-quota exemption (D-10).

    An anon-convention key (user_id IS NULL, e.g. key_prefix "dc_anon_...")
    with quota_monthly set well above any realistic monthly volume must NOT
    trip the "Quota mensuel epuise" 429 in _enforce_quota. Plan 07 applies
    the exemption as a pure `UPDATE api_keys SET quota_monthly = <high>` --
    no code path change is required, this test locks the existing behavior.

    This is safe only together with the Task 1 rate-limit hardening (D-11):
    header presence no longer buys a private generous bucket, so lifting
    this quota check does not remove the only cap -- the shared per-IP
    ceiling in app/rate_limit.py still applies.
    """
    import app.rate_limit as rate_limit_mod
    from app.auth.dependencies import _enforce_quota
    from app.auth.models import ApiKeyInfo
    from app.common.exceptions import ErreurApiError

    anon_key = ApiKeyInfo(
        id=_KEY_ID,
        name="anon-public-key",
        quota_monthly=10_000_000,  # D-10 exemption value: far above realistic traffic
        revoked=False,
        created_at=_NOW,
        user_id=None,  # anon-convention key (dc_anon_ prefix, migration 022)
        status="active",
    )

    class _QuotaDB:
        """Minimal DB stub: increment_quota returns a realistic-high count
        that is still far below quota_monthly."""

        async def execute(self, *args, **kwargs):
            result = MagicMock()
            result.scalar_one.return_value = 50_000
            return result

        async def commit(self):
            pass

    request = MagicMock()
    request.state = MagicMock()

    # Isolate the downstream plan:{id} rate-limit bucket from cross-test bleed.
    rate_limit_mod._buckets.pop(f"plan:{_KEY_ID}", None)
    try:
        try:
            result = await _enforce_quota(request, anon_key, _QuotaDB())
        except ErreurApiError as exc:
            pytest.fail(
                f"anon key with quota_monthly={anon_key.quota_monthly} was "
                f"quota-limited: {exc.code} {exc.message} ({exc.detail})"
            )
    finally:
        rate_limit_mod._buckets.pop(f"plan:{_KEY_ID}", None)

    assert result is anon_key


# ---------------------------------------------------------------------------
# A new quick-key account is verified only once the mail with its key is sent
# ---------------------------------------------------------------------------


async def _deliver_new_quick_key(email: str, email_mock: AsyncMock) -> AsyncMock:
    """Play _deliver_quick_key for an unknown address; return the verify mock."""
    import app.auth.router as router_mod

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    verify = AsyncMock(return_value=True)
    router_mod._QUICKKEY_PENDING.discard(email)
    router_mod._QUICKKEY_COOLDOWN.pop(email, None)
    with (
        patch("app.auth.router.async_session", return_value=session_ctx),
        patch("app.auth.router.get_user_by_email", new=AsyncMock(return_value=None)),
        patch("app.auth.router.create_user", new=AsyncMock(return_value=_USER_ID)),
        patch("app.auth.router.verify_user_email", new=verify),
        patch(
            "app.auth.router.insert_user_api_key", new=AsyncMock(return_value=_KEY_ID)
        ),
        patch("app.auth.router.update_api_key_status", new=AsyncMock()),
        patch("app.auth.router.send_api_key_email", new=email_mock),
    ):
        await router_mod._deliver_quick_key(email)
    return verify


@pytest.mark.asyncio
async def test_quick_key_account_stays_unverified_when_the_mail_fails():
    # Nobody proved the address: the account must stay unverified, so the purge of
    # never-verified accounts takes it and a later sign-up of the owner is not blocked.
    verify = await _deliver_new_quick_key(
        "jamais-recu@example.com", AsyncMock(side_effect=RuntimeError("SMTP down"))
    )
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_quick_key_account_is_verified_after_the_mail_is_sent():
    verify = await _deliver_new_quick_key("recu@example.com", AsyncMock())
    verify.assert_awaited_once()
