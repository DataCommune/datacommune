"""Preproduction flags block Resend and Stripe before any external call."""

from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.email import send_verification_email
from app.auth.models import UserInfo
from app.billing.service import BillingDisabledError, create_checkout_session
from app.config import settings
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit


def test_external_effect_flags_preserve_production_defaults():
    assert settings.email_enabled is True
    assert settings.stripe_enabled is True


@pytest.mark.asyncio
async def test_disabled_email_never_calls_resend(monkeypatch):
    monkeypatch.setattr(settings, "email_enabled", False)
    with patch("app.auth.email.resend.Emails.send") as send:
        await send_verification_email("test@example.invalid", "secret-token")
    send.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_email_registration_auto_verifies_test_account(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "email_enabled", False)
    monkeypatch.setattr(settings, "app_environment", "preproduction")
    with (
        patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("app.auth.router.create_user", new_callable=AsyncMock, return_value=42),
        patch(
            "app.auth.router._provision_verified_account", new_callable=AsyncMock
        ) as provision,
        patch(
            "app.auth.router.send_verification_email", new_callable=AsyncMock
        ) as send,
    ):
        response = await client.post(
            "/auth/register",
            json={"email": "test@example.com", "password": "longpassword"},
        )
    assert response.status_code == 202
    assert response.json()["requires_verification"] is False
    provision.assert_awaited_once_with(ANY, "test@example.com", 42)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_email_never_auto_verifies_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "email_enabled", False)
    monkeypatch.setattr(settings, "app_environment", "production")
    with (
        patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("app.auth.router.create_user", new_callable=AsyncMock, return_value=42),
        patch(
            "app.auth.router._provision_verified_account", new_callable=AsyncMock
        ) as provision,
        patch(
            "app.auth.router.send_verification_email", new_callable=AsyncMock
        ) as send,
    ):
        response = await client.post(
            "/auth/register",
            json={"email": "test@example.com", "password": "longpassword"},
        )
    assert response.status_code == 202
    assert response.json()["requires_verification"] is True
    provision.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_stripe_service_fails_before_client_creation(monkeypatch):
    monkeypatch.setattr(settings, "stripe_enabled", False)
    with (
        patch("app.billing.service.stripe.StripeClient") as client,
        pytest.raises(BillingDisabledError),
    ):
        await create_checkout_session(
            user_id=1,
            customer_id=None,
            customer_email="test@example.invalid",
            price_id="price_test",
            success_url="https://dev.example/success",
            cancel_url="https://dev.example/cancel",
        )
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/billing/checkout", "/billing/portal"])
async def test_disabled_billing_routes_fail_before_database_or_stripe(
    path, monkeypatch
):
    monkeypatch.setattr(settings, "stripe_enabled", False)
    user = UserInfo(
        id=2,
        email="test@example.invalid",
        name="Test",
        company=None,
        is_verified=True,
        is_admin=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    try:
        with (
            patch(
                "app.billing.router.create_checkout_session", new_callable=AsyncMock
            ) as checkout,
            patch(
                "app.billing.router.create_portal_session", new_callable=AsyncMock
            ) as portal,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.post(
                    path,
                    json={"plan": "pro", "interval": "month"}
                    if path.endswith("checkout")
                    else None,
                )
        assert response.status_code == 503
        assert response.json() == {"detail": "billing_disabled_for_environment"}
        db.execute.assert_not_awaited()
        checkout.assert_not_awaited()
        portal.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_disabled_webhook_never_verifies_or_processes(monkeypatch):
    monkeypatch.setattr(settings, "stripe_enabled", False)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    try:
        with (
            patch("app.billing.router.verify_webhook_signature") as verify,
            patch(
                "app.billing.router.process_event", new_callable=AsyncMock
            ) as process,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.post(
                    "/stripe/webhook", content=b"production-event"
                )
        assert response.status_code == 503
        verify.assert_not_called()
        process.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()
