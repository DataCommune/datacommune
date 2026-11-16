"""One-time script: create a default API key for every verified user who lacks one.

Run after migration 055_api_keys_is_default:
    cd backend && python -m scripts.backfill_default_keys

Idempotent — safe to run multiple times.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.auth.service import generate_api_key
from app.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


async def backfill() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Find verified users without a default key
        result = await db.execute(
            text("""
                SELECT u.id
                FROM users u
                WHERE u.is_verified = TRUE
                  AND NOT EXISTS (
                      SELECT 1 FROM api_keys k
                      WHERE k.user_id = u.id AND k.is_default = TRUE AND NOT k.revoked
                  )
                ORDER BY u.id
            """)
        )
        user_ids = [row[0] for row in result.fetchall()]

        if not user_ids:
            log.info("No users need backfill — all verified users have default keys.")
            return

        log.info("Backfilling %d users...", len(user_ids))

        for i, uid in enumerate(user_ids, 1):
            _plaintext, key_hash, key_prefix = generate_api_key("live")
            await db.execute(
                text("""
                    INSERT INTO api_keys
                        (key_hash, name, quota_monthly, user_id,
                         key_prefix, environment, is_default)
                    VALUES (:hash, 'Cle par defaut', 500, :uid, :prefix, 'live', TRUE)
                """),
                {"hash": key_hash, "uid": uid, "prefix": key_prefix},
            )

            # Commit in batches of 100
            if i % 100 == 0:
                await db.commit()
                log.info("  ... %d/%d done", i, len(user_ids))

        await db.commit()
        log.info("Backfill complete: %d default keys created.", len(user_ids))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
