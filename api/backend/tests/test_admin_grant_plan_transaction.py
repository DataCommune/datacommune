"""Test atomicity: if log_admin_action fails, the subscription write rolls back.

This is the safety-critical test for Phase 15. CONTEXT.md decision:
"grant-plan + audit log écrits dans la même transaction DB.
Si l'audit échoue, le grant rollback."

The test proves that admin_grant_plan() is implemented as a single transaction:
if log_admin_action raises, db.commit() is never called and db.rollback() is
triggered — meaning no subscription row is durably written.

These tests use a carefully instrumented mock DB that tracks whether
db.commit() was called. If commit was called despite the exception, the
implementation is broken (two-phase, not atomic).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

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
_ADMIN_ID = 1
_TARGET_ID = 77


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
    """AsyncMock DB that tracks commit calls and supports the execute sequence.

    This lets the test assert that db.commit() was NOT called when an
    intermediate step raises — which is the transaction atomicity proof.
    """

    def __init__(self, raise_on_execute_n: int | None = None):
        """
        raise_on_execute_n: if set, raises RuntimeError on the nth execute() call
                             (1-indexed). Used to simulate mid-transaction failures.
        """
        self._call_count = 0
        self._raise_on = raise_on_execute_n
        self.commit_called = False
        self.rollback_called = False

    async def execute(self, *args, **kwargs):
        sql = str(args[0])
        if "pg_advisory_xact_lock" in sql:
            return MagicMock()
        if "SELECT id, user_id" in sql and "FROM subscriptions" in sql:
            result = MagicMock()
            result.mappings.return_value.first.return_value = {"plan_name": "pro"}
            return result
        self._call_count += 1
        if self._raise_on is not None and self._call_count == self._raise_on:
            raise RuntimeError("simulated DB failure")

        # Call 1: before_snapshot — plain MagicMock so .mappings().first() is sync
        if self._call_count == 1:
            result = MagicMock()
            result.mappings.return_value.first.return_value = _DictMapping(
                {"plan": "free", "stripe_subscription_id": None, "status": None}
            )
            return result

        # Calls 2, 3, 4: upsert sub, update keys, audit INSERT
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


# ---------------------------------------------------------------------------
# Test 1: audit failure rolls back subscription
# ---------------------------------------------------------------------------


async def test_grant_plan_rolls_back_on_audit_failure():
    """If log_admin_action raises, db.commit() must NOT be called.

    The implementation wraps all steps in a single async transaction:
    upsert_sub → update_keys → log_audit → commit()

    If log_audit raises, commit() is never reached, so all prior DB
    changes (upsert_sub, update_keys) are not durably written.

    We prove this by:
    1. Patching log_admin_action to raise RuntimeError
    2. Asserting the HTTP response is 5xx
    3. Asserting db.commit() was NOT called on our tracking DB
    """
    tracking_db = _TrackingDB()

    admin = _make_admin()
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db] = lambda: tracking_db

    with patch("app.admin.queries.log_admin_action") as mock_log:
        mock_log.side_effect = RuntimeError("simulated audit failure")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/v1/admin/users/{_TARGET_ID}/grant-plan",
                json={
                    "plan": "pro",
                    "reason": "should fail and roll back due to audit error",
                    "end_date": None,
                },
            )

    app.dependency_overrides.clear()

    # The RuntimeError must surface as a 5xx response (FastAPI unhandled exception)
    assert resp.status_code >= 500, (
        f"Expected 5xx when audit raises, got {resp.status_code}"
    )

    mock_log.assert_called_once()
    assert tracking_db.rollback_called

    # CRITICAL: commit must NOT have been called — the subscription is not persisted
    assert not tracking_db.commit_called, (
        "db.commit() was called despite audit failure — transaction is NOT atomic. "
        "Subscription row would have been durably written."
    )


# ---------------------------------------------------------------------------
# Test 2: early step failure (api_keys UPDATE) also prevents commit
# ---------------------------------------------------------------------------


async def test_grant_plan_atomic_with_quota_update_failure():
    """If api_keys UPDATE (execute call #3) raises, db.commit() must NOT be called.

    This tests the earlier part of the transaction:
    execute(1) = before snapshot
    execute(2) = upsert subscription
    execute(3) = update api_keys quota  ← simulated failure here
    execute(4) = audit log INSERT       ← never reached

    Commit must not be called, ensuring upsert subscription is also rolled back.
    """
    # execute #3 raises (1-indexed: snapshot=1, upsert=2, keys_update=3)
    tracking_db = _TrackingDB(raise_on_execute_n=3)

    admin = _make_admin()
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db] = lambda: tracking_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/v1/admin/users/{_TARGET_ID}/grant-plan",
            json={
                "plan": "pro",
                "reason": "atomicity test quota update failure path",
                "end_date": None,
            },
        )

    app.dependency_overrides.clear()

    # Should surface as 5xx
    assert resp.status_code >= 500, (
        f"Expected 5xx when api_keys UPDATE raises, got {resp.status_code}"
    )

    # CRITICAL: commit must NOT have been called
    assert not tracking_db.commit_called, (
        "db.commit() was called despite api_keys UPDATE failure — "
        "subscription upsert leaked without audit log."
    )
