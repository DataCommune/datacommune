"""Stripe billing endpoints: webhook, checkout session, portal session."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.billing.plans import (
    PLANS,
    get_plans,
    is_internal_unlimited_plan,
)
from app.billing.queries import (
    get_active_subscription,
    get_stripe_customer_id,
)
from app.billing.reconciliation import process_event
from app.billing.service import (
    BillingBindingError,
    create_checkout_session,
    create_portal_session,
    verify_webhook_signature,
)
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])


def _billing_disabled_response() -> JSONResponse | None:
    if settings.stripe_enabled:
        return None
    return JSONResponse(
        status_code=503,
        content={"detail": "billing_disabled_for_environment"},
    )


# ---------------------------------------------------------------------------
# Webhook (public — called by Stripe)
# ---------------------------------------------------------------------------


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Receive and process Stripe webhook events."""
    if disabled := _billing_disabled_response():
        return disabled
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    event = verify_webhook_signature(payload, sig_header)
    if event is None:
        return JSONResponse(
            status_code=400,
            content={"detail": "invalid_signature"},
        )

    try:
        status = await process_event(db, event)
    except BillingBindingError:
        await db.rollback()
        return JSONResponse(
            status_code=409,
            content={"detail": "billing_identity_conflict", "retryable": True},
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "stripe_reconciliation_incomplete", extra={"event_id": event.get("id")}
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "billing_reconciliation_incomplete", "retryable": True},
        )
    return JSONResponse(content={"status": status})


# ---------------------------------------------------------------------------
# Plans (public — no auth required)
# ---------------------------------------------------------------------------


@router.get("/billing/plans")
async def billing_plans() -> JSONResponse:
    """Return available plans with pricing info (no Stripe IDs exposed)."""
    result = {}
    for name, plan in PLANS.items():
        result[name] = {
            "display_name": plan["display_name"],
            "quota_monthly": plan["quota_monthly"],
            "price_eur_monthly": plan["price_eur_monthly"],
            "price_eur_annual": plan["price_eur_annual"],
            "rate_limit": min(plan["rate_limit"], settings.rate_limit_per_ip),
            "plan_rate_limit": plan["rate_limit"],
            "shared_ip_rate_limit": settings.rate_limit_per_ip,
            "rate_limit_scope": "par processus ; plafond IP partagé entre les clés",
            "quota_unit": "appel admis, même si le traitement retourne une erreur",
            "quota_month": "UTC",
        }
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Checkout (authenticated — creates Stripe Checkout session)
# ---------------------------------------------------------------------------


class _CheckoutRequest(BaseModel):
    plan: str
    interval: str = "month"  # "month" or "year"


@router.post("/billing/checkout")
async def billing_checkout(
    body: _CheckoutRequest,
    user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Create a Stripe Checkout session and return the URL."""
    if disabled := _billing_disabled_response():
        return disabled
    active_sub = await get_active_subscription(db, user.id)
    if active_sub and is_internal_unlimited_plan(active_sub["plan_name"]):
        return JSONResponse(
            status_code=403,
            content={"error": "Le compte interne ne peut pas changer de plan"},
        )

    plan = body.plan
    interval = body.interval

    if interval not in ("month", "year"):
        return JSONResponse(
            status_code=400,
            content={"error": "Interval invalide (month ou year)"},
        )

    plans = get_plans()
    plan_info = plans.get(plan)

    if not plan_info:
        return JSONResponse(
            status_code=400,
            content={"error": "Plan invalide"},
        )

    price_key = "price_id_annual" if interval == "year" else "price_id_monthly"
    price_id = plan_info[price_key]

    if not price_id:
        return JSONResponse(
            status_code=400,
            content={"error": f"Plan {plan} ({interval}) non configure"},
        )

    customer_id = await get_stripe_customer_id(db, user.id)

    checkout_url = await create_checkout_session(
        user_id=user.id,
        customer_id=customer_id,
        customer_email=user.email if not customer_id else None,
        price_id=price_id,
        success_url=(
            f"{settings.dashboard_url}/billing?session_id={{CHECKOUT_SESSION_ID}}"
        ),
        cancel_url=f"{settings.dashboard_url}/billing?cancelled=1",
    )

    return JSONResponse(content={"url": checkout_url})


# ---------------------------------------------------------------------------
# Portal (authenticated — opens Stripe Customer Portal)
# ---------------------------------------------------------------------------


@router.post("/billing/portal")
async def billing_portal(
    user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Create a Stripe Customer Portal session and return the URL."""
    if disabled := _billing_disabled_response():
        return disabled
    customer_id = await get_stripe_customer_id(db, user.id)

    if not customer_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Aucun abonnement actif"},
        )

    portal_url = await create_portal_session(
        customer_id=customer_id,
        return_url=f"{settings.dashboard_url}/billing",
    )

    return JSONResponse(content={"url": portal_url})
