"""Purge the accounts whose address was never verified.

An account is purged only when nothing ties it to a real use:

* ``is_verified`` is false and it is not an administrator;
* it was created more than ``--older-than-days`` days ago (7 by default, the
  period of the /confidentialite draft), so a verification link still in flight
  is never cut;
* no subscription, no admin audit row written by it, and no monthly usage on any
  of its keys (a never-verified account cannot spend a quota; one that did is
  left for a human to look at).

Its keys, saved searches and account quota rows go with it (``ON DELETE CASCADE``).

Dry run by default: prints how many accounts would go, with their id and creation
date, never their address. ``--apply`` deletes them in one transaction, on the
same criteria evaluated again at that moment. Playing it twice deletes nothing the
second time.

    cd api/backend && python -m scripts.purge_unverified_accounts
    cd api/backend && python -m scripts.purge_unverified_accounts --apply

Never run on a server without Ianis's agreement (accounts are production data).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime

import psycopg

# One WHERE clause for the dry run and the deletion: they cannot drift apart.
_CANDIDATES = """
    FROM users u
    WHERE NOT u.is_verified
      AND NOT u.is_admin
      AND u.created_at < NOW() - make_interval(days => %(days)s)
      AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id)
      AND NOT EXISTS (SELECT 1 FROM admin_audit_log a WHERE a.admin_user_id = u.id)
      AND NOT EXISTS (
          SELECT 1 FROM quota_usage q
          JOIN api_keys k ON k.id = q.api_key_id
          WHERE k.user_id = u.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM quota_usage_account qa
          WHERE qa.user_id = u.id AND qa.request_count > 0
      )
"""


def find_candidates(
    conn: psycopg.Connection, older_than_days: int
) -> list[tuple[int, datetime]]:
    """Accounts the purge would delete: (id, created_at), oldest first."""
    return conn.execute(
        f"SELECT u.id, u.created_at {_CANDIDATES} ORDER BY u.created_at, u.id",
        {"days": older_than_days},
    ).fetchall()


def purge(conn: psycopg.Connection, older_than_days: int) -> list[int]:
    """Delete the candidate accounts in one transaction; return their ids."""
    with conn.transaction():
        rows = conn.execute(
            f"DELETE FROM users WHERE id IN (SELECT u.id {_CANDIDATES}) RETURNING id",
            {"days": older_than_days},
        ).fetchall()
    return sorted(r[0] for r in rows)


def _sync_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def main(argv: Sequence[str] | None = None, dsn: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply", action="store_true", help="delete (default: dry run, no change)"
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=7,
        help="only accounts created more than N days ago (default 7, minimum 1)",
    )
    args = parser.parse_args(argv)
    if args.older_than_days < 1:
        parser.error("--older-than-days must be at least 1")

    if dsn is None:
        from app.config import settings

        dsn = _sync_dsn(settings.database_url)

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        candidates = find_candidates(conn, args.older_than_days)
        for user_id, created_at in candidates:
            print(f"  id={user_id} created_at={created_at:%Y-%m-%d}")
        if not args.apply:
            print(
                f"dry run: {len(candidates)} unverified account(s) older than "
                f"{args.older_than_days} days would be deleted (--apply to delete)"
            )
            return 0
        conn.commit()
        deleted = purge(conn, args.older_than_days)
        print(f"deleted: {len(deleted)} unverified account(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
