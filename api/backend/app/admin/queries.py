# ruff: noqa: E501
"""SQL queries for admin users endpoints."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.admin.schemas import (
    AdminApiKeyRow,
    AdminApiKeysListResponse,  # noqa: F401
    AdminSubscriptionRow,
    EtlPipelineSummary,
    EtlRunRow,
)
from app.admin.service import log_admin_action
from app.auth.email import send_verification_email
from app.auth.models import UserInfo
from app.auth.service import create_verification_token
from app.billing.plans import (
    get_quota_for_plan,
    is_internal_unlimited_plan,
)
from app.billing.queries import (
    effective_subscription_order,
    get_active_subscription,
    get_stripe_customer_id,
    lock_billing_entity,
    rollback_billing_on_error,
    update_user_keys_quota,
)
from app.billing.reconciliation import effective_quota, reconcile_subscription
from app.billing.service import BillingBindingError
from app.common.exceptions import ErreurApiError
from app.scheduler.jobs import PIPELINE_MAP

logger = logging.getLogger(__name__)

# Per-user cooldown for resend-verification — prevents inbox spam.
# Mirrors the _QUICKKEY_COOLDOWN pattern in auth/router.py.
_RESEND_VERIF_COOLDOWN_SECONDS = 5 * 60
_resend_verif_last_sent: dict[int, float] = {}

# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------

_LIST_SELECT = f"""
    SELECT
        u.id,
        u.email,
        u.name,
        u.is_admin,
        u.is_verified              AS email_verified,
        u.created_at,
        u.last_login,
        COALESCE(s.plan_name, 'free') AS plan,
        COALESCE(qa.request_count, 0)  AS quota_used,
        (
            SELECT COUNT(*)
            FROM api_keys k
            WHERE k.user_id = u.id AND NOT k.revoked AND NOT k.is_default
        ) AS api_keys_count
    FROM users u
    LEFT JOIN LATERAL (
        SELECT plan_name
        FROM subscriptions
        WHERE user_id = u.id AND status = 'active'
        ORDER BY {effective_subscription_order()}
        LIMIT 1
    ) s ON true
    LEFT JOIN quota_usage_account qa
        ON qa.user_id = u.id AND qa.month = TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM')
"""

_LIST_WHERE = """
    WHERE
        (CAST(:search AS TEXT) IS NULL
         OR u.email ILIKE '%' || CAST(:search AS TEXT) || '%'
         OR u.id::text = CAST(:search AS TEXT))
        AND (CAST(:plan AS TEXT) IS NULL OR COALESCE(s.plan_name, 'free') = CAST(:plan AS TEXT))
        AND (CAST(:is_admin AS BOOLEAN) IS NULL OR u.is_admin = CAST(:is_admin AS BOOLEAN))
        AND (CAST(:email_verified AS BOOLEAN) IS NULL OR u.is_verified = CAST(:email_verified AS BOOLEAN))
"""


# Whitelisted sort fields → SQL column/alias (validated input only).
# Keep in sync with _SORT_PATTERN in app/admin/users.py.
_SORT_COLUMNS = {
    "email": "u.email",
    "plan": "plan",
    "quota": "quota_used",
    "api_keys_count": "api_keys_count",
    "created_at": "u.created_at",
    "last_login": "u.last_login",
}


def _build_order_by(sort: str | None) -> str:
    """Parse a validated `{field}_{asc|desc}` token into a safe ORDER BY clause."""
    if not sort:
        return "ORDER BY u.created_at DESC"
    field, _, direction = sort.rpartition("_")
    column = _SORT_COLUMNS.get(field)
    if not column:
        return "ORDER BY u.created_at DESC"
    direction_sql = "ASC" if direction == "asc" else "DESC"
    # last_login may be NULL for never-logged-in users; push nulls to the end.
    nulls = " NULLS LAST" if field == "last_login" else ""
    return f"ORDER BY {column} {direction_sql}{nulls}"


async def list_admin_users(
    db: AsyncSession,
    search: str | None,
    plan: str | None,
    is_admin: bool | None,
    email_verified: bool | None,
    sort: str | None,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, total) for the admin users table.

    Both the rows query and the count query share the same WHERE clause.
    quota_monthly is derived from get_quota_for_plan(plan) — the nominal plan quota,
    not the individual api_key.quota_monthly (which may differ after manual bumps).
    """
    params: dict[str, Any] = {
        "search": search,
        "plan": plan,
        "is_admin": is_admin,
        "email_verified": email_verified,
        "offset": offset,
        "limit": limit,
    }

    rows_sql = text(
        _LIST_SELECT
        + _LIST_WHERE
        + f" {_build_order_by(sort)} OFFSET :offset LIMIT :limit"
    )
    count_sql = text(
        "SELECT COUNT(*) FROM users u"
        " LEFT JOIN LATERAL ("
        "   SELECT plan_name FROM subscriptions"
        "   WHERE user_id = u.id AND status = 'active'"
        f"   ORDER BY {effective_subscription_order()} LIMIT 1"
        " ) s ON true"
        " LEFT JOIN quota_usage_account qa"
        "   ON qa.user_id = u.id AND qa.month = TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM')"
        + _LIST_WHERE
    )

    rows_result = await db.execute(rows_sql, params)
    rows_raw = [dict(r) for r in rows_result.mappings().all()]

    count_result = await db.execute(count_sql, params)
    total = count_result.scalar_one()

    # Enrich each row with quota_monthly from plan definition
    rows: list[dict[str, Any]] = []
    for r in rows_raw:
        r["quota_monthly"] = get_quota_for_plan(r["plan"])
        rows.append(r)

    return rows, total


# ---------------------------------------------------------------------------
# User detail
# ---------------------------------------------------------------------------


async def get_admin_user_detail(
    db: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """Return flat dict: user fields + api_keys + subscription + recent_activity.

    Shape: {**user_row, api_keys, subscription, recent_activity}.
    User fields (id, email, name, is_admin, email_verified, created_at, last_login,
    plan, quota_used, quota_monthly, api_keys_count) are spread at the root so the
    response matches AdminUserDetailResponse (which extends AdminUserRow).

    Returns {} if user not found (caller checks via detail.get("id")).
    """
    # 1. User row (same shape as list row)
    user_sql = text(_LIST_SELECT + " WHERE u.id = :uid")
    user_result = await db.execute(user_sql, {"uid": user_id})
    user_row = user_result.mappings().first()
    if user_row is None:
        return {}
    user_dict = dict(user_row)
    user_dict["quota_monthly"] = get_quota_for_plan(user_dict["plan"])

    # 2. API keys
    keys_result = await db.execute(
        text("""
            SELECT
                k.id,
                k.key_prefix                  AS prefix,
                k.name                        AS label,
                COALESCE(q.request_count, 0)  AS quota_used,
                k.quota_monthly,
                NULL::timestamptz             AS last_used_at,
                k.revoked,
                k.is_default,
                k.created_at
            FROM api_keys k
            LEFT JOIN quota_usage q
                ON q.api_key_id = k.id
                AND q.month = TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM')
            WHERE k.user_id = :uid
            ORDER BY k.created_at DESC
        """),
        {"uid": user_id},
    )
    api_keys = [dict(r) for r in keys_result.mappings().all()]

    # 3. The same effective subscription used for admission and the plan summary.
    active = await get_active_subscription(db, user_id)
    subscription = (
        {
            key: active[key]
            for key in (
                "plan_name",
                "status",
                "current_period_end",
                "source",
                "stripe_subscription_id",
            )
        }
        if active
        else None
    )

    # 4. Recent activity from admin_audit_log only (fast — no cross-join on request log)
    activity_result = await db.execute(
        text("""
            SELECT
                id,
                action,
                COALESCE(reason, '') AS reason,
                created_at
            FROM admin_audit_log
            WHERE target_type = 'user' AND target_id = :uid_str
            ORDER BY created_at DESC
            LIMIT 10
        """),
        {"uid_str": str(user_id)},
    )
    recent_activity = [dict(r) for r in activity_result.mappings().all()]

    return {
        **user_dict,
        "api_keys": api_keys,
        "subscription": subscription,
        "recent_activity": recent_activity,
    }


# ---------------------------------------------------------------------------
# Grant plan (transactional)
# ---------------------------------------------------------------------------


# Plan ordering for downgrade detection. Keep in sync with GrantPlanRequest.plan
# and the frontend PLAN_RANK in GrantPlanModal.svelte.
_PLAN_RANK = {"free": 0, "pro": 1, "business": 2, "enterprise": 3}


@rollback_billing_on_error
async def admin_grant_plan(
    db: AsyncSession,
    admin: UserInfo,
    target_user_id: int,
    plan_name: str,
    end_date: datetime | None,
    reason: str,
    request: Request,
    force_downgrade: bool = False,
) -> dict[str, Any]:
    """Grant a plan manually.

    Steps (all in ONE transaction — single db.commit() at end):
    1. Snapshot before state.
    2. Reject downgrades unless `force_downgrade` is set.
    3. Upsert subscription with source='manual_grant'.
    4. Update all non-revoked api_keys.quota_monthly for the user.
    5. Write audit log row via log_admin_action (no commit inside).
    6. db.commit() — atomic.

    Returns the after-snapshot dict for the endpoint response.
    If any step raises, SQLAlchemy async session rolls back (no partial state).
    """
    await lock_billing_entity(db, "account", target_user_id)
    fake_sub_id = f"manual_grant_{target_user_id}"

    # --- Before snapshot ---
    before_result = await db.execute(
        text(f"""
            SELECT
                COALESCE(s.plan_name, 'free') AS plan,
                s.stripe_subscription_id,
                s.status
            FROM users u
            LEFT JOIN LATERAL (
                SELECT plan_name, stripe_subscription_id, status
                FROM subscriptions
                WHERE user_id = u.id
                  AND status = 'active'
                ORDER BY {effective_subscription_order()}
                LIMIT 1
            ) s ON true
            WHERE u.id = :uid
        """),
        {"uid": target_user_id, "internal_plan": "internal"},
    )
    before_row = before_result.mappings().first()
    if before_row is None:
        raise ErreurApiError(
            404,
            "Introuvable",
            f"User {target_user_id} inexistant",
        )
    before: dict[str, Any] = dict(before_row) if before_row else {}

    # Reject silent downgrades. The UI requires an explicit checkbox to send
    # `force_downgrade=true`; direct API callers must opt in the same way.
    current_plan = before.get("plan", "free")
    if is_internal_unlimited_plan(current_plan):
        raise ErreurApiError(
            422,
            "Compte interne protégé",
            "Le plan d'un compte interne ne peut pas être modifié ici.",
        )
    if (
        _PLAN_RANK.get(plan_name, 0) < _PLAN_RANK.get(current_plan, 0)
        and not force_downgrade
    ):
        raise ErreurApiError(
            422,
            "Downgrade non confirme",
            f"Le plan {plan_name} est inferieur au plan actuel {current_plan}. "
            "Renvoyer la requete avec force_downgrade=true pour confirmer.",
        )

    # --- Step 1: Upsert subscription ---
    await db.execute(
        text("""
            INSERT INTO subscriptions (
                user_id,
                stripe_subscription_id,
                stripe_price_id,
                plan_name,
                status,
                current_period_end,
                cancel_at_period_end,
                source
            )
            VALUES (
                :uid,
                :fake_sub_id,
                '',
                :plan,
                'active',
                :end_date,
                false,
                'manual_grant'
            )
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                plan_name            = EXCLUDED.plan_name,
                current_period_end   = EXCLUDED.current_period_end,
                status               = 'active',
                source               = 'manual_grant',
                updated_at           = NOW()
        """),
        {
            "uid": target_user_id,
            "fake_sub_id": fake_sub_id,
            "plan": plan_name,
            "end_date": end_date,
        },
    )

    # Re-derive the quota through the same policy as account/webhook readers.
    effective_plan, quota = await effective_quota(db, target_user_id)
    await update_user_keys_quota(db, target_user_id, quota)

    # --- After snapshot ---
    after: dict[str, Any] = {
        "plan": effective_plan,
        "stripe_subscription_id": fake_sub_id,
        "status": "active",
        "quota_monthly": quota,
        "end_date": end_date.isoformat() if end_date else None,
    }

    # --- Step 3: Audit log (no commit inside) ---
    await log_admin_action(
        db=db,
        admin=admin,
        action="grant_plan",
        target_type="user",
        target_id=str(target_user_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )

    # --- Step 4: Single commit ---
    await db.commit()

    return after


# ---------------------------------------------------------------------------
# Toggle is_admin (transactional)
# ---------------------------------------------------------------------------


async def admin_toggle_is_admin(
    db: AsyncSession,
    admin: UserInfo,
    target_user_id: int,
    new_is_admin: bool,
    reason: str,
    request: Request,
) -> dict[str, Any]:
    """Toggle users.is_admin + write audit log in ONE transaction.

    Steps:
    1. SELECT current is_admin for before snapshot.
    2. UPDATE users SET is_admin = :new.
    3. log_admin_action (no commit inside).
    4. db.commit().

    Returns after-snapshot dict.
    """
    # --- Before snapshot ---
    before_result = await db.execute(
        text(f"""
            SELECT u.is_admin,
                   (
                       SELECT s.plan_name
                       FROM subscriptions s
                       WHERE s.user_id = u.id AND s.status = 'active'
                       ORDER BY {effective_subscription_order("s")}
                       LIMIT 1
                   ) AS plan_name
            FROM users u
            WHERE u.id = :uid
        """),
        {"uid": target_user_id},
    )
    before_row = before_result.mappings().first()
    if before_row is None:
        raise ErreurApiError(
            404,
            "Introuvable",
            f"User {target_user_id} inexistant",
        )
    before: dict[str, Any] = dict(before_row) if before_row else {}
    if not new_is_admin and is_internal_unlimited_plan(before.get("plan_name")):
        raise ErreurApiError(
            422,
            "Compte interne protégé",
            "Les droits administrateur du compte interne ne peuvent pas être révoqués.",
        )

    # --- UPDATE ---
    update_result = await db.execute(
        text(
            "UPDATE users SET is_admin = :new_val, updated_at = NOW() WHERE id = :uid"
        ),
        {"new_val": new_is_admin, "uid": target_user_id},
    )
    if getattr(update_result, "rowcount", None) == 0:
        raise ErreurApiError(
            404,
            "Introuvable",
            f"User {target_user_id} inexistant",
        )

    after: dict[str, Any] = {"is_admin": new_is_admin}

    # --- Audit log (no commit inside) ---
    await log_admin_action(
        db=db,
        admin=admin,
        action="toggle_is_admin",
        target_type="user",
        target_id=str(target_user_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )

    # --- Single commit ---
    await db.commit()

    return after


# ---------------------------------------------------------------------------
# Reset quota (transactional)
# ---------------------------------------------------------------------------


async def admin_reset_quota(
    db: AsyncSession,
    admin: UserInfo,
    target_user_id: int,
    reason: str,
    request: Request,
) -> dict[str, Any]:
    """Reset target user's monthly quota counter to 0 (account-level only).

    Steps (all in ONE transaction — single db.commit() at end):
    1. Validate user exists.
    2. Ensure and lock the monthly quota row, then snapshot its count.
    3. UPDATE quota_usage_account SET request_count = 0 for current month.
    4. Write audit log via log_admin_action (no commit inside).
    5. db.commit() — atomic.

    Only touches quota_usage_account (enforcement layer), NOT quota_usage (per-key).
    A previously unused month gains a zero row to serialize its first admission.
    """
    current_month = datetime.now(UTC).strftime("%Y-%m")

    # --- Validate user exists ---
    user_result = await db.execute(
        text(f"""
            SELECT u.id, u.email,
                   (
                       SELECT s.plan_name
                       FROM subscriptions s
                       WHERE s.user_id = u.id AND s.status = 'active'
                       ORDER BY {effective_subscription_order("s")}
                       LIMIT 1
                   ) AS plan_name
            FROM users u
            WHERE u.id = :uid
        """),
        {"uid": target_user_id},
    )
    user_row = user_result.mappings().first()
    if user_row is None:
        raise ErreurApiError(404, "Introuvable", f"User {target_user_id} inexistant")
    user = dict(user_row)
    if is_internal_unlimited_plan(user.get("plan_name")):
        raise ErreurApiError(
            422,
            "Compte interne protégé",
            "Le compteur du compte interne ne peut pas être réinitialisé ici.",
        )

    # Materialize the shared lock row even before the first admission of a month.
    # Its insertion rolls back with the reset if the audit cannot be persisted.
    await db.execute(
        text("""
            INSERT INTO quota_usage_account (user_id, month, request_count)
            VALUES (:uid, :month, 0)
            ON CONFLICT (user_id, month) DO NOTHING
        """),
        {"uid": target_user_id, "month": current_month},
    )

    # --- Before snapshot, locked until reset and audit commit together ---
    quota_result = await db.execute(
        text("""
            SELECT COALESCE(request_count, 0) AS quota_used
            FROM quota_usage_account
            WHERE user_id = :uid AND month = :month
            FOR UPDATE
        """),
        {"uid": target_user_id, "month": current_month},
    )
    quota_row = quota_result.mappings().first()
    before: dict[str, Any] = {
        "quota_used": quota_row["quota_used"] if quota_row else 0,
        "month": current_month,
    }

    # --- Mutation ---
    await db.execute(
        text("""
            UPDATE quota_usage_account
            SET request_count = 0
            WHERE user_id = :uid AND month = :month
        """),
        {"uid": target_user_id, "month": current_month},
    )

    after: dict[str, Any] = {"quota_used": 0, "month": current_month}

    # --- Audit log (no commit inside) ---
    await log_admin_action(
        db=db,
        admin=admin,
        action="reset_quota",
        target_type="user",
        target_id=str(target_user_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )

    # --- Single commit ---
    await db.commit()

    return after


# ---------------------------------------------------------------------------
# Revoke all API keys (transactional)
# ---------------------------------------------------------------------------


async def admin_revoke_all_keys(
    db: AsyncSession,
    admin: UserInfo,
    target_user_id: int,
    reason: str,
    request: Request,
) -> dict[str, Any]:
    """Revoke ALL non-revoked api_keys of the target user (including is_default).

    Steps (all in ONE transaction — single db.commit() at end):
    1. Validate user exists.
    2. Snapshot active_keys_count before.
    3. Synchronize both revocation state columns for every active key.
    4. Write audit log via log_admin_action (no commit inside).
    5. db.commit() — atomic.

    Intentionally includes is_default=True keys — admin action with explicit reason.
    """
    # --- Validate user exists ---
    user_result = await db.execute(
        text(f"""
            SELECT u.id, u.email,
                   (
                       SELECT s.plan_name
                       FROM subscriptions s
                       WHERE s.user_id = u.id AND s.status = 'active'
                       ORDER BY {effective_subscription_order("s")}
                       LIMIT 1
                   ) AS plan_name
            FROM users u
            WHERE u.id = :uid
        """),
        {"uid": target_user_id},
    )
    user_row = user_result.mappings().first()
    if user_row is None:
        raise ErreurApiError(404, "Introuvable", f"User {target_user_id} inexistant")
    user = dict(user_row)
    if is_internal_unlimited_plan(user.get("plan_name")):
        raise ErreurApiError(
            422,
            "Compte interne protégé",
            "Les clés du compte interne se gèrent via le provisioner sécurisé.",
        )

    # --- Before snapshot ---
    count_result = await db.execute(
        text("""
            SELECT COUNT(*) AS active_keys
            FROM api_keys
            WHERE user_id = :uid AND status = 'active' AND NOT revoked
        """),
        {"uid": target_user_id},
    )
    count_row = count_result.mappings().first()
    before: dict[str, Any] = {
        "active_keys_count": int(count_row["active_keys"]) if count_row else 0,
    }

    # --- Mutation ---
    revoke_result = await db.execute(
        text("""
            UPDATE api_keys
            SET revoked = TRUE, status = 'revoked'
            WHERE user_id = :uid AND status = 'active' AND NOT revoked
            RETURNING id
        """),
        {"uid": target_user_id},
    )
    revoked_count = len(revoke_result.all())

    after: dict[str, Any] = {"revoked_count": revoked_count}

    # --- Audit log (no commit inside) ---
    await log_admin_action(
        db=db,
        admin=admin,
        action="revoke_api_keys",
        target_type="user",
        target_id=str(target_user_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )

    # --- Single commit ---
    await db.commit()

    return after


# ---------------------------------------------------------------------------
# Resend verification email (transactional)
# ---------------------------------------------------------------------------


async def admin_resend_verification(
    db: AsyncSession,
    admin: UserInfo,
    target_user_id: int,
    reason: str,
    request: Request,
) -> dict[str, Any]:
    """Resend verification email to an unverified user.

    Steps:
    1. Validate user exists; 422 if already verified.
    2. Cooldown check (5-min per user_id, in-memory dict).
    3. Generate token, snapshot before/after.
    4. log_admin_action (no commit inside).
    5. db.commit().
    6. Update cooldown dict AFTER commit.
    7. Fire-and-forget asyncio.create_task(send_verification_email(...)) AFTER commit.

    CRITICAL order: commit BEFORE scheduling the email task.
    If email send fails, audit log stays (correct behavior).
    If commit fails, no email task scheduled — no spam on rollback.
    """
    # --- Validate user exists + fetch email + is_verified ---
    user_result = await db.execute(
        text("SELECT id, email, is_verified FROM users WHERE id = :uid"),
        {"uid": target_user_id},
    )
    user_row = user_result.mappings().first()
    if user_row is None:
        raise ErreurApiError(404, "Introuvable", f"User {target_user_id} inexistant")

    if user_row["is_verified"] is True:
        raise ErreurApiError(422, "Déjà vérifié", "Email déjà vérifié")

    # --- Cooldown check ---
    # Use `None` sentinel rather than 0.0 default: on Linux `time.monotonic()`
    # starts from boot, so a fresh runner can have `now < 300` and a default
    # of 0.0 would wrongly trigger the cooldown on the first-ever call.
    now = time.monotonic()
    last = _resend_verif_last_sent.get(target_user_id)
    if last is not None and now - last < _RESEND_VERIF_COOLDOWN_SECONDS:
        remaining = int(_RESEND_VERIF_COOLDOWN_SECONDS - (now - last))
        raise ErreurApiError(
            429,
            "Trop fréquent",
            f"Renvoi possible dans {remaining}s",
        )

    # --- Snapshots ---
    before: dict[str, Any] = {"is_verified": False}
    token = create_verification_token(user_row["email"])
    after: dict[str, Any] = {"email_sent": True, "email": user_row["email"]}

    # --- Audit log (no commit inside) ---
    await log_admin_action(
        db=db,
        admin=admin,
        action="resend_verification",
        target_type="user",
        target_id=str(target_user_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )

    # --- Single commit ---
    await db.commit()

    # --- Update cooldown dict AFTER successful commit ---
    _resend_verif_last_sent[target_user_id] = now

    # --- Fire-and-forget email AFTER commit ---
    asyncio.create_task(send_verification_email(user_row["email"], token))  # noqa: RUF006

    return after


# ---------------------------------------------------------------------------
# Phase 17 — API keys cross-user management
# ---------------------------------------------------------------------------


async def list_admin_api_keys(
    db: AsyncSession,
    owner_email: str | None,
    revoked: bool | None,
    page: int,
    page_size: int,
) -> tuple[list[AdminApiKeyRow], int]:
    """Cross-user api_keys listing.

    last_used_at is derived via LATERAL subquery on api_request_log (7-day retention —
    NULL if no activity in that window). See research Pitfall 3.
    quota_used for the CURRENT calendar month from quota_usage (per-key counter).
    """
    offset = (page - 1) * page_size
    params = {
        "owner_email": owner_email,
        "revoked": revoked,
        "offset": offset,
        "limit": page_size,
    }

    data_sql = text("""
        SELECT
            k.id,
            k.key_prefix            AS prefix,
            k.name                  AS label,
            k.user_id,
            u.email                 AS owner_email,
            k.quota_monthly,
            COALESCE(q.request_count, 0) AS quota_used,
            k.revoked,
            k.is_default,
            k.created_at,
            lu.last_used_at
        FROM api_keys k
        LEFT JOIN users u ON u.id = k.user_id
        LEFT JOIN quota_usage q
            ON q.api_key_id = k.id
           AND q.month = TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM')
        LEFT JOIN LATERAL (
            SELECT MAX(ts) AS last_used_at
            FROM api_request_log
            WHERE api_key_id = k.id
        ) lu ON TRUE
        WHERE
            (CAST(:owner_email AS TEXT) IS NULL OR u.email ILIKE '%' || CAST(:owner_email AS TEXT) || '%')
            AND (CAST(:revoked AS BOOLEAN) IS NULL OR k.revoked = CAST(:revoked AS BOOLEAN))
        ORDER BY k.created_at DESC
        OFFSET :offset LIMIT :limit
    """)
    count_sql = text("""
        SELECT COUNT(*) FROM api_keys k
        LEFT JOIN users u ON u.id = k.user_id
        WHERE
            (CAST(:owner_email AS TEXT) IS NULL OR u.email ILIKE '%' || CAST(:owner_email AS TEXT) || '%')
            AND (CAST(:revoked AS BOOLEAN) IS NULL OR k.revoked = CAST(:revoked AS BOOLEAN))
    """)

    data_result = await db.execute(data_sql, params)
    rows = [AdminApiKeyRow(**dict(r)) for r in data_result.mappings().all()]

    count_result = await db.execute(count_sql, params)
    total = int(count_result.scalar_one() or 0)

    return rows, total


async def admin_revoke_single_key(
    db: AsyncSession,
    admin: UserInfo,
    key_id: int,
    reason: str,
    request: Request,
) -> dict[str, Any]:
    """Revoke ONE api_key (cross-user admin action).

    Self-protection: admin cannot revoke their own keys (lockout risk).
    Includes is_default=TRUE keys — admin action is explicit.
    """
    row = await db.execute(
        text(f"""
            SELECT k.id, k.key_prefix, k.user_id, k.revoked, k.is_default,
                   k.quota_monthly, u.email AS owner_email,
                   (
                       SELECT s.plan_name
                       FROM subscriptions s
                       WHERE s.user_id = k.user_id AND s.status = 'active'
                       ORDER BY {effective_subscription_order("s")}
                       LIMIT 1
                   ) AS owner_plan
            FROM api_keys k
            LEFT JOIN users u ON u.id = k.user_id
            WHERE k.id = :kid
        """),
        {"kid": key_id},
    )
    key = row.mappings().first()
    if key is None:
        raise ErreurApiError(404, "Clé introuvable", f"Clé {key_id} inexistante")
    if key["revoked"]:
        raise ErreurApiError(422, "Déjà révoquée", "Cette clé est déjà révoquée")
    if key["user_id"] == admin.id:
        raise ErreurApiError(422, "Action interdite", "cannot revoke own keys")
    if is_internal_unlimited_plan(key["owner_plan"]):
        raise ErreurApiError(
            422,
            "Compte interne protégé",
            "La clé interne se gère via le provisioner sécurisé.",
        )

    before = {
        "revoked": False,
        "is_default": bool(key["is_default"]),
        "owner_email": key["owner_email"],
        "key_prefix": key["key_prefix"],
    }

    await db.execute(
        text("""
            UPDATE api_keys
            SET revoked = TRUE, status = 'revoked'
            WHERE id = :kid
        """),
        {"kid": key_id},
    )
    after: dict[str, Any] = {"revoked": True}

    await log_admin_action(
        db=db,
        admin=admin,
        action="revoke_api_key",
        target_type="api_key",
        target_id=str(key_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )
    await db.commit()
    return after


async def admin_update_key_quota(
    db: AsyncSession,
    admin: UserInfo,
    key_id: int,
    new_quota: int,
    reason: str,
    request: Request,
) -> dict[str, Any]:
    """Update quota_monthly on ONE api_key.

    Guards:
      - 404 if key not found
      - 422 if key revoked (changing quota on dead keys makes no sense)
      - 422 if key belongs to the admin themselves (self-protection)
    Note: Pydantic UpdateKeyQuotaRequest already clamps [0, 10_000_000].
    """
    row = await db.execute(
        text(f"""
            SELECT k.id, k.user_id, k.quota_monthly, k.revoked, k.is_default,
                   u.email AS owner_email,
                   (
                       SELECT s.plan_name
                       FROM subscriptions s
                       WHERE s.user_id = k.user_id AND s.status = 'active'
                       ORDER BY {effective_subscription_order("s")}
                       LIMIT 1
                   ) AS owner_plan
            FROM api_keys k
            LEFT JOIN users u ON u.id = k.user_id
            WHERE k.id = :kid
        """),
        {"kid": key_id},
    )
    key = row.mappings().first()
    if key is None:
        raise ErreurApiError(404, "Clé introuvable", f"Clé {key_id} inexistante")
    if key["revoked"]:
        raise ErreurApiError(
            422,
            "Clé révoquée",
            "Impossible de modifier le quota d'une clé révoquée",
        )
    if key["user_id"] == admin.id:
        raise ErreurApiError(422, "Action interdite", "cannot modify own keys quota")
    if is_internal_unlimited_plan(key["owner_plan"]):
        raise ErreurApiError(
            422,
            "Compte interne protégé",
            "Le quota d'une clé interne ne peut pas être modifié.",
        )

    before: dict[str, Any] = {
        "quota_monthly": int(key["quota_monthly"]),
        "owner_email": key["owner_email"],
    }

    await db.execute(
        text("UPDATE api_keys SET quota_monthly = :q WHERE id = :kid"),
        {"q": new_quota, "kid": key_id},
    )
    after: dict[str, Any] = {"quota_monthly": new_quota}

    await log_admin_action(
        db=db,
        admin=admin,
        action="update_key_quota",
        target_type="api_key",
        target_id=str(key_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )
    await db.commit()
    return after


# ---------------------------------------------------------------------------
# Phase 18 -- Billing Hardening (/admin/subscriptions)
# ---------------------------------------------------------------------------


async def list_admin_subscriptions(
    db: AsyncSession,
    source: str | None,
    plan_name: str | None,
    status: str | None,
    user_email: str | None,
    page: int,
    page_size: int,
) -> tuple[list[AdminSubscriptionRow], int]:
    """Cross-user subscriptions listing with filters.

    Filters on source ('stripe'|'manual_grant'), plan_name, status, and
    user_email ILIKE (partial match). Pagination identical to other admin
    list endpoints. No audit log -- reads are never audited per Phase 15
    locked decision.

    CAST(:name AS TYPE) IS NULL rather than :name::type IS NULL -- asyncpg
    SQLAlchemy text() fails to parse the double-colon syntax (Phase 17 UAT
    bugfix, STATE.md 2026-04-16).
    """
    offset = (page - 1) * page_size
    params = {
        "source": source,
        "plan_name": plan_name,
        "status": status,
        "user_email": user_email,
        "offset": offset,
        "limit": page_size,
    }

    data_sql = text("""
        SELECT
            s.id,
            s.user_id,
            u.email                 AS user_email,
            s.plan_name,
            s.status,
            s.source,
            s.stripe_subscription_id,
            s.current_period_start,
            s.current_period_end,
            s.cancel_at_period_end,
            s.created_at,
            s.updated_at
        FROM subscriptions s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE
            (CAST(:source AS TEXT) IS NULL OR s.source = CAST(:source AS TEXT))
            AND (CAST(:plan_name AS TEXT) IS NULL OR s.plan_name = CAST(:plan_name AS TEXT))
            AND (CAST(:status AS TEXT) IS NULL OR s.status = CAST(:status AS TEXT))
            AND (
                CAST(:user_email AS TEXT) IS NULL
                OR u.email ILIKE '%' || CAST(:user_email AS TEXT) || '%'
            )
        ORDER BY s.created_at DESC
        OFFSET :offset LIMIT :limit
    """)
    count_sql = text("""
        SELECT COUNT(*)
          FROM subscriptions s
          LEFT JOIN users u ON u.id = s.user_id
         WHERE
            (CAST(:source AS TEXT) IS NULL OR s.source = CAST(:source AS TEXT))
            AND (CAST(:plan_name AS TEXT) IS NULL OR s.plan_name = CAST(:plan_name AS TEXT))
            AND (CAST(:status AS TEXT) IS NULL OR s.status = CAST(:status AS TEXT))
            AND (
                CAST(:user_email AS TEXT) IS NULL
                OR u.email ILIKE '%' || CAST(:user_email AS TEXT) || '%'
            )
    """)

    data_result = await db.execute(data_sql, params)
    rows = [AdminSubscriptionRow(**dict(r)) for r in data_result.mappings().all()]

    count_result = await db.execute(count_sql, params)
    total = int(count_result.scalar_one() or 0)

    return rows, total


def _ts_to_dt_admin(ts: int | None) -> datetime | None:
    """Convert a Unix timestamp to a timezone-aware datetime.

    Duplicated locally from billing/router.py to avoid a reverse import
    from admin/ into billing/router.py (research Open Question 3). Three
    lines, no maintenance cost.
    """
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


@rollback_billing_on_error
async def admin_sync_subscription_from_stripe(
    db: AsyncSession,
    admin: UserInfo,
    sub_id: int,
    reason: str,
    request: Request,
) -> dict[str, Any]:
    """Use the same locked canonical reconciliation; audit shares its commit."""
    result = await db.execute(
        text(
            "SELECT user_id, stripe_subscription_id, source FROM subscriptions WHERE id=:sid"
        ),
        {"sid": sub_id},
    )
    row = result.mappings().first()
    if row is None:
        raise ErreurApiError(404, "Introuvable", f"Subscription {sub_id} inexistante")
    stripe_id = row["stripe_subscription_id"]
    if (
        row["source"] == "manual_grant"
        or not stripe_id
        or stripe_id.startswith("manual_grant_")
    ):
        raise ErreurApiError(
            422, "Action interdite", "Impossible de sync une subscription manuelle"
        )
    customer = await get_stripe_customer_id(db, row["user_id"])
    try:
        after = await reconcile_subscription(
            db, stripe_id, customer or "", expected_user_id=row["user_id"]
        )
    except BillingBindingError as exc:
        raise ErreurApiError(
            409, "Identité incohérente", "Liaison Stripe non prouvée"
        ) from exc
    except Exception as exc:
        raise ErreurApiError(
            502, "Erreur Stripe API", "Réconciliation incomplète, réessayer"
        ) from exc
    before = after.pop("_before", None)
    await log_admin_action(
        db=db,
        admin=admin,
        action="sync_subscription",
        target_type="subscription",
        target_id=str(sub_id),
        before=before,
        after=after,
        reason=reason,
        request=request,
    )
    await db.commit()
    return after


# ---------------------------------------------------------------------------
# Phase 19 — Admin Ops (/admin/system)
# ---------------------------------------------------------------------------

import shutil  # noqa: E402 — placed here to keep system helpers co-located

from app.admin.schemas import (  # noqa: E402 — second local import block
    DbHealth,
    DiskUsageRow,
    RateLimitBucket,
    ServiceHealth,
    SystemStatus,
)


async def get_db_health(db: AsyncSession) -> DbHealth:
    """Aggregate DB connection + size metrics.

    Runs 2 queries: GROUP BY state + pg_database_size. Any exception is
    caught and rendered as status='down' with error detail — the system
    page must not 500 just because the DB is unhealthy.
    """
    try:
        state_result = await db.execute(
            text(
                """
                SELECT state, count(*) AS cnt
                  FROM pg_stat_activity
                 WHERE datname = current_database()
                 GROUP BY state
                """
            ),
        )
        states = {
            row["state"]: int(row["cnt"]) for row in state_result.mappings().all()
        }

        size_result = await db.execute(
            text(
                """
                SELECT pg_database_size(current_database()) AS bytes,
                       pg_size_pretty(pg_database_size(current_database())) AS pretty
                """
            ),
        )
        size_row = size_result.mappings().first()
        size_bytes = int(size_row["bytes"]) if size_row else None
        size_pretty = size_row["pretty"] if size_row else None

        total = sum(states.values())
        return DbHealth(
            status="up",
            connections_active=states.get("active", 0),
            connections_idle=states.get("idle", 0),
            connections_total=total,
            db_size_pretty=size_pretty,
            db_size_bytes=size_bytes,
            error=None,
        )
    except Exception as e:  # surface as degraded, never 500  # noqa: BLE001
        return DbHealth(
            status="down",
            connections_active=None,
            connections_idle=None,
            connections_total=None,
            db_size_pretty=None,
            db_size_bytes=None,
            error=str(e)[:200],
        )


async def get_scheduler_last_activity(db: AsyncSession) -> ServiceHealth:
    """Derive scheduler health from MAX(etl_runs.started_at).

    Heartbeat file /tmp/scheduler_heartbeat is unreachable from the API
    container (separate Docker /tmp). Using etl_runs as a proxy:
      - no row ever          -> 'unknown' / 'aucun run ETL enregistre'
      - started_at within 2d -> 'up'     / 'dernier run: <iso>'
      - started_at older     -> 'stale'  / 'dernier run: <iso> (> 2j)'

    2-day threshold covers the longest cron interval (many pipelines run
    daily; some weekly). Tighter thresholds would false-positive.
    """
    from datetime import timedelta  # already imported at top; lazy re-import is fine

    try:
        result = await db.execute(
            text("SELECT MAX(started_at) AS last_ts FROM etl_runs"),
        )
        row = result.mappings().first()
        last_ts: datetime | None = row["last_ts"] if row else None
    except Exception as e:  # noqa: BLE001
        return ServiceHealth(
            name="scheduler",
            status="unknown",
            detail=f"Erreur requete etl_runs: {str(e)[:120]}",
        )

    if last_ts is None:
        return ServiceHealth(
            name="scheduler",
            status="unknown",
            detail="Aucun run ETL enregistre",
        )

    now = datetime.now(UTC)
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=UTC)
    if now - last_ts < timedelta(days=2):
        return ServiceHealth(
            name="scheduler",
            status="up",
            detail=f"dernier run: {last_ts.isoformat()}",
        )
    return ServiceHealth(
        name="scheduler",
        status="stale",
        detail=f"dernier run: {last_ts.isoformat()} (> 2j)",
    )


def get_rate_limit_buckets() -> tuple[list[RateLimitBucket], int]:
    """Enumerate active rate limit buckets.

    Imports _buckets + _lock + _WINDOW_SECONDS lazily to avoid pulling
    rate_limit module state at admin/queries.py import time. _buckets
    is a defaultdict(list) shared across threads; we MUST acquire _lock
    before iterating (otherwise data race — see rate_limit._check_rate
    pattern).

    Returns (active_buckets, total_buckets) where active = at least one
    timestamp within the last _WINDOW_SECONDS.
    """
    from app.rate_limit import _WINDOW_SECONDS, _buckets, _lock  # lazy

    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    active: list[RateLimitBucket] = []

    with _lock:
        total_buckets = len(_buckets)
        for bucket_key, timestamps in _buckets.items():
            recent = [ts for ts in timestamps if ts >= cutoff]
            if recent:
                active.append(RateLimitBucket(bucket=bucket_key, count=len(recent)))

    active.sort(key=lambda b: b.count, reverse=True)
    return active, total_buckets


def get_disk_usage() -> list[DiskUsageRow]:
    """shutil.disk_usage for /etl_tmp and /.

    /etl_tmp may not exist in dev (the ETL volume is prod-only) — catch
    FileNotFoundError and emit an 'unavailable' row instead of 500.
    """
    rows: list[DiskUsageRow] = []
    for mount in ("/etl_tmp", "/"):
        try:
            usage = shutil.disk_usage(mount)
            percent = (usage.used / usage.total * 100.0) if usage.total else None
            rows.append(
                DiskUsageRow(
                    mount=mount,
                    total_bytes=usage.total,
                    used_bytes=usage.used,
                    free_bytes=usage.free,
                    percent_used=round(percent, 1) if percent is not None else None,
                    status="ok",
                    detail=None,
                )
            )
        except (FileNotFoundError, PermissionError) as e:
            rows.append(
                DiskUsageRow(
                    mount=mount,
                    total_bytes=None,
                    used_bytes=None,
                    free_bytes=None,
                    percent_used=None,
                    status="unavailable",
                    detail=str(e)[:120],
                )
            )
    return rows


async def get_system_status(db: AsyncSession) -> SystemStatus:
    """Composite snapshot used by GET /v1/admin/system.

    Order:
      1. db health (also determines the 'db' service row)
      2. rate limit enumeration (sync, in-process)
      3. disk usage (sync, stdlib)
      4. services list (derived — api always up, db from db_health,
         scheduler from get_scheduler_last_activity)
      5. generated_at timestamp
    """
    from app.rate_limit import _WINDOW_SECONDS as _RL_WINDOW  # lazy

    db_health = await get_db_health(db)
    rate_buckets, total_buckets = get_rate_limit_buckets()
    disk_rows = get_disk_usage()
    scheduler_row = await get_scheduler_last_activity(db)

    services: list[ServiceHealth] = [
        ServiceHealth(name="api", status="up", detail="in-process"),
        ServiceHealth(
            name="db",
            status=db_health.status,
            detail=db_health.error if db_health.status == "down" else None,
        ),
        scheduler_row,
    ]

    return SystemStatus(
        db=db_health,
        rate_limit={
            "active_buckets": [b.model_dump() for b in rate_buckets],
            "total_buckets": total_buckets,
            "window_seconds": _RL_WINDOW,
        },
        disk=disk_rows,
        services=services,
        generated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Phase 19 — Admin Ops (ETL history)
# ---------------------------------------------------------------------------


async def list_etl_pipeline_summary(
    db: AsyncSession,
) -> tuple[list[EtlPipelineSummary], int]:
    """One row per known pipeline, with last-run aggregates.

    Uses PIPELINE_MAP (canonical 21-pipeline registry from scheduler.jobs)
    as the LHS of a LEFT JOIN against etl_runs so pipelines that have
    never run still appear (with NULL aggregates).

    Query shape:
      - VALUES list of (pipeline_name) seeded from PIPELINE_MAP keys
      - LATERAL subquery for last run (ORDER BY started_at DESC LIMIT 1)
      - subquery for last success (status='success', ORDER BY completed_at DESC LIMIT 1)

    Index `idx_etl_runs_dataset_started` (dataset_name, started_at DESC)
    covers both subqueries — no sequential scan.
    """
    pipeline_names = sorted(PIPELINE_MAP.keys())
    if not pipeline_names:
        return [], 0

    # Build a VALUES clause dynamically. Safe because keys are code-defined.
    values_sql = ", ".join(f"(:p{i})" for i in range(len(pipeline_names)))
    etl_params: dict[str, Any] = {
        f"p{i}": name for i, name in enumerate(pipeline_names)
    }

    sql = text(f"""
        WITH pipelines(name) AS (
            VALUES {values_sql}
        )
        SELECT
            p.name AS pipeline,
            last_success.last_success_at,
            last_run.status           AS last_run_status,
            last_run.started_at       AS last_run_started_at,
            last_run.total_duration_seconds AS last_run_duration_seconds,
            last_run.total_rows_loaded AS last_run_rows_loaded,
            last_run.error_message    AS last_run_error_message
        FROM pipelines p
        LEFT JOIN LATERAL (
            SELECT status, started_at, total_duration_seconds,
                   total_rows_loaded, error_message
            FROM etl_runs
            WHERE dataset_name = p.name
            ORDER BY started_at DESC
            LIMIT 1
        ) last_run ON TRUE
        LEFT JOIN LATERAL (
            SELECT completed_at AS last_success_at
            FROM etl_runs
            WHERE dataset_name = p.name AND status = 'success'
            ORDER BY completed_at DESC
            LIMIT 1
        ) last_success ON TRUE
        ORDER BY p.name
    """)

    result = await db.execute(sql, etl_params)
    rows = [EtlPipelineSummary(**dict(r)) for r in result.mappings().all()]
    return rows, len(rows)


async def list_etl_runs_for_pipeline(
    db: AsyncSession,
    pipeline_name: str,
    page: int,
    page_size: int,
) -> tuple[list[EtlRunRow], int]:
    """Paginated run history for one pipeline.

    Caller MUST verify `pipeline_name in PIPELINE_MAP` before calling —
    this helper does NOT validate existence (returns empty list + total=0
    for unknown names, which the router distinguishes from a valid empty
    history by checking PIPELINE_MAP directly).
    """
    offset = (page - 1) * page_size
    run_params: dict[str, Any] = {
        "name": pipeline_name,
        "offset": offset,
        "limit": page_size,
    }

    data_sql = text("""
        SELECT id, started_at, completed_at, status,
               total_rows_loaded, total_duration_seconds,
               error_message, stages
          FROM etl_runs
         WHERE dataset_name = :name
         ORDER BY started_at DESC
         OFFSET :offset LIMIT :limit
    """)
    count_sql = text("""
        SELECT COUNT(*) FROM etl_runs WHERE dataset_name = :name
    """)

    data_result = await db.execute(data_sql, run_params)
    rows = [EtlRunRow(**dict(r)) for r in data_result.mappings().all()]

    count_result = await db.execute(count_sql, {"name": pipeline_name})
    total = int(count_result.scalar_one() or 0)
    return rows, total
