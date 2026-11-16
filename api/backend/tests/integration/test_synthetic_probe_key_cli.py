"""Real CLI transactions and secret redaction on a disposable migrated database."""

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

pytestmark = pytest.mark.destructive
BACKEND = Path(__file__).resolve().parents[2]
HMAC_SECRET = "synthetic-probe-key-test-hmac"


def digest(key):
    return hmac.new(HMAC_SECRET.encode(), key.encode(), hashlib.sha256).hexdigest()


@pytest.fixture(scope="module")
def database(isolated_pg_dsn):
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    # migrations/env.py reads Settings, overriding the Alembic URL option.
    previous_dsn = settings.database_url
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(config, "head")
    assert settings.database_url == previous_dsn
    return isolated_pg_dsn


@pytest.fixture(autouse=True)
def clear_own_keys(database):
    with psycopg.connect(database) as conn:
        conn.execute("DELETE FROM api_keys")


def cli(database, key, *args):
    environment = os.environ.copy()
    environment.update(
        {
            "DEBUG": "true",
            "DATABASE_URL": database.replace("postgresql://", "postgresql+asyncpg://"),
            "API_KEY_HMAC_SECRET": HMAC_SECRET,
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "scripts.provision_synthetic_probe_key", *args],
        cwd=BACKEND,
        env=environment,
        input=key.encode(),
        capture_output=True,
        timeout=20,
    )
    assert key.encode() not in result.stdout + result.stderr
    assert digest(key).encode() not in result.stdout + result.stderr
    assert not result.stderr
    return result.returncode, json.loads(result.stdout)


def test_cli_commits_exact_normal_key_then_verifies_and_is_idempotent(database):
    key = "dc_live_" + "n" * 43
    assert cli(database, key, "--verify-only") == (
        2,
        {"status": "refused", "code": "key_not_provisioned"},
    )
    status, created = cli(database, key)
    assert status == 0 and created["created"] is True
    # A separate connection proves persistence after the CLI process exited.
    with psycopg.connect(database) as conn:
        row = conn.execute(
            "SELECT key_hash,quota_monthly,status,revoked,user_id,environment,"
            "is_default FROM api_keys WHERE id=%s",
            (created["key_id"],),
        ).fetchone()
        assert row == (digest(key), 500, "active", False, None, "live", False)
        before = conn.execute(
            "SELECT xmin::text FROM api_keys WHERE id=%s", (created["key_id"],)
        ).fetchone()
    for options in [(), ("--verify-only",)]:
        status, checked = cli(database, key, *options)
        assert (
            status == 0
            and checked["key_id"] == created["key_id"]
            and checked["created"] is False
        )
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT xmin::text FROM api_keys WHERE id=%s", (created["key_id"],)
            ).fetchone()
            == before
        )


@pytest.mark.parametrize("case", ["revoked", "unrelated", "legacy", "sentinel"])
def test_collision_or_revocation_never_adopts_or_reactivates(database, case):
    key = "dc_live_" + "c" * 43
    stored_hash = (
        hashlib.sha256(key.encode()).hexdigest() if case == "legacy" else digest(key)
    )
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO api_keys(key_hash,name,quota_monthly,status,revoked,"
            "environment,is_default) VALUES (%s,%s,%s,%s,%s,'live',FALSE)",
            (
                stored_hash,
                "unrelated" if case == "unrelated" else "Synthetic golden probe key",
                -1 if case == "sentinel" else 500,
                "revoked" if case == "revoked" else "active",
                case == "revoked",
            ),
        )
        before = conn.execute("SELECT row_to_json(k)::text FROM api_keys k").fetchall()
    assert cli(database, key) == (
        2,
        {"status": "refused", "code": "key_collision_or_revoked"},
    )
    with psycopg.connect(database) as conn:
        assert (
            conn.execute("SELECT row_to_json(k)::text FROM api_keys k").fetchall()
            == before
        )


def test_commit_failure_rolls_back_and_never_reports_success(database):
    key = "dc_live_" + "f" * 43
    with psycopg.connect(database) as conn:
        conn.execute(
            """CREATE FUNCTION reject_probe_commit() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'deferred test failure'; END $$"""
        )
        conn.execute(
            """CREATE CONSTRAINT TRIGGER reject_probe_commit AFTER INSERT ON api_keys
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION reject_probe_commit()"""
        )
    try:
        assert cli(database, key) == (
            2,
            {"status": "unavailable", "code": "provisioning_failed"},
        )
        with psycopg.connect(database) as conn:
            assert conn.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 0
    finally:
        with psycopg.connect(database) as conn:
            conn.execute("DROP TRIGGER reject_probe_commit ON api_keys")
            conn.execute("DROP FUNCTION reject_probe_commit()")


def test_staged_rotation_requires_expected_previous_key_and_does_not_revoke_it(
    database,
):
    old, new = "dc_live_" + "a" * 43, "dc_live_" + "b" * 43
    _, previous = cli(database, old)
    assert cli(database, new) == (
        2,
        {"status": "refused", "code": "active_probe_key_requires_explicit_rotation"},
    )
    assert cli(database, new, "--previous-key-id", "2147483647") == (
        2,
        {"status": "refused", "code": "previous_key_not_qualified"},
    )
    status, created = cli(database, new, "--previous-key-id", str(previous["key_id"]))
    assert status == 0 and created["created"] is True
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status,revoked,quota_monthly FROM api_keys ORDER BY id"
        ).fetchall() == [("active", False, 500), ("active", False, 500)]
    assert cli(database, old, "--verify-only")[0] == 0
    assert cli(database, new, "--verify-only")[0] == 0


def test_recette_key_revokes_only_the_previous_recette_key(database):
    probe = "dc_live_" + "p" * 43
    first, second = "dc_anon_" + "r" * 43, "dc_anon_" + "s" * 43
    _, golden = cli(database, probe)
    assert cli(database, "dc_anon_" + "r" * 43, "--recette", "--verify-only") == (
        2,
        {"status": "refused", "code": "key_not_provisioned"},
    )
    status, one = cli(database, first, "--recette")
    assert status == 0 and one["created"] is True and one["revoked_previous"] == 0
    assert cli(database, first, "--recette")[1]["created"] is False
    status, two = cli(database, second, "--recette")
    assert status == 0 and two["revoked_previous"] == 1
    assert two["quota_monthly"] == 25000
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT id,status,revoked,quota_monthly FROM api_keys ORDER BY id"
        ).fetchall() == [
            (golden["key_id"], "active", False, 500),
            (one["key_id"], "revoked", True, 25000),
            (two["key_id"], "active", False, 25000),
        ]
    # A revoked acceptance key is never revived; the golden probe key is untouched.
    assert cli(database, first, "--recette") == (
        2,
        {"status": "refused", "code": "key_collision_or_revoked"},
    )
    assert cli(database, probe, "--verify-only")[0] == 0
    assert cli(database, second, "--recette")[1]["created"] is False
