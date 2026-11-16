"""Stripe API calls: Checkout sessions, Customer Portal, webhook verification."""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets

import stripe

from app.config import settings

logger = logging.getLogger(__name__)


class BillingDisabledError(RuntimeError):
    """Stripe effects are disabled for this deployment."""


def _require_stripe_enabled() -> None:
    if not settings.stripe_enabled:
        raise BillingDisabledError("stripe_disabled")


def _get_client() -> stripe.StripeClient:
    """Create a Stripe client (lazy, no global state)."""
    _require_stripe_enabled()
    return stripe.StripeClient(settings.stripe_secret_key)


async def create_checkout_session(
    *,
    user_id: int,
    customer_id: str | None,
    customer_email: str | None,
    price_id: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout Session for subscription mode.

    Returns the checkout session URL for redirect.
    """
    client = _get_client()
    binding = make_checkout_binding(user_id, customer_id)
    params: dict = {
        "mode": "subscription",
        "client_reference_id": str(user_id),
        "metadata": binding,
        "subscription_data": {"metadata": binding},
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    # Use existing customer if available, otherwise pass email
    if customer_id:
        params["customer"] = customer_id
    elif customer_email:
        params["customer_email"] = customer_email

    session = await client.checkout.sessions.create_async(params)
    return session.url


async def create_portal_session(
    *,
    customer_id: str,
    return_url: str,
) -> str:
    """Create a Stripe Customer Portal session.

    Returns the portal URL for redirect.
    """
    client = _get_client()
    portal = await client.billing_portal.sessions.create_async(
        {
            "customer": customer_id,
            "return_url": return_url,
        }
    )
    return portal.url


def verify_webhook_signature(
    payload: bytes,
    sig_header: str,
) -> dict | None:
    """Verify Stripe webhook signature and return the event dict.

    Returns None if signature is invalid.
    """
    _require_stripe_enabled()
    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.stripe_webhook_secret,
        )
        return event
    except (ValueError, stripe.SignatureVerificationError):
        logger.warning("stripe_webhook_signature_invalid")
        return None


class BillingBindingError(ValueError):
    """A conflicting or unproved account binding; never resolved by email."""


def make_checkout_binding(user_id: int, customer_id: str | None) -> dict[str, str]:
    payload = json.dumps(
        {
            "v": 1,
            "uid": user_id,
            "customer": customer_id or "",
            "nonce": secrets.token_hex(16),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {"dc_binding": payload, "dc_signature": _binding_signature(payload)}


def _binding_signature(payload: str) -> str:
    return hmac.new(
        settings.jwt_secret_key.encode(),
        b"datacommune.billing.checkout.v1\x00" + payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def read_checkout_binding(metadata: dict) -> dict | None:
    if not metadata.get("dc_binding") and not metadata.get("dc_signature"):
        return None
    payload = metadata.get("dc_binding", "")
    signature = metadata.get("dc_signature", "")
    if (
        not isinstance(payload, str)
        or not isinstance(signature, str)
        or not hmac.compare_digest(_binding_signature(payload), signature)
    ):
        raise BillingBindingError("invalid_checkout_binding")
    try:
        binding = json.loads(payload)
        if (
            binding["v"] != 1
            or type(binding["uid"]) is not int
            or binding["uid"] <= 0
            or not isinstance(binding["customer"], str)
            or len(binding["nonce"]) != 32
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise BillingBindingError("invalid_checkout_binding") from exc
    return binding


async def retrieve_subscription(subscription_id: str) -> dict:
    """Read the current provider state, bounded while holding local locks."""
    async with asyncio.timeout(10):
        subscription = await _get_client().subscriptions.retrieve_async(subscription_id)
    # The pinned Stripe SDK exposes periods on the single subscription item.
    return (
        subscription.to_dict()
        if hasattr(subscription, "to_dict")
        else dict(subscription)
    )
