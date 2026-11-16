"""One-time script: generate and insert a new dc_anon_* anonymous API key.

Phase 21-03 two-step rollout (Pitfall 6 from RESEARCH):

  STEP 1 — Run this script to create the new dc_anon_* key:
      cd backend
      rtk python -m scripts.rotate_anon_key
      # Copy the printed dc_anon_xxx... key — do NOT log it in chat.

  STEP 2 — Set the new key as GitHub Actions secret PUBLIC_DC_API_KEY
      and deploy the frontend. Verify bundle contains dc_anon_*, not dc_live_*.

  STEP 3 — ONLY AFTER successful deployment:
      Revoke the old dc_live_* anonymous key via /admin/api-keys UI.

WARNING: This script does NOT revoke the old key. That must be done manually
after verifying the new key is live in production (see STEP 3 above).
"""

import asyncio
import logging
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Quota for the anonymous bundle key (low cap — non-authenticated public traffic).
# Align with the existing dc_live_* quota. Override via ANON_KEY_QUOTA env var.
_ANON_KEY_QUOTA = int(os.environ.get("ANON_KEY_QUOTA", "500"))

# Anonymous user convention: user_id=None (CLI key, no owner).
# Consistent with insert_api_key() path used for non-user keys.
_ANON_KEY_NAME = "anonymous-bundle-key"


async def _insert_anon_key(db: AsyncSession) -> str:
    """Insert a new dc_anon_* key and return its plaintext. Caller must commit."""
    # Import here so script does not need app config at module level.
    from app.auth.service import generate_api_key, hash_api_key  # noqa: F401

    plaintext, key_hash, key_prefix = generate_api_key("anon")

    result = await db.execute(
        text("""
            INSERT INTO api_keys
                (key_hash, name, quota_monthly, key_prefix, status)
            VALUES (:hash, :name, :quota, :prefix, 'active')
            RETURNING id
        """),
        {
            "hash": key_hash,
            "name": _ANON_KEY_NAME,
            "quota": _ANON_KEY_QUOTA,
            "prefix": key_prefix,
        },
    )
    key_id = result.scalar_one()
    log.info("Inserted dc_anon_* key with id=%d, prefix=%s", key_id, key_prefix)
    return plaintext


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        # Try loading from app config (requires PYTHONPATH=backend)
        try:
            from app.config import settings

            database_url = settings.database_url
        except ImportError:
            pass

    if not database_url:
        log.error("DATABASE_URL environment variable not set.")
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        plaintext = await _insert_anon_key(db)
        await db.commit()

    await engine.dispose()

    print()
    print("=" * 60)
    print("NEW dc_anon_* KEY GENERATED:")
    print(plaintext)
    print("=" * 60)
    print()
    print("NEXT STEPS (two-step rollout — do NOT skip):")
    print("1. Set PUBLIC_DC_API_KEY GitHub Actions secret to the key above.")
    print("2. Deploy frontend (wait for Cloudflare Workers CD to complete).")
    print("3. Verify bundle: curl -s https://datacommune.fr/_app/immutable/*.js |")
    print("   grep -oE 'dc_(anon|live)_[a-zA-Z0-9]+' | sort -u")
    print("   => Must show dc_anon_*, NEVER dc_live_*")
    print("4. Revoke the old dc_live_* key via /admin/api-keys ONLY AFTER step 3.")
    print()


if __name__ == "__main__":
    print()
    print(
        "WARNING: This script inserts a new dc_anon_* API key into the production DB."
    )
    print("It does NOT revoke the old dc_live_* key (see step 4 above).")
    print()
    confirm = input("Type 'ROTATE' to proceed: ").strip()
    if confirm != "ROTATE":
        print("Aborted.")
        sys.exit(0)
    asyncio.run(main())
