# ruff: noqa: E501
"""Database queries for API key authentication, quota tracking, and user CRUD."""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# API key queries (existing)
# ---------------------------------------------------------------------------


async def lookup_api_key(db: AsyncSession, key_hash: str) -> dict | None:
    """Look up an active API key by its hash.

    Rejects keys with status != 'active' (pending or revoked) since migration 064.
    The legacy NOT revoked predicate is kept alongside the status check for
    one deploy cycle (RESEARCH Open Question 4 — both columns in sync).

    Returns:
        Dict with id, name, quota_monthly, revoked, created_at, status if found
        and status='active'. None otherwise.
    """
    result = await db.execute(
        text("""
            SELECT id, name, quota_monthly, revoked, created_at, user_id, status,
                   key_prefix
            FROM api_keys
            WHERE key_hash = :hash AND status = 'active' AND NOT revoked
        """),
        {"hash": key_hash},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def increment_quota(db: AsyncSession, api_key_id: int, month: str) -> int:
    """Atomically increment the request counter for a key/month pair.

    Uses INSERT ... ON CONFLICT DO UPDATE (upsert) to ensure atomicity.

    Returns:
        The new request_count after incrementing.
    """
    result = await db.execute(
        text("""
            INSERT INTO quota_usage (api_key_id, month, request_count)
            VALUES (:key_id, :month, 1)
            ON CONFLICT (api_key_id, month)
            DO UPDATE SET request_count = quota_usage.request_count + 1
            RETURNING request_count
        """),
        {"key_id": api_key_id, "month": month},
    )
    count = result.scalar_one()
    await db.commit()
    return count


async def insert_api_key(
    db: AsyncSession,
    key_hash: str,
    name: str,
    quota: int,
) -> int:
    """Insert a new API key and return its ID (CLI usage, no user_id).

    Args:
        db: Async database session.
        key_hash: SHA-256 hash of the plaintext key.
        name: Human-readable name for the key.
        quota: Monthly request quota.

    Returns:
        The auto-generated key ID.
    """
    result = await db.execute(
        text("""
            INSERT INTO api_keys (key_hash, name, quota_monthly)
            VALUES (:hash, :name, :quota)
            RETURNING id
        """),
        {"hash": key_hash, "name": name, "quota": quota},
    )
    key_id = result.scalar_one()
    await db.commit()
    return key_id


# ---------------------------------------------------------------------------
# User CRUD queries
# ---------------------------------------------------------------------------

_USER_COLS = (
    "id, email, password_hash, name, company, is_verified, is_admin,"
    " created_at, updated_at"
)


async def create_user(
    db: AsyncSession,
    email: str,
    password_hash: str,
) -> int:
    """Insert a new user and return its ID."""
    result = await db.execute(
        text("""
            INSERT INTO users (email, password_hash)
            VALUES (:email, :pw)
            RETURNING id
        """),
        {"email": email, "pw": password_hash},
    )
    user_id = result.scalar_one()
    await db.commit()
    return user_id


async def get_user_by_email(db: AsyncSession, email: str) -> dict | None:
    """Fetch a user by email (case-insensitive; uses lower(email) functional index)."""
    result = await db.execute(
        text(f"SELECT {_USER_COLS} FROM users WHERE lower(email) = lower(:email)"),
        {"email": email},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_user_by_id(db: AsyncSession, user_id: int) -> dict | None:
    """Fetch a user by ID. Returns dict or None."""
    result = await db.execute(
        text(f"SELECT {_USER_COLS} FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def verify_user_email(db: AsyncSession, email: str) -> bool:
    """Mark a user as verified (case-insensitive email match). Returns True if actually updated."""
    result = await db.execute(
        text("""
            UPDATE users
            SET is_verified = TRUE, updated_at = NOW()
            WHERE lower(email) = lower(:email) AND NOT is_verified
        """),
        {"email": email},
    )
    await db.commit()
    return result.rowcount > 0


async def update_user_password(
    db: AsyncSession,
    user_id: int,
    password_hash: str,
    expected_password_hash: str,
) -> bool:
    """Consume one credential generation atomically, including concurrent resets."""
    result = await db.execute(
        text("""
            UPDATE users
            SET password_hash = :pw, updated_at = NOW()
            WHERE id = :uid AND password_hash = :expected
        """),
        {"pw": password_hash, "uid": user_id, "expected": expected_password_hash},
    )
    await db.commit()
    return result.rowcount == 1


async def update_user_profile(
    db: AsyncSession,
    user_id: int,
    name: str | None,
    company: str | None,
) -> None:
    """Update a user's name and/or company."""
    await db.execute(
        text("""
            UPDATE users
            SET name = COALESCE(:name, name),
                company = COALESCE(:company, company),
                updated_at = NOW()
            WHERE id = :uid
        """),
        {"name": name, "company": company, "uid": user_id},
    )
    await db.commit()


async def delete_user(db: AsyncSession, user_id: int) -> None:
    """Delete a user (CASCADE deletes their api_keys)."""
    await db.execute(
        text("DELETE FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    await db.commit()


async def count_user_active_keys(
    db: AsyncSession,
    user_id: int,
) -> int:
    """Count usable non-default API keys for a user.

    Both state columns are checked while the legacy ``revoked`` boolean
    coexists with the status state machine.
    """
    result = await db.execute(
        text("""
            SELECT COUNT(*)
            FROM api_keys
            WHERE user_id = :uid
              AND status = 'active'
              AND NOT revoked
              AND NOT is_default
        """),
        {"uid": user_id},
    )
    return result.scalar_one()


async def insert_user_api_key(
    db: AsyncSession,
    key_hash: str,
    name: str,
    quota: int,
    user_id: int,
    key_prefix: str,
    environment: str,
    status: str = "active",
) -> int:
    """Insert a new API key for a user and return its ID.

    Args:
        status: Initial state machine value ('pending'|'active'|'revoked').
                Defaults to 'active' for back-compat with existing callers.
                Pass 'pending' for the quick-key flow (insert before email send).
                CAST(:status AS VARCHAR) form required for asyncpg compatibility
                (STATE:129 — asyncpg cannot parse :name::type double-colon cast).
    """
    result = await db.execute(
        text("""
            INSERT INTO api_keys
                (key_hash, name, quota_monthly, user_id,
                 key_prefix, environment, status, revoked)
            VALUES (
                :hash, :name, :quota, :uid, :prefix, :env,
                CAST(:status AS VARCHAR), :revoked
            )
            RETURNING id
        """),
        {
            "hash": key_hash,
            "name": name,
            "quota": quota,
            "uid": user_id,
            "prefix": key_prefix,
            "env": environment,
            "status": status,
            "revoked": status == "revoked",
        },
    )
    key_id = result.scalar_one()
    await db.commit()
    return key_id


async def update_api_key_status(
    db: AsyncSession,
    key_id: int,
    new_status: str,
) -> None:
    """Update the status column of an API key (state machine transition).

    Used by quick-key flow: pending -> active (email sent) or
    pending -> revoked (email failed). Caller must call db.commit().
    CAST(:s AS VARCHAR) form required for asyncpg compatibility (STATE:129).
    """
    await db.execute(
        text("""
            UPDATE api_keys
            SET status = CAST(:s AS VARCHAR), revoked = :revoked
            WHERE id = :id
        """),
        {"s": new_status, "revoked": new_status == "revoked", "id": key_id},
    )


async def list_user_keys(db: AsyncSession, user_id: int) -> list[dict]:
    """List user-created API keys (excludes default/internal key)."""
    result = await db.execute(
        text("""
            SELECT k.id, k.name, k.key_prefix, k.quota_monthly, k.revoked,
                   k.environment, k.created_at, k.is_default,
                   COALESCE(q.request_count, 0) AS usage_count
            FROM api_keys k
            LEFT JOIN quota_usage q
                ON q.api_key_id = k.id
                AND q.month = TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM')
            WHERE k.user_id = :uid AND NOT k.is_default
            ORDER BY k.created_at DESC
        """),
        {"uid": user_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def revoke_user_key(db: AsyncSession, key_id: int, user_id: int) -> bool:
    """Revoke an API key (owned by user, not default). Returns True."""
    result = await db.execute(
        text("""
            UPDATE api_keys
            SET revoked = TRUE, status = 'revoked'
            WHERE id = :kid AND user_id = :uid AND NOT revoked AND NOT is_default
        """),
        {"kid": key_id, "uid": user_id},
    )
    await db.commit()
    return result.rowcount > 0


async def get_default_key_for_user(db: AsyncSession, user_id: int) -> dict | None:
    """Look up the user's active default API key."""
    result = await db.execute(
        text("""
            SELECT id, name, quota_monthly, revoked, created_at, user_id
            FROM api_keys
            WHERE user_id = :uid
              AND is_default = TRUE
              AND status = 'active'
              AND NOT revoked
            LIMIT 1
        """),
        {"uid": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def insert_default_api_key(
    db: AsyncSession,
    key_hash: str,
    user_id: int,
    key_prefix: str,
) -> int:
    """Insert the default API key for a user (created at email verification)."""
    result = await db.execute(
        text("""
            INSERT INTO api_keys
                (key_hash, name, quota_monthly, user_id,
                 key_prefix, environment, is_default)
            VALUES (:hash, 'Cle par defaut', 500, :uid, :prefix, 'live', TRUE)
            RETURNING id
        """),
        {"hash": key_hash, "uid": user_id, "prefix": key_prefix},
    )
    key_id = result.scalar_one()
    await db.commit()
    return key_id


async def migrate_key_hash(
    db: AsyncSession,
    old_hash: str,
    new_hash: str,
) -> None:
    """Migrate a legacy SHA-256 key_hash to HMAC-SHA256 (one-time per key)."""
    await db.execute(
        text("UPDATE api_keys SET key_hash = :new WHERE key_hash = :old"),
        {"old": old_hash, "new": new_hash},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Account-level quota tracking
# ---------------------------------------------------------------------------


async def increment_quota_account(db: AsyncSession, user_id: int, month: str) -> int:
    """Atomically increment the account-level request counter.

    Returns the new request_count after incrementing.
    """
    result = await db.execute(
        text("""
            INSERT INTO quota_usage_account (user_id, month, request_count)
            VALUES (:uid, :month, 1)
            ON CONFLICT (user_id, month)
            DO UPDATE SET request_count = quota_usage_account.request_count + 1
            RETURNING request_count
        """),
        {"uid": user_id, "month": month},
    )
    count = result.scalar_one()
    await db.commit()
    return count


async def get_account_usage(db: AsyncSession, user_id: int) -> int:
    """Get current month request count for a user account."""
    current_month = datetime.now(UTC).strftime("%Y-%m")
    result = await db.execute(
        text("""
            SELECT COALESCE(request_count, 0)
            FROM quota_usage_account
            WHERE user_id = :uid AND month = :month
        """),
        {"uid": user_id, "month": current_month},
    )
    return result.scalar() or 0


async def key_name_exists(db: AsyncSession, user_id: int, name: str) -> bool:
    """Check if an active key with this name already exists for the user."""
    result = await db.execute(
        text("""
            SELECT 1 FROM api_keys
            WHERE user_id = :uid AND LOWER(name) = LOWER(:name)
                AND NOT revoked AND NOT is_default
            LIMIT 1
        """),
        {"uid": user_id, "name": name},
    )
    return result.first() is not None
