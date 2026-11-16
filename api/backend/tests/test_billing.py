"""Tests for billing module: webhook signature, idempotency, event handlers.

Covers: webhook verification, checkout.session.completed,
customer.subscription.updated, customer.subscription.deleted,
invoice.payment_failed, idempotency, plans.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.billing.plans import (
    UNLIMITED_QUOTA,
    get_plan_by_price_id,
    get_plans,
    get_quota_for_plan,
    is_internal_unlimited_plan,
)
from app.billing.queries import get_active_subscription
from app.billing.service import verify_webhook_signature
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_db():
    return AsyncMock()


@pytest.fixture()
async def billing_client():
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _make_webhook_event(
    event_type: str,
    event_id: str = "evt_test_123",
    data_object: dict | None = None,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object or {}},
    }


# ===========================================================================
# 1. Plans
# ===========================================================================


class TestPlans:
    def test_get_plans_returns_all(self):
        plans = get_plans()
        assert "free" in plans
        assert "pro" in plans
        assert "business" in plans

    def test_free_quota(self):
        assert get_quota_for_plan("free") == 500

    def test_pro_quota(self):
        assert get_quota_for_plan("pro") == 5_000

    def test_business_quota(self):
        assert get_quota_for_plan("business") == 25_000

    def test_unknown_plan_defaults_to_free(self):
        assert get_quota_for_plan("nonexistent") == 500

    def test_internal_plan_is_unlimited_but_not_public(self):
        assert get_quota_for_plan("internal") == UNLIMITED_QUOTA
        assert is_internal_unlimited_plan("internal") is True
        assert "internal" not in get_plans()

    def test_get_plan_by_price_id_none(self):
        assert get_plan_by_price_id("price_nonexistent") is None

    @patch("app.billing.plans.settings")
    def test_get_plan_by_price_id_found(self, mock_settings):
        mock_settings.stripe_price_pro_monthly = "price_pro_123"
        mock_settings.stripe_price_pro_annual = "price_pro_ann_123"
        mock_settings.stripe_price_business_monthly = "price_biz_m_456"
        mock_settings.stripe_price_business_annual = "price_biz_ann_456"
        mock_settings.stripe_price_enterprise_monthly = "price_ent_m_789"
        mock_settings.stripe_price_enterprise_annual = "price_ent_ann_789"
        result = get_plan_by_price_id("price_pro_123")
        assert result is not None
        assert result[0] == "pro"


class TestActiveSubscription:
    @pytest.mark.asyncio
    async def test_internal_plan_has_priority_over_newer_active_plan(self):
        result = MagicMock()
        result.mappings.return_value.first.return_value = {
            "id": 7,
            "plan_name": "internal",
        }
        db = AsyncMock()
        db.execute.return_value = result

        subscription = await get_active_subscription(db, user_id=99)

        assert subscription == {"id": 7, "plan_name": "internal"}
        statement, params = db.execute.await_args.args
        assert "ORDER BY (plan_name = 'internal') DESC" in str(statement)
        assert (
            "(source = 'manual_grant') DESC NULLS LAST, created_at DESC, id DESC"
            in str(statement)
        )
        assert params == {"uid": 99}


class TestCheckoutProtection:
    @pytest.mark.asyncio
    async def test_internal_account_cannot_start_checkout(self):
        now = datetime.now(UTC)
        user = UserInfo(
            id=99,
            email="internal@example.test",
            name="Internal",
            company=None,
            is_verified=True,
            is_admin=True,
            created_at=now,
            updated_at=now,
        )
        db = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: db

        try:
            with (
                patch(
                    "app.billing.router.get_active_subscription",
                    new_callable=AsyncMock,
                    return_value={"plan_name": "internal"},
                ),
                patch(
                    "app.billing.router.create_checkout_session",
                    new_callable=AsyncMock,
                ) as create_checkout,
            ):
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/billing/checkout",
                        json={"plan": "pro", "interval": "month"},
                    )

            assert response.status_code == 403
            create_checkout.assert_not_awaited()
        finally:
            app.dependency_overrides.clear()


# ===========================================================================
# 2. Webhook signature verification
# ===========================================================================


class TestWebhookSignature:
    @patch("app.billing.service.stripe.Webhook.construct_event")
    def test_valid_signature(self, mock_construct):
        mock_construct.return_value = {"id": "evt_1", "type": "test"}
        result = verify_webhook_signature(b"payload", "sig_header")
        assert result is not None
        assert result["id"] == "evt_1"

    @patch("app.billing.service.stripe.Webhook.construct_event")
    def test_invalid_signature(self, mock_construct):
        import stripe

        mock_construct.side_effect = stripe.SignatureVerificationError(
            "bad sig", "sig_header"
        )
        result = verify_webhook_signature(b"payload", "bad")
        assert result is None

    @patch("app.billing.service.stripe.Webhook.construct_event")
    def test_value_error(self, mock_construct):
        mock_construct.side_effect = ValueError("bad payload")
        result = verify_webhook_signature(b"bad", "sig")
        assert result is None


# Legacy handler mocks are superseded by the real PostgreSQL contract suite:
# tests/integration/test_billing_reconciliation_contract.py (COR-17).


@pytest.mark.parametrize("status", ["ok", "already_processed"])
async def test_webhook_success_reflects_atomic_processor(billing_client, status):
    event = _make_webhook_event("customer.subscription.updated")
    with (
        patch("app.billing.router.verify_webhook_signature", return_value=event),
        patch(
            "app.billing.router.process_event",
            new_callable=AsyncMock,
            return_value=status,
        ) as process,
    ):
        response = await billing_client.post("/stripe/webhook", content=b"signed")
    assert response.status_code == 200
    assert response.json() == {"status": status}
    process.assert_awaited_once()


async def test_invalid_signature_never_processes_event(billing_client):
    with (
        patch("app.billing.router.verify_webhook_signature", return_value=None),
        patch("app.billing.router.process_event", new_callable=AsyncMock) as process,
    ):
        response = await billing_client.post("/stripe/webhook", content=b"invalid")
    assert response.status_code == 400
    process.assert_not_called()


@pytest.mark.parametrize("error,code", [(RuntimeError("unavailable"), 503)])
async def test_incomplete_processor_returns_retry_without_success(
    billing_client, error, code
):
    event = _make_webhook_event("customer.subscription.updated")
    with (
        patch("app.billing.router.verify_webhook_signature", return_value=event),
        patch(
            "app.billing.router.process_event",
            new_callable=AsyncMock,
            side_effect=error,
        ),
    ):
        response = await billing_client.post("/stripe/webhook", content=b"signed")
    assert response.status_code == code
    assert response.json()["retryable"] is True


@pytest.mark.parametrize(
    "field,value", [("uid", 99), ("customer", "cus_other"), ("nonce", "0" * 32)]
)
def test_signed_binding_rejects_account_customer_or_nonce_tampering(field, value):
    import json

    from app.billing.service import (
        BillingBindingError,
        make_checkout_binding,
        read_checkout_binding,
    )

    metadata = make_checkout_binding(42, "cus_fixture")
    assert read_checkout_binding(metadata)["uid"] == 42
    obj = json.loads(metadata["dc_binding"])
    obj[field] = value
    metadata["dc_binding"] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    with pytest.raises(BillingBindingError):
        read_checkout_binding(metadata)


async def test_checkout_propagates_the_same_signed_binding_to_subscription():
    from app.billing.service import create_checkout_session, read_checkout_binding

    client = MagicMock()
    client.checkout.sessions.create_async = AsyncMock(
        return_value=MagicMock(url="https://fixture/checkout")
    )
    with patch("app.billing.service._get_client", return_value=client):
        assert (
            await create_checkout_session(
                user_id=42,
                customer_id=None,
                customer_email="mutable@example.com",
                price_id="price_fixture",
                success_url="https://fixture/ok",
                cancel_url="https://fixture/cancel",
            )
            == "https://fixture/checkout"
        )
    params = client.checkout.sessions.create_async.await_args.args[0]
    assert params["client_reference_id"] == "42"
    assert params["metadata"] == params["subscription_data"]["metadata"]
    binding = read_checkout_binding(params["metadata"])
    assert binding["uid"] == 42 and binding["customer"] == ""
    assert "email" not in binding
