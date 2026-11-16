"""COR-18: account/key reservation races and rollback in disposable PostgreSQL."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import dependencies, service
from app.common.exceptions import ErreurApiError, _handle_exception_api
from app.config import settings
from app.database import get_db

pytestmark = pytest.mark.destructive


def test_quota_races_rejections_errors_and_month_boundary(isolated_pg_dsn, monkeypatch):
    dsn = isolated_pg_dsn
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "database_url", dsn)
    monkeypatch.setattr(dependencies, "get_quota_for_plan", lambda _plan: 3)
    monkeypatch.setattr(dependencies, "_check_rate", lambda *_args: (True, 999, 60))
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    command.upgrade(cfg, "head")
    with psycopg.connect(dsn) as conn:
        uid = conn.execute(
            "INSERT INTO users(email,password_hash,is_verified) "
            "VALUES('quota@example.com','!test',true) RETURNING id"
        ).fetchone()[0]
        key_ids = {}
        for key in ("quota-a", "quota-b"):
            key_ids[key] = conn.execute(
                "INSERT INTO api_keys(key_hash,name,user_id,quota_monthly) "
                "VALUES(%s,%s,%s,3) RETURNING id",
                (service.hash_api_key(key), key, uid),
            ).fetchone()[0]
        orphan_id = conn.execute(
            "INSERT INTO api_keys(key_hash,name,quota_monthly) "
            "VALUES(%s,'quota-orphan',1) RETURNING id",
            (service.hash_api_key("quota-orphan"),),
        ).fetchone()[0]

    class Clock:
        month = 9

        @classmethod
        def now(cls, tz):
            return datetime(2026, cls.month, 1, tzinfo=UTC)

    monkeypatch.setattr(dependencies, "datetime", Clock)

    def counters(month):
        with psycopg.connect(dsn) as conn:
            account = conn.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM quota_usage_account "
                "WHERE user_id=%s AND month=%s",
                (uid, month),
            ).fetchone()[0]
            keys = conn.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM quota_usage WHERE month=%s",
                (month,),
            ).fetchone()[0]
            return account, keys

    async def exercise():
        engine = create_async_engine(
            dsn.replace("postgresql://", "postgresql+asyncpg://")
        )
        sessions = async_sessionmaker(engine)

        async def db():
            async with sessions() as session:
                yield session

        app = FastAPI()
        app.dependency_overrides[get_db] = db
        app.add_exception_handler(ErreurApiError, _handle_exception_api)

        @app.get("/data/{status}", dependencies=[Depends(dependencies.check_quota)])
        async def data(status: int):
            return JSONResponse({"downstream": status}, status_code=status)

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://local.test",
            ) as client:
                results = await asyncio.gather(
                    *[
                        client.get(
                            "/data/200",
                            headers={"X-API-Key": "quota-a" if i % 2 else "quota-b"},
                        )
                        for i in range(6)
                    ]
                )
                assert sorted(r.status_code for r in results) == [
                    200,
                    200,
                    200,
                    429,
                    429,
                    429,
                ]
                assert counters("2026-09") == (3, 3)
                # Compare each accepted request with its actual key row; an
                # aggregate sum alone can conceal accounting on the wrong key.
                accepted = {key: 0 for key in key_ids}
                for i, response in enumerate(results):
                    if response.status_code == 200:
                        accepted["quota-a" if i % 2 else "quota-b"] += 1
                with psycopg.connect(dsn) as conn:
                    actual = dict(
                        conn.execute(
                            "SELECT api_key_id,request_count FROM quota_usage "
                            "WHERE month='2026-09'"
                        ).fetchall()
                    )
                assert {
                    key: actual.get(kid, 0) for key, kid in key_ids.items()
                } == accepted
                Clock.month = 10
                for status in (400, 503):
                    assert (
                        await client.get(
                            f"/data/{status}", headers={"X-API-Key": "quota-a"}
                        )
                    ).status_code == status
                assert counters("2026-10") == (2, 2)
                with psycopg.connect(dsn) as conn:
                    conn.execute(
                        "CREATE FUNCTION reject_key_write() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$ BEGIN "
                        "RAISE EXCEPTION 'injected second write'; END $$"
                    )
                    conn.execute(
                        "CREATE TRIGGER reject_key_write BEFORE INSERT OR UPDATE "
                        "ON quota_usage FOR EACH ROW "
                        "EXECUTE FUNCTION reject_key_write()"
                    )
                assert (
                    await client.get("/data/200", headers={"X-API-Key": "quota-b"})
                ).status_code == 500
                assert counters("2026-10") == (2, 2)
                with psycopg.connect(dsn) as conn:
                    conn.execute("DROP TRIGGER reject_key_write ON quota_usage")
                assert (
                    await client.get("/data/200", headers={"X-API-Key": "quota-b"})
                ).status_code == 200
                assert counters("2026-10") == (3, 3)
                assert (
                    await client.get("/data/200", headers={"X-API-Key": "invalid"})
                ).status_code == 403
                assert counters("2026-10") == (3, 3)
                assert counters("2026-09") == (3, 3)
                Clock.month = 11
                orphan_results = [
                    await client.get("/data/200", headers={"X-API-Key": "quota-orphan"})
                    for _ in range(2)
                ]
                assert [r.status_code for r in orphan_results] == [200, 429]
                assert counters("2026-11") == (0, 1)
                with psycopg.connect(dsn) as conn:
                    assert conn.execute(
                        "SELECT request_count FROM quota_usage "
                        "WHERE api_key_id=%s AND month='2026-11'",
                        (orphan_id,),
                    ).fetchone() == (1,)
        finally:
            await engine.dispose()

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))


# The shared public key is limited per visitor IP (stored key_prefix).
_ANON = "dc_anon_" + "a" * 43
_LIVE = "dc_live_" + "b" * 43


def test_anon_key_counts_per_ip_and_its_monthly_row_never_blocks(
    isolated_pg_dsn, monkeypatch
):
    dsn = isolated_pg_dsn
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "database_url", dsn)
    monkeypatch.setattr(settings, "anon_rate_limit_per_ip_minute", 3)
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    command.upgrade(cfg, "head")
    with psycopg.connect(dsn) as conn:
        anon_id = conn.execute(
            "INSERT INTO api_keys(key_hash,name,quota_monthly,key_prefix) "
            "VALUES(%s,'frontend public anon key',1,%s) RETURNING id",
            (service.hash_api_key(_ANON), _ANON[:16] + "..."),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_keys(key_hash,name,quota_monthly,key_prefix) "
            "VALUES(%s,'cli key',1,%s)",
            (service.hash_api_key(_LIVE), _LIVE[:16] + "..."),
        )

    async def exercise():
        engine = create_async_engine(
            dsn.replace("postgresql://", "postgresql+asyncpg://")
        )
        sessions = async_sessionmaker(engine)

        async def db():
            async with sessions() as session:
                yield session

        app = FastAPI()
        app.dependency_overrides[get_db] = db
        app.add_exception_handler(ErreurApiError, _handle_exception_api)

        @app.get("/data", dependencies=[Depends(dependencies.check_quota)])
        async def data():
            return JSONResponse({"ok": True})

        def client(ip: str) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=(ip, 40000)),
                base_url="http://local.test",
            )

        try:
            async with client("198.51.100.1") as first, client("203.0.113.9") as other:
                anon = {"X-API-Key": _ANON}
                first_statuses = [
                    (await first.get("/data", headers=anon)).status_code
                    for _ in range(4)
                ]
                other_status = (await other.get("/data", headers=anon)).status_code
                live = {"X-API-Key": _LIVE}
                live_statuses = [
                    (await first.get("/data", headers=live)).status_code
                    for _ in range(2)
                ]
        finally:
            await engine.dispose()
        return first_statuses, other_status, live_statuses

    first_statuses, other_status, live_statuses = asyncio.run(exercise())

    # quota_monthly=1 never blocks the public key; its per-IP ceiling (3) does.
    assert first_statuses == [200, 200, 200, 429]
    assert other_status == 200
    # A nominative key keeps its own monthly quota.
    assert live_statuses == [200, 429]
    with psycopg.connect(dsn) as conn:
        supervised = conn.execute(
            "SELECT SUM(request_count) FROM quota_usage WHERE api_key_id=%s",
            (anon_id,),
        ).fetchone()[0]
    assert supervised == 4
