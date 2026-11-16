"""Atomic admission accounting: one accepted API attempt, regardless of outcome."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def reserve_quota(
    db: AsyncSession, *, key_id: int, user_id: int | None, month: str, limit: int
) -> int | None:
    """Reserve one account/key unit; rejected attempts never increase either row.

    The account row is the serialization point shared by all its keys. Both
    counters commit together; failure of the second write rolls back the first.
    Only callers with a verified internal entitlement may supply limit=-1, plus
    the public anonymous key, whose allowance is enforced per visitor IP before
    this call and whose monthly row is a supervision counter only.
    """
    table = "quota_usage_account" if user_id is not None else "quota_usage"
    owner_column = "user_id" if user_id is not None else "api_key_id"
    try:
        result = await db.execute(
            text(f"""
                INSERT INTO {table} ({owner_column}, month, request_count)
                SELECT :owner, :month, 1 WHERE :limit > 0 OR :limit = -1
                ON CONFLICT ({owner_column}, month)
                DO UPDATE SET request_count = {table}.request_count + 1
                WHERE :limit = -1 OR {table}.request_count < :limit
                RETURNING request_count
            """),
            {
                "owner": user_id if user_id is not None else key_id,
                "month": month,
                "limit": limit,
            },
        )
        count = result.scalar_one_or_none()
        if count is None:
            await db.rollback()
            return None
        if user_id is not None:
            await db.execute(
                text("""
                    INSERT INTO quota_usage(api_key_id,month,request_count)
                    VALUES(:key_id,:month,1)
                    ON CONFLICT(api_key_id,month) DO UPDATE
                    SET request_count=quota_usage.request_count+1
                """),
                {"key_id": key_id, "month": month},
            )
        await db.commit()
        return count
    except Exception:
        await db.rollback()
        raise
