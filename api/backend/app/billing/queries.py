"""Database queries for subscription management and event deduplication."""

from datetime import datetime
from functools import wraps

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Single policy shared by account readers and admin SQL projections.
def effective_subscription_order(alias: str = "") -> str:
    if alias not in {"", "s"}:
        raise ValueError("unsupported subscription alias")
    prefix = f"{alias}." if alias else ""
    return (
        f"({prefix}plan_name = 'internal') DESC, "
        f"({prefix}source = 'manual_grant') DESC NULLS LAST, "
        f"{prefix}created_at DESC, {prefix}id DESC"
    )


async def lock_billing_entity(
    db: AsyncSession, kind: str, identifier: str | int
) -> None:
    """Transaction-scoped serialization; caller owns commit/rollback."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 17017))"),
        {"key": f"billing:{kind}:{identifier}"},
    )


async def is_event_processed(db: AsyncSession, event_id: str) -> bool:
    """Check if a Stripe event has already been processed."""
    result = await db.execute(
        text("SELECT 1 FROM stripe_events WHERE event_id = :eid"),
        {"eid": event_id},
    )
    return result.first() is not None


async def mark_event_processed(
    db: AsyncSession,
    event_id: str,
    event_type: str,
) -> None:
    """Record a processed Stripe event for deduplication."""
    await db.execute(
        text("""
            INSERT INTO stripe_events (event_id, event_type)
            VALUES (:eid, :etype)
            ON CONFLICT (event_id) DO NOTHING
        """),
        {"eid": event_id, "etype": event_type},
    )


async def upsert_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    stripe_subscription_id: str,
    stripe_price_id: str,
    plan_name: str,
    status: str,
    period_start: datetime | None,
    period_end: datetime | None,
    cancel_at_period_end: bool,
) -> int:
    """Insert or update a subscription. Returns subscription id.

    Writes `source='stripe'` unconditionally -- canonical Stripe-origin writer.
    """
    result = await db.execute(
        text("""
            INSERT INTO subscriptions (
                user_id, stripe_subscription_id, stripe_price_id,
                plan_name, status,
                current_period_start, current_period_end,
                cancel_at_period_end, source, updated_at
            )
            VALUES (
                :uid, :sub_id, :price_id,
                :plan, :status,
                :period_start, :period_end,
                :cancel_end, 'stripe', NOW()
            )
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                stripe_price_id = EXCLUDED.stripe_price_id,
                plan_name = EXCLUDED.plan_name,
                status = EXCLUDED.status,
                current_period_start = EXCLUDED.current_period_start,
                current_period_end = EXCLUDED.current_period_end,
                cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                source = EXCLUDED.source,
                updated_at = NOW()
            RETURNING id
        """),
        {
            "uid": user_id,
            "sub_id": stripe_subscription_id,
            "price_id": stripe_price_id,
            "plan": plan_name,
            "status": status,
            "period_start": period_start,
            "period_end": period_end,
            "cancel_end": cancel_at_period_end,
        },
    )
    sub_id = result.scalar_one()
    return sub_id


async def get_active_subscription(
    db: AsyncSession,
    user_id: int,
) -> dict | None:
    """Get the active subscription for a user, if any."""
    result = await db.execute(
        text(f"""
            SELECT id, user_id, stripe_subscription_id, stripe_price_id,
                   plan_name, status, current_period_start, current_period_end,
                   cancel_at_period_end, source, created_at, updated_at
            FROM subscriptions
            WHERE user_id = :uid AND status = 'active'
            ORDER BY {effective_subscription_order()}
            LIMIT 1
        """),
        {"uid": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def delete_subscription_by_stripe_id(
    db: AsyncSession,
    stripe_subscription_id: str,
) -> int | None:
    """Mark a subscription as canceled. Returns user_id or None."""
    result = await db.execute(
        text("""
            UPDATE subscriptions
            SET status = 'canceled', updated_at = NOW()
            WHERE stripe_subscription_id = :sub_id
            RETURNING user_id
        """),
        {"sub_id": stripe_subscription_id},
    )
    row = result.first()
    return row[0] if row else None


async def set_stripe_customer_id(
    db: AsyncSession,
    user_id: int,
    stripe_customer_id: str,
) -> None:
    """Set the Stripe customer ID on a user."""
    await db.execute(
        text("""
            UPDATE users
            SET stripe_customer_id = :cust_id, updated_at = NOW()
            WHERE id = :uid
        """),
        {"cust_id": stripe_customer_id, "uid": user_id},
    )


async def get_stripe_customer_id(
    db: AsyncSession,
    user_id: int,
) -> str | None:
    """Get the Stripe customer ID for a user."""
    result = await db.execute(
        text("SELECT stripe_customer_id FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    return result.scalar_one_or_none()


async def update_user_keys_quota(
    db: AsyncSession,
    user_id: int,
    quota: int,
) -> int:
    """Update quota_monthly for all non-revoked keys of a user.

    Returns the number of keys updated.
    """
    result = await db.execute(
        text("""
            UPDATE api_keys
            SET quota_monthly = :quota
            WHERE user_id = :uid AND NOT revoked
        """),
        {"quota": quota, "uid": user_id},
    )
    return result.rowcount


def rollback_billing_on_error(function):
    """Admin entrypoints own their commit and roll back every incomplete action."""

    @wraps(function)
    async def wrapped(db, *args, **kwargs):
        try:
            return await function(db, *args, **kwargs)
        except Exception:
            await db.rollback()
            raise

    return wrapped
