"""Accounts backup and reload on a real PostgreSQL (plan 05-01, D-05, D-07).

``infra/public/accounts-dump.sh`` extracts, in one read-only snapshot of the
production database, the rows of its table list with the column list and fingerprint
that ``accounts-manifest.sql`` gives; ``accounts-load.sh`` stages them in
``public_reset_rows`` of an empty database of the same schema, plays
``public-reset.sql`` and recomputes the manifest. Both scripts drive psql inside a db
container; this module plays the same two SQL files of ``infra/public/`` directly on
two child databases of the disposable harness:

* "production": migrated to head, a user, an administrator, a personal key, the
  anonymous key, a subscription, a saved search, quotas, an admin audit row, request
  logs and the hosted scope;
* "restore": the same schema, empty (``accounts-load.sh --verify`` restores the
  schema only).

The manifest of the reloaded rows equals the manifest at extraction, every table is
equal row for row, and a row changed after the extraction changes the manifest.
Reloading into a database that holds internal keys leaves none (``--apply``). The
table list is read from ``accounts-dump.sh``, never copied here; it must start with
``APP_TABLES`` of ``public-db-restore.sh`` (also checked by
``infra/public/tests/test-accounts-dump-load.sh``).
"""

from __future__ import annotations

import re
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


def _script_array(script: str, name: str) -> list[str]:
    text = (PUBLIC_DIR / script).read_text(encoding="utf-8")
    found = re.search(rf"^readonly {name}=\(([^)]*)\)", text, re.MULTILINE)
    assert found, f"{name} not found in {script}"
    return found.group(1).split()


TABLES = _script_array("accounts-dump.sh", "TABLES")
APP_TABLES = _script_array("public-db-restore.sh", "APP_TABLES")


def _dsn(parent: str, database: str) -> str:
    return urlunsplit(urlsplit(parent)._replace(path="/" + database))


def _results(cur: psycopg.Cursor) -> list[tuple]:
    out: list[tuple] = []
    while True:
        if cur.description is not None:
            out += cur.fetchall()
        if not cur.nextset():
            return out


def _manifest(conn: psycopg.Connection) -> dict[str, tuple[int, str, str]]:
    """table -> (rows, fingerprint, columns), as accounts-manifest.sql prints them."""
    conn.execute(
        "SELECT set_config('accounts_manifest.tables', %s, false)", (",".join(TABLES),)
    )
    lines = [
        r[0]
        for r in _results(
            conn.execute((PUBLIC_DIR / "accounts-manifest.sql").read_text("utf-8"))
        )
    ]
    out = {}
    for line in lines:
        kind, table, rows, fingerprint, cols = line.split("\t")
        assert kind == "table"
        out[table] = (int(rows), fingerprint, cols)
    assert list(out) == TABLES
    return out


def _rows(conn: psycopg.Connection, table: str) -> list[tuple]:
    return conn.execute(
        sql.SQL("SELECT (t.*)::text FROM public.{} t ORDER BY 1").format(
            sql.Identifier(table)
        )
    ).fetchall()


def _export(conn: psycopg.Connection, table: str, cols: str) -> bytes:
    data = bytearray()
    with conn.cursor().copy(
        f"COPY (SELECT {cols} FROM public.{table}) TO STDOUT"
    ) as cp:
        for chunk in cp:
            data += chunk
    return bytes(data)


def _reload(
    conn: psycopg.Connection,
    manifest: dict[str, tuple[int, str, str]],
    exported: dict[str, bytes],
) -> list[tuple]:
    """Stage and reset as accounts-load.sh does: public_reset_rows, then
    public-reset.sql with the manifest tables and counts, no role to drop, every
    present role kept."""
    conn.execute("DROP SCHEMA IF EXISTS public_reset_rows CASCADE")
    conn.execute("CREATE SCHEMA public_reset_rows")
    for table, (_n, _h, cols) in manifest.items():
        conn.execute(
            f"CREATE TABLE public_reset_rows.{table} AS SELECT {cols} "
            f"FROM public.{table} WITH NO DATA"
        )
        with conn.cursor().copy(
            f"COPY public_reset_rows.{table} ({cols}) FROM STDIN"
        ) as cp:
            cp.write(exported[table])
    keep = [
        r[0]
        for r in conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%%'"
        ).fetchall()
    ]
    for key, value in (
        ("tables", ",".join(manifest)),
        ("counts", ",".join(f"{t}={v[0]}" for t, v in manifest.items())),
        ("drop_roles", ""),
        ("keep_roles", ",".join(keep)),
    ):
        conn.execute("SELECT set_config(%s, %s, false)", (f"public_reset.{key}", value))
    return _results(conn.execute((PUBLIC_DIR / "public-reset.sql").read_text("utf-8")))


def _seed_production(conn: psycopg.Connection) -> None:
    """Fabricated values only: no real e-mail, hash, key or address."""
    conn.execute(
        "INSERT INTO users (id, email, password_hash, name, is_verified, is_admin) "
        "VALUES (500, 'pro@example.invalid', 'fabricated-hash', 'Pro', true, false), "
        "(501, 'admin@example.invalid', 'fabricated-hash', 'Admin', true, true)"
    )
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, name, user_id, quota_monthly) VALUES "
        "(900, %s, 'frontend anonymous', NULL, 500), "
        "(901, %s, 'personal', 500, 1000)",
        ("anon".ljust(64, "a"), "pro".ljust(64, "p")),
    )
    conn.execute(
        "INSERT INTO subscriptions (id, user_id, stripe_subscription_id, "
        "stripe_price_id, plan_name, source) "
        "VALUES (300, 500, 'sub_fabricated', 'price_fabricated', 'pro', 'admin')"
    )
    conn.execute(
        "INSERT INTO saved_searches (id, user_id, name, addresses, datasets) "
        "VALUES (700, 500, 'search', '[\"44109\"]', '[\"dvf\"]')"
    )
    conn.execute(
        "INSERT INTO quota_usage (api_key_id, month, request_count) "
        "VALUES (900, '2026-11', 104), (901, '2026-11', 37)"
    )
    conn.execute(
        "INSERT INTO quota_usage_account (user_id, month, request_count) "
        "VALUES (500, '2026-11', 37)"
    )
    conn.execute(
        "INSERT INTO admin_audit_log (id, admin_user_id, action, target_type, "
        "target_id, after, reason, ip) VALUES (50, 501, 'grant_plan', 'user', '500', "
        "'{\"plan\": \"pro\"}', 'fabricated', '198.51.100.4')"
    )
    conn.execute(
        "INSERT INTO api_request_log (id, method, path, status_code, client_ip, "
        "api_key_id) SELECT 100000 + g, 'GET', '/v1/geocode?q=' || g, 200, "
        "'203.0.113.7', 900 FROM generate_series(1, 25) g"
    )
    conn.execute("INSERT INTO instance_scope_datasets (dataset) VALUES ('dvf')")
    conn.execute(
        "INSERT INTO instance_scope_departements (code_departement) VALUES ('44')"
    )


def test_accounts_dump_tables_start_with_app_tables():
    assert TABLES[: len(APP_TABLES)] == APP_TABLES
    assert TABLES[len(APP_TABLES) :] == [
        "instance_scope_datasets",
        "instance_scope_departements",
    ]


def test_accounts_reload_is_identical_to_the_extraction(isolated_pg_dsn, monkeypatch):
    prod_dsn = isolated_pg_dsn
    prod_db = urlsplit(prod_dsn).path.lstrip("/")
    restore_db = "contract_restore_" + uuid4().hex
    apply_db = "contract_apply_" + uuid4().hex
    monkeypatch.setenv("DATABASE_URL", prod_dsn)
    monkeypatch.setattr(settings, "database_url", prod_dsn)
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    command.upgrade(cfg, "head")

    with psycopg.connect(prod_dsn, autocommit=True) as admin:
        # Empty databases of the same schema, before any row is written.
        for name in (restore_db, apply_db):
            admin.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(name), sql.Identifier(prod_db)
                )
            )
    try:
        with psycopg.connect(prod_dsn, autocommit=True) as conn:
            _seed_production(conn)
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            before = _manifest(conn)
            exported = {t: _export(conn, t, before[t][2]) for t in TABLES}
            expected = {t: _rows(conn, t) for t in TABLES}
            conn.execute("COMMIT")
        for table in ("users", "api_keys", "subscriptions", "admin_audit_log"):
            assert before[table][0] >= 1, table
        assert before["api_request_log"][0] == 25
        for table in TABLES:
            assert exported[table].count(b"\n") == before[table][0], table

        # --verify: empty database of the same schema.
        with psycopg.connect(_dsn(prod_dsn, restore_db), autocommit=True) as conn:
            out = _reload(conn, before, exported)
            assert out[-1] == ("PUBLIC_RESET_OK api_keys=2",), out
            assert _manifest(conn) == before
            for table in TABLES:
                assert _rows(conn, table) == expected[table], table
            # A row changed after the extraction changes the manifest of its table only.
            conn.execute("UPDATE users SET name = 'changed' WHERE id = 500")
            drifted = _manifest(conn)
            assert drifted["users"] != before["users"]
            assert {t: v for t, v in drifted.items() if t != "users"} == {
                t: v for t, v in before.items() if t != "users"
            }

        # --apply: a served database holding internal keys keeps none of them.
        with psycopg.connect(_dsn(prod_dsn, apply_db), autocommit=True) as conn:
            conn.execute(
                "INSERT INTO api_keys (key_hash, name) "
                "SELECT 'work-' || lpad(g::text, 2, '0') || repeat('w', 57), "
                "'internal ' || g FROM generate_series(1, 5) g"
            )
            out = _reload(conn, before, exported)
            assert out[-1] == ("PUBLIC_RESET_OK api_keys=2",), out
            assert (
                conn.execute(
                    "SELECT count(*) FROM api_keys WHERE key_hash LIKE 'work-%%'"
                ).fetchone()[0]
                == 0
            )
            assert _manifest(conn) == before
    finally:
        with psycopg.connect(prod_dsn, autocommit=True) as admin:
            for name in (restore_db, apply_db):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )
