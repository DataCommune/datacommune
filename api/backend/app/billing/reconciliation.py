"""COR-17: serialized provider reconciliation and atomic local publication.

Lock order: event (webhook only), customer, subscription, account. Admin grants
only acquire account; no path acquires a customer/subscription after account.
No network mutation or commit is performed by the reconciliation helper.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import queries, service
from app.billing.plans import get_plan_by_price_id, get_quota_for_plan


class BillingRetryableError(RuntimeError):
    """No event is acknowledged when reconciliation is incomplete."""


def _identifier(value) -> str:
    return value if isinstance(value, str) else (value or {}).get("id", "")


async def effective_quota(db: AsyncSession, user_id: int) -> tuple[str, int]:
    subscription = await queries.get_active_subscription(db, user_id)
    plan = subscription["plan_name"] if subscription else "free"
    return plan, get_quota_for_plan(plan)


async def reconcile_subscription(  # noqa: C901
    db: AsyncSession,
    subscription_id: str,
    customer_id: str,
    *,
    checkout_metadata: dict | None = None,
    expected_user_id: int | None = None,
) -> dict:
    if not subscription_id or not customer_id:
        raise service.BillingBindingError("missing_provider_identity")
    await queries.lock_billing_entity(db, "customer", customer_id)
    await queries.lock_billing_entity(db, "subscription", subscription_id)
    sub = await service.retrieve_subscription(subscription_id)
    if (
        sub.get("id") != subscription_id
        or _identifier(sub.get("customer")) != customer_id
    ):
        raise service.BillingBindingError("provider_customer_conflict")
    binding = service.read_checkout_binding(sub.get("metadata") or {})
    if checkout_metadata is not None:
        checkout_binding = service.read_checkout_binding(checkout_metadata)
        if checkout_binding is not None and checkout_binding != binding:
            raise service.BillingBindingError("checkout_subscription_binding_conflict")
    result = await db.execute(
        text("SELECT id FROM users WHERE stripe_customer_id = :customer"),
        {"customer": customer_id},
    )
    customer_owner = result.scalar_one_or_none()
    user_id = binding["uid"] if binding else customer_owner
    if (
        user_id is None
        or (customer_owner is not None and customer_owner != user_id)
        or (expected_user_id is not None and expected_user_id != user_id)
    ):
        raise service.BillingBindingError("unproved_account_binding")
    if binding and binding["customer"] and binding["customer"] != customer_id:
        raise service.BillingBindingError("signed_customer_conflict")
    await queries.lock_billing_entity(db, "account", user_id)
    result = await db.execute(
        text("SELECT stripe_customer_id FROM users WHERE id = :uid FOR UPDATE"),
        {"uid": user_id},
    )
    account = result.first()
    if account is None or (account[0] is not None and account[0] != customer_id):
        raise service.BillingBindingError("linked_customer_conflict")
    result = await db.execute(
        text(
            "SELECT user_id, source, plan_name, status FROM subscriptions "
            "WHERE stripe_subscription_id = :sid"
        ),
        {"sid": subscription_id},
    )
    existing = result.first()
    if existing and (existing[0] != user_id or existing[1] != "stripe"):
        raise service.BillingBindingError("subscription_owner_conflict")
    items = (sub.get("items") or {}).get("data", [])
    if len(items) != 1 or (sub.get("items") or {}).get("has_more"):
        raise BillingRetryableError("unsupported_subscription_items")
    item = items[0]
    price_id = _identifier(item.get("price"))
    plan_result = get_plan_by_price_id(price_id)
    if plan_result is None:
        raise BillingRetryableError("unknown_price")
    plan_name, _ = plan_result
    status = sub.get("status")
    if status not in {
        "active",
        "trialing",
        "past_due",
        "canceled",
        "incomplete",
        "incomplete_expired",
        "unpaid",
        "paused",
    }:
        raise BillingRetryableError("unknown_subscription_status")
    start = item.get("current_period_start", sub.get("current_period_start"))
    end = item.get("current_period_end", sub.get("current_period_end"))
    if not account[0]:
        await queries.set_stripe_customer_id(db, user_id, customer_id)
    await queries.upsert_subscription(
        db,
        user_id=user_id,
        stripe_subscription_id=subscription_id,
        stripe_price_id=price_id,
        plan_name=plan_name,
        status=status,
        period_start=datetime.fromtimestamp(start, UTC) if start is not None else None,
        period_end=datetime.fromtimestamp(end, UTC) if end is not None else None,
        cancel_at_period_end=bool(sub.get("cancel_at_period_end", False)),
    )
    effective_plan, quota = await effective_quota(db, user_id)
    await queries.update_user_keys_quota(db, user_id, quota)
    return {
        "_before": {
            "plan_name": existing[2],
            "status": existing[3],
            "source": existing[1],
        }
        if existing
        else None,
        "plan_name": plan_name,
        "status": status,
        "source": "stripe",
        "effective_plan": effective_plan,
        "quota_monthly": quota,
        "current_period_end": datetime.fromtimestamp(end, UTC).isoformat()
        if end is not None
        else None,
    }


async def process_event(db: AsyncSession, event: dict) -> str:
    """Own one transaction including the event receipt; retries re-read Stripe."""
    event_id, event_type = event["id"], event["type"]
    async with db.begin():
        await queries.lock_billing_entity(db, "event", event_id)
        if await queries.is_event_processed(db, event_id):
            return "already_processed"
        obj = event["data"]["object"]
        if event_type == "checkout.session.completed":
            await reconcile_subscription(
                db,
                _identifier(obj.get("subscription")),
                _identifier(obj.get("customer")),
                checkout_metadata=obj.get("metadata") or {},
            )
        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            await reconcile_subscription(
                db, obj.get("id", ""), _identifier(obj.get("customer"))
            )
        elif event_type == "invoice.payment_failed":
            subscription_id = _identifier(
                obj.get("subscription")
                or ((obj.get("parent") or {}).get("subscription_details") or {}).get(
                    "subscription"
                )
            )
            if subscription_id:
                await reconcile_subscription(
                    db, subscription_id, _identifier(obj.get("customer"))
                )
            logging.getLogger(__name__).warning(
                "invoice_payment_failed", extra={"event_id": event_id}
            )
        await queries.mark_event_processed(db, event_id, event_type)
    return "ok"
