"""Purge of never-verified accounts on a real PostgreSQL migrated to head.

Only an old, unverified, unused account goes; its keys go with it. A recent one,
a verified one, an administrator, an account with a subscription, one that wrote
audit rows or spent a quota stay. The dry run changes nothing, a second run
deletes nothing, and no address is ever printed.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts import purge_unverified_accounts as purge_mod

pytestmark = pytest.mark.destructive

OLD = "NOW() - INTERVAL '40 days'"


@pytest.fixture(scope="module")
def accounts_db(isolated_pg_dsn):
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    # migrations/env.py reads the URL from the settings, never from the Config.
    previous = settings.database_url
    settings.database_url = isolated_pg_dsn
    try:
        command.upgrade(cfg, "head")
    finally:
        settings.database_url = previous
    return isolated_pg_dsn


def _user(conn, email, *, verified=False, admin=False, created=OLD) -> int:
    return conn.execute(
        "INSERT INTO users (email, password_hash, is_verified, is_admin, created_at) "
        f"VALUES (%s, '!fixture', %s, %s, {created}) RETURNING id",
        (email, verified, admin),
    ).fetchone()[0]


def _key(conn, user_id, suffix, *, revoked=True) -> int:
    return conn.execute(
        "INSERT INTO api_keys (key_hash, name, quota_monthly, user_id, key_prefix, "
        "environment, status, revoked) VALUES (%s, 'Quick Key', 500, %s, 'dc_live_x', "
        "'live', %s, %s) RETURNING id",
        (
            f"purge-fixture-{suffix}",
            user_id,
            "revoked" if revoked else "active",
            revoked,
        ),
    ).fetchone()[0]


@pytest.fixture()
def seeded(accounts_db):
    with psycopg.connect(accounts_db) as conn:
        conn.execute("TRUNCATE users CASCADE")
        ids = {
            "stale": _user(conn, "stale@example.test"),
            "stale_quickkey": _user(conn, "failed-mail@example.test"),
            "recent": _user(conn, "recent@example.test", created="NOW()"),
            "verified": _user(conn, "verified@example.test", verified=True),
            "admin": _user(conn, "admin@example.test", admin=True),
            "subscribed": _user(conn, "subscribed@example.test"),
            "auditor": _user(conn, "auditor@example.test"),
            "used_key": _user(conn, "used-key@example.test"),
        }
        ids["quickkey_key"] = _key(conn, ids["stale_quickkey"], "qk")
        used = _key(conn, ids["used_key"], "used", revoked=False)
        conn.execute(
            "INSERT INTO quota_usage (api_key_id, month, request_count) "
            "VALUES (%s, '2026-10', 3)",
            (used,),
        )
        conn.execute(
            "INSERT INTO subscriptions (user_id, stripe_subscription_id, "
            "stripe_price_id, plan_name, source) "
            "VALUES (%s, 'sub_purge', 'price_purge', 'pro', 'admin')",
            (ids["subscribed"],),
        )
        conn.execute(
            "INSERT INTO admin_audit_log (admin_user_id, action, target_type, "
            "target_id, reason) VALUES (%s, 'fixture', 'user', '0', 'fixture')",
            (ids["auditor"],),
        )
    return accounts_db, ids


def _user_ids(dsn) -> set[int]:
    with psycopg.connect(dsn) as conn:
        return {r[0] for r in conn.execute("SELECT id FROM users").fetchall()}


def test_dry_run_lists_and_changes_nothing(seeded, capsys):
    dsn, ids = seeded
    before = _user_ids(dsn)

    assert purge_mod.main([], dsn=dsn) == 0

    out = capsys.readouterr().out
    assert "dry run: 2 unverified account(s)" in out
    assert f"id={ids['stale']} " in out
    assert f"id={ids['stale_quickkey']} " in out
    assert "@" not in out  # never an address
    assert _user_ids(dsn) == before


def test_apply_deletes_only_stale_unused_accounts_and_is_idempotent(seeded, capsys):
    dsn, ids = seeded

    assert purge_mod.main(["--apply"], dsn=dsn) == 0
    assert "deleted: 2 unverified account(s)" in capsys.readouterr().out

    kept = {k for k in ids if k not in {"stale", "stale_quickkey", "quickkey_key"}}
    assert _user_ids(dsn) == {ids[k] for k in kept}
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM api_keys WHERE id = %s", (ids["quickkey_key"],)
            ).fetchone()[0]
            == 0
        )

    assert purge_mod.main(["--apply"], dsn=dsn) == 0
    assert "deleted: 0 unverified account(s)" in capsys.readouterr().out
    assert _user_ids(dsn) == {ids[k] for k in kept}


def test_threshold_is_respected(seeded):
    dsn, ids = seeded
    with psycopg.connect(dsn) as conn:
        assert purge_mod.find_candidates(conn, 60) == []
        found = [r[0] for r in purge_mod.find_candidates(conn, 1)]
    assert set(found) == {ids["stale"], ids["stale_quickkey"]}


def test_refuses_a_zero_day_threshold(accounts_db):
    with pytest.raises(SystemExit):
        purge_mod.main(["--older-than-days", "0"], dsn=accounts_db)
