"""Provision the protected internal service account without exposing credentials.

Credentials are read from environment variables supplied by the one-shot
production workflow. The operation is atomic, idempotent, and fail-closed when
an existing account, subscription, or key does not match the expected markers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.auth.service import hash_api_key, hash_password, verify_password
from app.billing.plans import INTERNAL_PLAN_NAME, UNLIMITED_QUOTA
from app.config import settings

_LOCK_NAME = "datacommune:provision-internal-account"
_ACCOUNT_NAME = "Codex Service Account"
_ACCOUNT_COMPANY = "DataCommune"
_KEY_NAME = "Codex internal service key"


class ProvisioningError(RuntimeError):
    """Raised when provisioning would adopt or overwrite unexpected state."""


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str
    api_key: str


def load_credentials(environ: Mapping[str, str]) -> Credentials:
    """Load and validate credentials without logging their values."""
    email = environ.get("DC_INTERNAL_ACCOUNT_EMAIL", "").strip().lower()
    password = environ.get("DC_INTERNAL_ACCOUNT_PASSWORD", "")
    api_key = environ.get("DC_INTERNAL_ACCOUNT_API_KEY", "")

    if not email.startswith("codex-") or not email.endswith("@datacommune.fr"):
        raise ProvisioningError("Internal email must be codex-*@datacommune.fr")
    if len(password) < 32:
        raise ProvisioningError("Internal password must contain at least 32 characters")
    if not api_key.startswith("dc_live_") or len(api_key) < 48:
        raise ProvisioningError("Internal API key has an invalid format")
    return Credentials(email=email, password=password, api_key=api_key)


async def _get_or_create_user(db: AsyncSession, creds: Credentials) -> tuple[int, bool]:
    result = await db.execute(
        text("""
            SELECT id, name, company
            FROM users
            WHERE lower(email) = lower(:email)
            FOR UPDATE
        """),
        {"email": creds.email},
    )
    existing = result.mappings().first()
    password_hash = hash_password(creds.password)

    if existing is None:
        inserted = await db.execute(
            text("""
                INSERT INTO users (
                    email, password_hash, name, company, is_verified, is_admin
                )
                VALUES (:email, :password_hash, :name, :company, TRUE, TRUE)
                RETURNING id
            """),
            {
                "email": creds.email,
                "password_hash": password_hash,
                "name": _ACCOUNT_NAME,
                "company": _ACCOUNT_COMPANY,
            },
        )
        return int(inserted.scalar_one()), True

    if existing["name"] != _ACCOUNT_NAME or existing["company"] != _ACCOUNT_COMPANY:
        raise ProvisioningError("Refusing to adopt an existing unmarked user")

    user_id = int(existing["id"])
    marker = f"manual_grant_{user_id}"
    marker_result = await db.execute(
        text("""
            SELECT COUNT(*)
            FROM subscriptions
            WHERE user_id = :user_id
              AND stripe_subscription_id = :marker
              AND plan_name = :plan_name
              AND source = 'manual_grant'
        """),
        {
            "user_id": user_id,
            "marker": marker,
            "plan_name": INTERNAL_PLAN_NAME,
        },
    )
    if int(marker_result.scalar_one()) != 1:
        raise ProvisioningError("Refusing to adopt an existing user without marker")

    await db.execute(
        text("""
            UPDATE users
            SET password_hash = :password_hash,
                is_verified = TRUE,
                is_admin = TRUE,
                updated_at = NOW()
            WHERE id = :user_id
        """),
        {"password_hash": password_hash, "user_id": user_id},
    )
    return user_id, False


async def _upsert_internal_subscription(db: AsyncSession, user_id: int) -> None:
    marker = f"manual_grant_{user_id}"
    collision = await db.execute(
        text("""
            SELECT user_id
            FROM subscriptions
            WHERE stripe_subscription_id = :marker
            FOR UPDATE
        """),
        {"marker": marker},
    )
    owner_id = collision.scalar_one_or_none()
    if owner_id is not None and int(owner_id) != user_id:
        raise ProvisioningError("Internal subscription marker belongs to another user")

    unexpected = await db.execute(
        text("""
            SELECT COUNT(*)
            FROM subscriptions
            WHERE user_id = :user_id
              AND status = 'active'
              AND stripe_subscription_id <> :marker
        """),
        {"user_id": user_id, "marker": marker},
    )
    if int(unexpected.scalar_one()) != 0:
        raise ProvisioningError("Unexpected active subscription on internal account")

    await db.execute(
        text("""
            INSERT INTO subscriptions (
                user_id, stripe_subscription_id, stripe_price_id, plan_name,
                status, cancel_at_period_end, source, updated_at
            )
            VALUES (
                :user_id, :marker, '', :plan_name,
                'active', FALSE, 'manual_grant', NOW()
            )
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                plan_name = EXCLUDED.plan_name,
                status = 'active',
                current_period_start = NULL,
                current_period_end = NULL,
                cancel_at_period_end = FALSE,
                source = 'manual_grant',
                updated_at = NOW()
        """),
        {
            "user_id": user_id,
            "marker": marker,
            "plan_name": INTERNAL_PLAN_NAME,
        },
    )


async def _upsert_internal_key(
    db: AsyncSession, creds: Credentials, user_id: int
) -> int:
    result = await db.execute(
        text("""
            SELECT id, name
            FROM api_keys
            WHERE user_id = :user_id
            ORDER BY id
            FOR UPDATE
        """),
        {"user_id": user_id},
    )
    keys = list(result.mappings().all())
    if len(keys) > 1 or (keys and keys[0]["name"] != _KEY_NAME):
        raise ProvisioningError("Unexpected API keys on internal account")

    key_hash = hash_api_key(creds.api_key)
    key_prefix = f"{creds.api_key[:16]}..."
    if not keys:
        inserted = await db.execute(
            text("""
                INSERT INTO api_keys (
                    key_hash, name, quota_monthly, user_id, key_prefix,
                    environment, is_default, status, revoked
                )
                VALUES (
                    :key_hash, :name, :quota, :user_id, :key_prefix,
                    'live', TRUE, 'active', FALSE
                )
                RETURNING id
            """),
            {
                "key_hash": key_hash,
                "name": _KEY_NAME,
                "quota": UNLIMITED_QUOTA,
                "user_id": user_id,
                "key_prefix": key_prefix,
            },
        )
        return int(inserted.scalar_one())

    key_id = int(keys[0]["id"])
    await db.execute(
        text("""
            UPDATE api_keys
            SET key_hash = :key_hash,
                quota_monthly = :quota,
                key_prefix = :key_prefix,
                environment = 'live',
                is_default = TRUE,
                status = 'active',
                revoked = FALSE
            WHERE id = :key_id
        """),
        {
            "key_hash": key_hash,
            "quota": UNLIMITED_QUOTA,
            "key_prefix": key_prefix,
            "key_id": key_id,
        },
    )
    return key_id


async def _write_audit(db: AsyncSession, user_id: int, created: bool) -> None:
    await db.execute(
        text("""
            INSERT INTO admin_audit_log (
                admin_user_id, action, target_type, target_id,
                before, after, reason, user_agent
            )
            VALUES (
                :user_id, 'provision_internal_account', 'user', :target_id,
                NULL, CAST(:after AS JSONB), :reason, 'github-actions'
            )
        """),
        {
            "user_id": user_id,
            "target_id": str(user_id),
            "after": json.dumps(
                {
                    "created": created,
                    "is_admin": True,
                    "is_verified": True,
                    "plan": INTERNAL_PLAN_NAME,
                    "quota_monthly": UNLIMITED_QUOTA,
                }
            ),
            "reason": "Provision protected internal Codex service account",
        },
    )


async def provision(db: AsyncSession, creds: Credentials) -> dict[str, int | bool]:
    """Apply all account invariants in one serialized transaction."""
    async with db.begin():
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
            {"lock_name": _LOCK_NAME},
        )
        user_id, created = await _get_or_create_user(db, creds)
        await _upsert_internal_subscription(db, user_id)
        key_id = await _upsert_internal_key(db, creds, user_id)
        await _write_audit(db, user_id, created)
    return {"user_id": user_id, "key_id": key_id, "created": created}


async def verify(db: AsyncSession, creds: Credentials) -> dict[str, int | bool]:
    """Verify persisted invariants and exact credentials without mutating state."""
    result = await db.execute(
        text("""
            SELECT u.id, u.name, u.company, u.password_hash,
                   u.is_verified, u.is_admin,
                   s.plan_name, s.status AS subscription_status,
                   s.source AS subscription_source,
                   s.stripe_price_id,
                   k.id AS key_id, k.name AS key_name,
                   k.key_hash, k.quota_monthly, k.environment,
                   k.is_default, k.status AS key_status, k.revoked,
                   (
                       SELECT COUNT(*) FROM api_keys x WHERE x.user_id = u.id
                   ) AS key_count,
                   (
                       SELECT COUNT(*)
                       FROM subscriptions active
                       WHERE active.user_id = u.id AND active.status = 'active'
                   ) AS active_subscription_count
            FROM users u
            JOIN subscriptions s
              ON s.user_id = u.id
             AND s.stripe_subscription_id = CONCAT('manual_grant_', u.id)
            JOIN api_keys k ON k.user_id = u.id
            WHERE lower(u.email) = lower(:email)
        """),
        {"email": creds.email},
    )
    row = result.mappings().first()
    valid = bool(
        row
        and settings.internal_unlimited_key_hash
        and secrets.compare_digest(
            settings.internal_unlimited_key_hash,
            hash_api_key(creds.api_key),
        )
        and row["name"] == _ACCOUNT_NAME
        and row["company"] == _ACCOUNT_COMPANY
        and row["is_verified"]
        and row["is_admin"]
        and row["plan_name"] == INTERNAL_PLAN_NAME
        and row["subscription_status"] == "active"
        and row["subscription_source"] == "manual_grant"
        and row["stripe_price_id"] == ""
        and int(row["active_subscription_count"]) == 1
        and row["key_name"] == _KEY_NAME
        and row["key_hash"] == hash_api_key(creds.api_key)
        and row["quota_monthly"] == UNLIMITED_QUOTA
        and row["environment"] == "live"
        and row["is_default"]
        and row["key_status"] == "active"
        and not row["revoked"]
        and int(row["key_count"]) == 1
        and verify_password(creds.password, row["password_hash"])
    )
    if not valid:
        raise ProvisioningError("Internal account verification failed")
    return {"user_id": int(row["id"]), "key_id": int(row["key_id"]), "valid": True}


async def _run(verify_only: bool) -> None:
    creds = load_credentials(os.environ)
    engine = create_async_engine(settings.database_url, echo=False)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            result = (
                await verify(db, creds) if verify_only else await provision(db, creds)
            )
        print(json.dumps(result, sort_keys=True))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision the internal account")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.verify_only))
    except ProvisioningError as exc:
        raise SystemExit(f"Provisioning refused: {exc}") from None


if __name__ == "__main__":
    main()
