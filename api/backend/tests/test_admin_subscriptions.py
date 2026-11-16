"""Unit tests for /v1/admin/subscriptions (Phase 18).

Covers:
- Force-sync happy path (mocks Stripe, asserts upsert + quota + audit).
- Force-sync rejects source='manual_grant' (422).
- Force-sync rejects unknown sub_id (404).
- List endpoint pagination + source filter.

Pattern: AsyncMock DB + dependency_overrides[get_current_user], matches
test_admin_api_keys.py and test_admin_users.py established in Phases 15-17.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
_ADMIN_ID = 1


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
    """Adapter so AsyncMock().execute(...).mappings().first()/.all() work."""

    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _ExecResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return _DictMapping(self._rows)

    def scalar_one(self):
        return self._scalar if self._scalar is not None else 0


@pytest.fixture
def admin_client():
    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


# ---------- List endpoint ----------


class TestListAdminSubscriptions:
    @pytest.mark.asyncio
    async def test_list_returns_rows_and_pagination(self, admin_client):
        db = AsyncMock()

        fake_row = {
            "id": 7,
            "user_id": 42,
            "user_email": "u@test",
            "plan_name": "pro",
            "status": "active",
            "source": "stripe",
            "stripe_subscription_id": "sub_abc",
            "current_period_start": datetime(2026, 4, 1, tzinfo=UTC),
            "current_period_end": datetime(2026, 5, 1, tzinfo=UTC),
            "cancel_at_period_end": False,
            "created_at": datetime(2026, 4, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 4, 1, tzinfo=UTC),
        }

        # First execute() -> data rows, second -> count
        db.execute = AsyncMock(
            side_effect=[
                _ExecResult(rows=[fake_row]),
                _ExecResult(scalar=1),
            ]
        )
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as client:
            resp = await client.get(
                "/v1/admin/subscriptions?page=1&page_size=10",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["rows"][0]["source"] == "stripe"
        assert body["rows"][0]["user_email"] == "u@test"

    @pytest.mark.asyncio
    async def test_list_source_filter_param_flows_to_query(self, admin_client):
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[_ExecResult(rows=[]), _ExecResult(scalar=0)]
        )
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as client:
            resp = await client.get(
                "/v1/admin/subscriptions?source=manual_grant",
            )

        assert resp.status_code == 200
        # Params dict passed to the 2 SQL calls must include the filter value
        for call in db.execute.await_args_list:
            params = (
                call.args[1]
                if len(call.args) > 1
                else call.kwargs.get("parameters") or {}
            )
            assert params.get("source") == "manual_grant"

    @pytest.mark.asyncio
    async def test_list_invalid_source_value_422(self, admin_client):
        # Pydantic Literal['stripe','manual_grant'] rejects other strings.
        db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: db
        async with admin_client as client:
            resp = await client.get(
                "/v1/admin/subscriptions?source=bogus",
            )
        assert resp.status_code == 422


# ---------- Force-sync endpoint ----------


class TestSyncSubscription:
    @pytest.mark.asyncio
    async def test_sync_rejects_manual_grant_422(self, admin_client):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=_ExecResult(
                rows=[
                    {
                        "id": 5,
                        "user_id": 42,
                        "stripe_subscription_id": "manual_grant_42",
                        "plan_name": "enterprise",
                        "status": "active",
                        "source": "manual_grant",
                    }
                ]
            )
        )
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as client:
            resp = await client.post(
                "/v1/admin/subscriptions/5/sync",
                json={"reason": "should be blocked by guard"},
            )

        assert resp.status_code == 422
        assert "manuelle" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_sync_404_when_sub_not_found(self, admin_client):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ExecResult(rows=[]))
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as client:
            resp = await client.post(
                "/v1/admin/subscriptions/999999/sync",
                json={"reason": "nothing should be found"},
            )
        assert resp.status_code == 404

    async def test_sync_happy_path_calls_upsert_and_audit(self, admin_client):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=_ExecResult(
                rows=[
                    {
                        "id": 11,
                        "user_id": 42,
                        "stripe_subscription_id": "sub_fixture",
                        "source": "stripe",
                    }
                ]
            )
        )
        app.dependency_overrides[get_db] = lambda: db
        after = {
            "plan_name": "business",
            "status": "active",
            "source": "stripe",
            "quota_monthly": 25000,
            "_before": {"plan_name": "pro", "status": "active", "source": "stripe"},
        }
        with (
            patch(
                "app.admin.queries.get_stripe_customer_id",
                new_callable=AsyncMock,
                return_value="cus_fixture",
            ),
            patch(
                "app.admin.queries.reconcile_subscription",
                new_callable=AsyncMock,
                return_value=after,
            ) as reconcile,
            patch(
                "app.admin.queries.log_admin_action", new_callable=AsyncMock
            ) as audit,
        ):
            async with admin_client as client:
                response = await client.post(
                    "/v1/admin/subscriptions/11/sync",
                    json={"reason": "reconcile provider state"},
                )
        assert response.status_code == 200, response.text
        reconcile.assert_awaited_once_with(
            db, "sub_fixture", "cus_fixture", expected_user_id=42
        )
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["before"]["plan_name"] == "pro"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_reason_too_short_422(self, admin_client):
        db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: db
        async with admin_client as client:
            resp = await client.post(
                "/v1/admin/subscriptions/1/sync",
                json={"reason": "short"},
            )
        assert resp.status_code == 422

    async def test_sync_502_on_stripe_error(self, admin_client):
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=_ExecResult(
                rows=[
                    {
                        "id": 11,
                        "user_id": 42,
                        "stripe_subscription_id": "sub_fixture",
                        "source": "stripe",
                    }
                ]
            )
        )
        app.dependency_overrides[get_db] = lambda: db
        with (
            patch(
                "app.admin.queries.get_stripe_customer_id",
                new_callable=AsyncMock,
                return_value="cus_fixture",
            ),
            patch(
                "app.admin.queries.reconcile_subscription",
                new_callable=AsyncMock,
                side_effect=RuntimeError("provider unavailable"),
            ) as reconcile,
            patch(
                "app.admin.queries.log_admin_action", new_callable=AsyncMock
            ) as audit,
        ):
            async with admin_client as client:
                response = await client.post(
                    "/v1/admin/subscriptions/11/sync",
                    json={"reason": "reconcile provider state"},
                )
        assert response.status_code == 502, response.text
        reconcile.assert_awaited_once_with(
            db, "sub_fixture", "cus_fixture", expected_user_id=42
        )
        audit.assert_not_awaited()
        db.commit.assert_not_awaited()
        db.rollback.assert_awaited_once()
