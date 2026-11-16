"""Public state of a ZFS clone of the work base, on a real PostgreSQL (plan 04.2-05).

``infra/public/public-db-restore.sh --from-zfs-clone`` mounts in the public CT a
clone of the work cluster, then plays three SQL files of ``infra/public/`` in it.
This module plays the same files on two child databases of the disposable harness:

* "work": migrated to head, 58 internal api keys, request logs, quotas, a user and
  its saved search, a chat, the read-only role ``wb_reader`` granted as
  the private ``workbase-reader-role.sh`` grants it, the witness database
  ``datacommune_temoin``, and a published row whose relation OID is stale;
* "public": the same schema, one anonymous key, request logs and one quota row.

The public rows are exported and staged in "work" the way the script does it (column
list, ``COPY ... TO STDOUT`` text, ``CREATE TABLE ... AS ... WITH NO DATA``, ``COPY
... FROM STDIN``), then ``public-reset-temoin.sql``, ``public-reset.sql`` and
``public-reanchor.sql`` run. After the reset: no internal key, the anonymous key,
the logs and the quotas identical to the public ones, no work role, no witness
database, sequences past the reloaded ids. A failing reset (wrong count, unknown
role) changes nothing. The table and role lists are read from the script, never
copied here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.config import settings

pytestmark = pytest.mark.destructive

REPO = Path(__file__).resolve().parents[4]
PUBLIC_DIR = REPO / "infra" / "public"
SCRIPT = PUBLIC_DIR / "public-db-restore.sh"
WORK_KEYS = 58


def _script_array(name: str) -> list[str]:
    text = SCRIPT.read_text(encoding="utf-8")
    found = re.search(rf"^readonly {name}=\(([^)]*)\)", text, re.MULTILINE)
    assert found, f"{name} not found in {SCRIPT}"
    return found.group(1).split()


APP_TABLES = _script_array("APP_TABLES")
WORK_ROLES = _script_array("WORK_ROLES")


def _sql(name: str) -> str:
    return (PUBLIC_DIR / name).read_text(encoding="utf-8")


def _dsn(parent: str, database: str) -> str:
    return urlunsplit(urlsplit(parent)._replace(path="/" + database))


def _rows(conn: psycopg.Connection, table: str) -> list[tuple]:
    return conn.execute(
        sql.SQL("SELECT (t.*)::text FROM public.{} t ORDER BY 1").format(
            sql.Identifier(table)
        )
    ).fetchall()


def _export(conn: psycopg.Connection, table: str) -> tuple[str, int, bytes]:
    """Column list, row count and COPY text of one table, as the script exports them."""
    cols = conn.execute(
        "SELECT string_agg(quote_ident(attname), ',' ORDER BY attnum) FROM "
        "pg_attribute "
        "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped "
        "AND attgenerated = ''",
        (f"public.{table}",),
    ).fetchone()[0]
    count = conn.execute(
        sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table))
    ).fetchone()[0]
    data = bytearray()
    with conn.cursor().copy(f"COPY public.{table} ({cols}) TO STDOUT") as copy:
        for chunk in copy:
            data += chunk
    assert data.count(b"\n") == count
    return cols, count, bytes(data)


def _stage(
    conn: psycopg.Connection, exported: dict[str, tuple[str, int, bytes]]
) -> None:
    conn.execute("DROP SCHEMA IF EXISTS public_reset_rows CASCADE")
    conn.execute("CREATE SCHEMA public_reset_rows")
    for table, (cols, _count, data) in exported.items():
        conn.execute(
            f"CREATE TABLE public_reset_rows.{table} AS SELECT {cols} "
            f"FROM public.{table} WITH NO DATA"
        )
        with conn.cursor().copy(
            f"COPY public_reset_rows.{table} ({cols}) FROM STDIN"
        ) as copy:
            copy.write(data)


def _results(cur: psycopg.Cursor) -> list[str]:
    """First column of every row of every result set of a multi-statement execute."""
    out = []
    while True:
        if cur.description is not None:
            out += [row[0] for row in cur.fetchall()]
        if not cur.nextset():
            return out


def _statements(name: str) -> list[str]:
    """Statements of a comment-led SQL file without dollar quoting, one by one, as psql
    sends
    them (DROP DATABASE cannot run in a multi-statement string)."""
    body = "\n".join(
        line for line in _sql(name).splitlines() if not line.startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def _reset(
    conn: psycopg.Connection, counts: dict[str, int], keep: list[str]
) -> list[str]:
    conn.execute(
        "SELECT set_config('public_reset.tables', %s, false)", (",".join(APP_TABLES),)
    )
    conn.execute(
        "SELECT set_config('public_reset.counts', %s, false)",
        (",".join(f"{t}={n}" for t, n in counts.items()),),
    )
    conn.execute(
        "SELECT set_config('public_reset.drop_roles', %s, false)",
        (",".join(WORK_ROLES),),
    )
    conn.execute(
        "SELECT set_config('public_reset.keep_roles', %s, false)", (",".join(keep),)
    )
    cur = conn.cursor()
    cur.execute(_sql("public-reset.sql"))
    return _results(cur)


def _seed_work(conn: psycopg.Connection, database: str) -> None:
    uid = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES ('internal@work.invalid', "
        "'x') "
        "RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO saved_searches (user_id, name, addresses, datasets) "
        "VALUES (%s, 'work search', '[]', '[]')",
        (uid,),
    )
    for i in range(WORK_KEYS):
        key_id = conn.execute(
            "INSERT INTO api_keys (key_hash, name, user_id) VALUES (%s, %s, %s) "
            "RETURNING id",
            (f"work-{i:02d}".ljust(64, "w"), f"internal {i}", uid if i == 0 else None),
        ).fetchone()[0]
        if i < 55:
            conn.execute(
                "INSERT INTO quota_usage (api_key_id, month, request_count) "
                "VALUES (%s, '2026-10', %s)",
                (key_id, i),
            )
    conn.execute(
        "INSERT INTO api_request_log (method, path, status_code, api_key_id) "
        "SELECT 'GET', '/v1/work/' || g, 200, 1 FROM generate_series(1, 40) g"
    )
    session = conn.execute(
        "INSERT INTO chat_sessions (user_id) VALUES (%s) RETURNING id", (uid,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (%s, 'user', "
        "'work')",
        (session,),
    )
    # A published row whose relation OID is stale, as after a pg_restore.
    conn.execute(
        "INSERT INTO etl_publications (generation, dataset_name, table_name, status, "
        "relation_oids) VALUES (gen_random_uuid(), 'reset_probe', 'api_keys', "
        "'published', "
        "jsonb_build_object('public.api_keys', 1))"
    )
    # wb_reader as the private workbase-reader-role.sh grants it.
    conn.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wb_reader') "
        "THEN CREATE ROLE wb_reader LOGIN; END IF; END $$"
    )
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO wb_reader").format(
            sql.Identifier(database)
        )
    )
    conn.execute("GRANT USAGE ON SCHEMA public TO wb_reader")
    conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO wb_reader")
    conn.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO wb_reader")
    conn.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE current_user IN SCHEMA public "
        "GRANT SELECT ON TABLES TO wb_reader"
    )


def _seed_public(conn: psycopg.Connection) -> None:
    # Ids far above the work sequences: the reset must move the sequences past them.
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, name, quota_monthly) "
        "VALUES (900, %s, 'frontend anonymous', 500)",
        ("anon".ljust(64, "a"),),
    )
    conn.execute(
        "INSERT INTO api_request_log (id, method, path, status_code, client_ip, "
        "api_key_id) "
        "SELECT 100000 + g, 'GET', '/v1/geocode?q=' || g, 200, '203.0.113.7', 900 "
        "FROM generate_series(1, 25) g"
    )
    conn.execute(
        "INSERT INTO quota_usage (api_key_id, month, request_count) VALUES (900, "
        "'2026-10', 104)"
    )


@contextmanager
def _work_and_public(work_dsn: str, monkeypatch) -> Iterator[tuple[str, list[str]]]:
    """Migrated work base, a public copy of its schema and the witness database;
    yields the public database name and the roles to keep, then drops them."""
    work_db = urlsplit(work_dsn).path.lstrip("/")
    public_db = "contract_public_" + uuid4().hex
    # migrations/env.py takes its URL from settings.database_url.
    monkeypatch.setenv("DATABASE_URL", work_dsn)
    monkeypatch.setattr(settings, "database_url", work_dsn)
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    command.upgrade(cfg, "head")

    with psycopg.connect(work_dsn, autocommit=True) as admin:
        # isolated_pg_dsn is module-scoped: start each test from empty app tables.
        admin.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in APP_TABLES)
            )
        )
        # The public base has the same schema: a copy of the migrated work base.
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(public_db), sql.Identifier(work_db)
            )
        )
        admin.execute("DROP DATABASE IF EXISTS datacommune_temoin WITH (FORCE)")
        admin.execute("CREATE DATABASE datacommune_temoin")
        keep = [
            r[0]
            for r in admin.execute(
                "SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%%' "
                "AND rolname <> ALL(%s)",
                (WORK_ROLES,),
            ).fetchall()
        ]
    try:
        yield public_db, keep
    finally:
        with psycopg.connect(work_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(public_db)
                )
            )
            admin.execute("DROP DATABASE IF EXISTS datacommune_temoin WITH (FORCE)")
            if admin.execute(
                "SELECT FROM pg_roles WHERE rolname = 'wb_reader'"
            ).fetchone():
                admin.execute("REASSIGN OWNED BY wb_reader TO current_user")
                admin.execute("DROP OWNED BY wb_reader")
                admin.execute("DROP ROLE wb_reader")


def test_public_reset_keeps_public_rows_and_removes_work_state(
    isolated_pg_dsn, monkeypatch
):
    work_dsn = isolated_pg_dsn
    work_db = urlsplit(work_dsn).path.lstrip("/")
    with _work_and_public(work_dsn, monkeypatch) as (public_db, keep):
        with psycopg.connect(work_dsn) as conn:
            _seed_work(conn, work_db)
        with psycopg.connect(_dsn(work_dsn, public_db)) as conn:
            _seed_public(conn)
            expected = {t: _rows(conn, t) for t in APP_TABLES}
            exported = {t: _export(conn, t) for t in APP_TABLES}
        counts = {t: exported[t][1] for t in APP_TABLES}
        assert counts["api_keys"] == 1 and counts["api_request_log"] == 25
        assert counts["quota_usage"] == 1

        with psycopg.connect(work_dsn, autocommit=True) as conn:
            _stage(conn, exported)

            # A failing reset changes nothing: wrong count, then a role nobody listed.
            for wrong, stray, message in (
                ({**counts, "api_keys": 2}, False, "staged rows of api_keys"),
                (counts, True, "roles left from the work cluster: stray_reset_role"),
            ):
                if stray:
                    conn.execute("CREATE ROLE stray_reset_role")
                try:
                    with pytest.raises(psycopg.errors.RaiseException, match=message):
                        _reset(conn, wrong, keep)
                    conn.execute("ROLLBACK")
                finally:
                    if stray:
                        conn.execute("DROP ROLE stray_reset_role")
                assert (
                    conn.execute("SELECT count(*) FROM api_keys").fetchone()[0]
                    == WORK_KEYS
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM pg_roles WHERE rolname = 'wb_reader'"
                    ).fetchone()[0]
                    == 1
                )
                assert conn.execute(
                    "SELECT to_regnamespace('public_reset_rows') IS NOT NULL"
                ).fetchone()[0]

            temoin = [
                _results(conn.execute(s))
                for s in _statements("public-reset-temoin.sql")
            ]
            assert temoin == [[], ["TEMOIN_DROPPED"]], temoin
            out = _reset(conn, counts, keep)
            assert out[-1] == "PUBLIC_RESET_OK api_keys=1", out

            for table in APP_TABLES:
                assert _rows(conn, table) == expected[table], table
            assert (
                conn.execute(
                    "SELECT count(*) FROM api_keys WHERE key_hash LIKE 'work-%%'"
                ).fetchone()[0]
                == 0
            )
            assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0
            assert (
                conn.execute("SELECT count(*) FROM saved_searches").fetchone()[0] == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)",
                    (WORK_ROLES,),
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM pg_database WHERE datname = "
                    "'datacommune_temoin'"
                ).fetchone()[0]
                == 0
            )
            assert conn.execute(
                "SELECT to_regnamespace('public_reset_rows') IS NULL"
            ).fetchone()[0]
            assert (
                conn.execute("SELECT count(*) FROM pg_default_acl").fetchone()[0] == 0
            )
            # Sequences past the reloaded ids: the next rows do not collide.
            log_id = conn.execute(
                "INSERT INTO api_request_log (method, path, status_code) "
                "VALUES ('GET', '/v1/health', 200) RETURNING id"
            ).fetchone()[0]
            assert log_id > 100025
            key_id = conn.execute(
                "INSERT INTO api_keys (key_hash, name) VALUES (%s, 'next') RETURNING "
                "id",
                ("next".ljust(64, "n"),),
            ).fetchone()[0]
            assert key_id > 900

            reanchor = _results(conn.execute(_sql("public-reanchor.sql")))
            published = conn.execute(
                "SELECT count(*) FROM (SELECT DISTINCT ON (dataset_name) status "
                "FROM etl_publications ORDER BY dataset_name, id DESC) l "
                "WHERE status = 'published'"
            ).fetchone()[0]
            assert reanchor == [
                "REANCHORED 1 reset_probe",
                f"PUBLISHED {published} NOT_PUBLISHED 0 STALE 0",
            ], reanchor
            assert conn.execute(
                "SELECT (relation_oids ->> 'public.api_keys')::bigint "
                "= 'public.api_keys'::regclass::oid::bigint "
                "FROM etl_publications WHERE dataset_name = 'reset_probe'"
            ).fetchone()[0]


def _seed_public_accounts(conn: psycopg.Connection) -> None:
    """Real account rows on the public side (fabricated values, no real e-mail or
    hash), with ids far above the work sequences."""
    _seed_public(conn)
    conn.execute(
        "INSERT INTO users (id, email, password_hash, name, is_verified, is_admin) "
        "VALUES (500, 'pro@example.invalid', 'fabricated-hash', 'Pro', true, false), "
        "(501, 'admin@example.invalid', 'fabricated-hash', 'Admin', true, true)"
    )
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, name, user_id, key_prefix) "
        "VALUES (901, %s, 'personal', 500, 'dc_live_pro')",
        ("pro".ljust(64, "p"),),
    )
    conn.execute(
        "INSERT INTO subscriptions (id, user_id, stripe_subscription_id, "
        "stripe_price_id, plan_name, source) "
        "VALUES (300, 500, 'sub_fabricated', 'price_fabricated', 'pro', 'admin')"
    )
    conn.execute(
        "INSERT INTO saved_searches (id, user_id, name, addresses, datasets) "
        "VALUES (700, 500, 'public search', '[\"44109\"]', '[\"dvf\"]')"
    )
    conn.execute(
        "INSERT INTO quota_usage (api_key_id, month, request_count) VALUES (901, "
        "'2026-11', 37)"
    )
    conn.execute(
        "INSERT INTO quota_usage_account (user_id, month, request_count) "
        "VALUES (500, '2026-11', 37)"
    )
    conn.execute(
        "INSERT INTO admin_audit_log (id, admin_user_id, action, target_type, "
        "target_id, after, reason, ip) VALUES (50, 501, 'grant_plan', 'user', '500', "
        "'{\"plan\": \"pro\"}', 'prospect 44', '198.51.100.4')"
    )


def test_public_reset_keeps_real_accounts(isolated_pg_dsn, monkeypatch):
    """A republication keeps real public accounts row for row (OPS-01, plan 05-02)."""
    work_dsn = isolated_pg_dsn
    work_db = urlsplit(work_dsn).path.lstrip("/")
    with _work_and_public(work_dsn, monkeypatch) as (public_db, keep):
        with psycopg.connect(work_dsn) as conn:
            _seed_work(conn, work_db)
        with psycopg.connect(_dsn(work_dsn, public_db)) as conn:
            _seed_public_accounts(conn)
            expected = {t: _rows(conn, t) for t in APP_TABLES}
            exported = {t: _export(conn, t) for t in APP_TABLES}
        counts = {t: exported[t][1] for t in APP_TABLES}
        for table in (
            "users",
            "api_keys",
            "subscriptions",
            "saved_searches",
            "quota_usage",
            "quota_usage_account",
            "admin_audit_log",
        ):
            assert counts[table] >= 1, table

        with psycopg.connect(work_dsn, autocommit=True) as conn:
            _stage(conn, exported)
            for s in _statements("public-reset-temoin.sql"):
                _results(conn.execute(s))
            out = _reset(conn, counts, keep)
            assert out[-1] == "PUBLIC_RESET_OK api_keys=2", out

            for table in APP_TABLES:
                assert _rows(conn, table) == expected[table], table
            assert (
                conn.execute(
                    "SELECT count(*) FROM api_keys WHERE key_hash LIKE 'work-%%'"
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM users WHERE email = 'internal@work.invalid'"
                ).fetchone()[0]
                == 0
            )
            # Sequences past the reloaded ids: the next account rows do not collide.
            user_id = conn.execute(
                "INSERT INTO users (email, password_hash) "
                "VALUES ('next@example.invalid', 'x') RETURNING id"
            ).fetchone()[0]
            assert user_id > 501
            audit_id = conn.execute(
                "INSERT INTO admin_audit_log (admin_user_id, action, target_type, "
                "target_id, reason) VALUES (501, 'probe', 'user', '500', 'probe') "
                "RETURNING id"
            ).fetchone()[0]
            assert audit_id > 50
