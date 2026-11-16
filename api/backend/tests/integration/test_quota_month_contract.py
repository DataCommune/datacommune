"""UTC monthly accounting against real SQL, with an explicit test clock seam.

A private schema shadows unqualified now(); PostgreSQL and host clocks are
never changed. Application SQL is executed verbatim in both session timezones.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.admin import queries as admin
from app.auth import queries as auth
from app.auth.models import UserInfo
from app.auth.quota import reserve_quota
from app.config import settings

pytestmark = pytest.mark.destructive
INSTANT = datetime(2026, 9, 30, 22, 30, tzinfo=UTC)


class Clock:
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return INSTANT.astimezone(tz)
        return INSTANT.astimezone(ZoneInfo("Europe/Paris")).replace(tzinfo=None)


@pytest.fixture
def stock(isolated_pg_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    monkeypatch.setattr(admin, "datetime", Clock)
    monkeypatch.setattr(auth, "datetime", Clock)
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    command.upgrade(cfg, "head")
    with psycopg.connect(isolated_pg_dsn) as conn:
        # The existing harness owns one child per module, so every parameter
        # gets fresh accounting rows and its own SQL clock within that child.
        conn.execute("TRUNCATE users CASCADE")
        conn.execute("DROP TRIGGER IF EXISTS reject_audit ON admin_audit_log")
        conn.execute("DROP FUNCTION IF EXISTS reject_audit()")
        conn.execute("DROP SCHEMA IF EXISTS quota_clock CASCADE")
        conn.execute("CREATE SCHEMA quota_clock")
        conn.execute(
            "CREATE FUNCTION quota_clock.now() RETURNS timestamptz LANGUAGE sql "
            "IMMUTABLE AS $$ SELECT '2026-09-30T22:30:00Z'::timestamptz $$"
        )
        ids = []
        for name in ("admin", "target", "other"):
            ids.append(  # noqa: PERF401
                conn.execute(
                    "INSERT INTO users(email,password_hash,is_verified,is_admin) "
                    "VALUES(%s,'!test',true,%s) RETURNING id",
                    (f"{name}@example.com", name == "admin"),
                ).fetchone()[0]
            )
        aid, uid, other = ids
        keys = []
        for index in range(2):
            keys.append(  # noqa: PERF401
                conn.execute(
                    "INSERT INTO api_keys(key_hash,name,user_id,quota_monthly) "
                    "VALUES(%s,%s,%s,500) RETURNING id",
                    (f"synthetic-key-{index}", f"key-{index}", uid),
                ).fetchone()[0]
            )
        conn.execute(
            "INSERT INTO quota_usage_account(user_id,month,request_count) "
            "VALUES(%s,'2026-09',7),(%s,'2026-10',11),(%s,'2026-09',19)",
            (uid, uid, other),
        )
        for key, september, october in ((keys[0], 3, 13), (keys[1], 4, 17)):
            conn.execute(
                "INSERT INTO quota_usage(api_key_id,month,request_count) "
                "VALUES(%s,'2026-09',%s),(%s,'2026-10',%s)",
                (key, september, key, october),
            )
    return isolated_pg_dsn, aid, uid, other, dict(zip(keys, (3, 4), strict=True))


def engine_for(dsn, timezone):
    return create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={
            "server_settings": {
                "timezone": timezone,
                "search_path": "quota_clock,pg_catalog,public",
            }
        },
    )


async def assert_clock(db, timezone):
    row = (
        await db.execute(
            text(
                "SELECT now(),TO_CHAR(now(),'YYYY-MM'),"
                "TO_CHAR(now() AT TIME ZONE 'UTC','YYYY-MM')"
            )
        )
    ).one()
    assert row[0] == INSTANT
    assert row[1] == ("2026-10" if timezone == "Europe/Paris" else "2026-09")
    assert row[2] == "2026-09"


@pytest.mark.parametrize("timezone", ["Europe/Paris", "UTC"])
@pytest.mark.parametrize(
    "reader", ["users", "detail", "detail_keys", "admin_keys", "user_keys"]
)
async def test_monthly_readers_follow_admission_utc_month(stock, timezone, reader):
    dsn, _, uid, _, expected_keys = stock
    engine = engine_for(dsn, timezone)
    try:
        async with async_sessionmaker(engine)() as db:
            await assert_clock(db, timezone)
            if reader == "users":
                rows, total = await admin.list_admin_users(
                    db, "target@", None, None, None, None, 0, 20
                )
                assert total == 1
                assert rows[0]["quota_used"] == 7
            elif reader in ("detail", "detail_keys"):
                result = await admin.get_admin_user_detail(db, uid)
                if reader == "detail":
                    assert result["quota_used"] == 7
                else:
                    assert {
                        r["id"]: r["quota_used"] for r in result["api_keys"]
                    } == expected_keys
            elif reader == "admin_keys":
                rows, total = await admin.list_admin_api_keys(
                    db, "target@", None, 1, 20
                )
                assert total == 2
                assert {r.id: r.quota_used for r in rows} == expected_keys
            else:
                rows = await auth.list_user_keys(db, uid)
                assert {r["id"]: r["usage_count"] for r in rows} == expected_keys
    finally:
        await engine.dispose()


def admin_identity(aid):
    return UserInfo(
        id=aid,
        email="admin@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=True,
        created_at=INSTANT,
        updated_at=INSTANT,
    )


def snapshot(dsn):
    with psycopg.connect(dsn) as conn:
        return (
            conn.execute(
                "SELECT user_id,month,request_count FROM quota_usage_account "
                "ORDER BY 1,2"
            ).fetchall(),
            conn.execute(
                "SELECT api_key_id,month,request_count FROM quota_usage ORDER BY 1,2"
            ).fetchall(),
        )


@pytest.mark.parametrize("timezone", ["Europe/Paris", "UTC"])
async def test_reset_only_utc_account_month_and_aligned_audit(stock, timezone):
    dsn, aid, uid, _, _ = stock
    before = snapshot(dsn)
    engine = engine_for(dsn, timezone)
    try:
        async with async_sessionmaker(engine)() as db:
            await assert_clock(db, timezone)
            result = await admin.admin_reset_quota(
                db,
                admin_identity(aid),
                uid,
                "Synthetic UTC reset contract",
                Request({"type": "http", "headers": [], "client": ("127.0.0.1", 0)}),
            )
    finally:
        await engine.dispose()
    after = snapshot(dsn)
    assert after == (
        [
            (user, month, 0 if (user, month) == (uid, "2026-09") else count)
            for user, month, count in before[0]
        ],
        before[1],
    )
    assert result == {"quota_used": 0, "month": "2026-09"}
    with psycopg.connect(dsn) as conn:
        assert conn.execute(
            "SELECT before,after FROM admin_audit_log WHERE action='reset_quota'"
        ).fetchall() == [({"quota_used": 7, "month": "2026-09"}, result)]


@pytest.mark.parametrize(
    "account_row_present", [True, False], ids=["existing-row", "created-zero-row"]
)
async def test_audit_write_failure_rolls_back_real_reset_when_session_closes(
    stock, account_row_present
):
    dsn, aid, uid, _, _ = stock
    if not account_row_present:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM quota_usage_account WHERE user_id=%s AND month='2026-09'",
                (uid,),
            )
    before = snapshot(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "CREATE FUNCTION reject_audit() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'synthetic audit failure'; END $$"
        )
        conn.execute(
            "CREATE TRIGGER reject_audit BEFORE INSERT ON admin_audit_log "
            "FOR EACH ROW EXECUTE FUNCTION reject_audit()"
        )
    engine = engine_for(dsn, "Europe/Paris")
    try:
        with pytest.raises(Exception, match="synthetic audit failure"):
            async with async_sessionmaker(engine)() as db:
                await admin.admin_reset_quota(
                    db,
                    admin_identity(aid),
                    uid,
                    "Synthetic failing audit contract",
                    Request(
                        {"type": "http", "headers": [], "client": ("127.0.0.1", 0)}
                    ),
                )
    finally:
        await engine.dispose()
    assert snapshot(dsn) == before
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM admin_audit_log").fetchone() == (0,)


@pytest.mark.parametrize(
    "account_row_present", [True, False], ids=["existing-row", "first-month-request"]
)
async def test_reset_and_admission_preserve_accounting_without_imposing_order(  # noqa: C901
    stock, account_row_present
):
    """Pause after actual SELECT; observe admission completion or its exact blocker.

    The barrier delays execution only. It never changes SQL, results or commit.
    Conservation allows either operation to linearize first.
    """
    dsn, aid, uid, _, keys = stock
    key = next(iter(keys))
    if not account_row_present:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM quota_usage_account WHERE user_id=%s AND month='2026-09'",
                (uid,),
            )
            conn.execute(
                "DELETE FROM quota_usage WHERE api_key_id=ANY(%s) AND month='2026-09'",
                (list(keys),),
            )
    selected = asyncio.Event()
    release = asyncio.Event()

    class PausedResetSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            query = str(statement)
            if (
                "SELECT COALESCE(request_count" in query
                and "quota_usage_account" in query
            ):
                selected.set()
                await asyncio.wait_for(release.wait(), timeout=10)
            return result

    engine = engine_for(dsn, "Europe/Paris")
    tasks = []
    before = snapshot(dsn)
    try:
        async with (
            async_sessionmaker(engine, class_=PausedResetSession)() as reset_db,
            async_sessionmaker(engine)() as admission_db,
        ):
            reset_pid = (
                await reset_db.execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()
            admission_pid = (
                await admission_db.execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()
            assert reset_pid != admission_pid
            reset_task = asyncio.create_task(
                admin.admin_reset_quota(
                    reset_db,
                    admin_identity(aid),
                    uid,
                    "Synthetic concurrent UTC reset",
                    Request(
                        {"type": "http", "headers": [], "client": ("127.0.0.1", 0)}
                    ),
                )
            )
            tasks.append(reset_task)
            await asyncio.wait_for(selected.wait(), timeout=5)
            admission_task = asyncio.create_task(
                reserve_quota(
                    admission_db,
                    key_id=key,
                    user_id=uid,
                    month="2026-09",
                    limit=500,
                )
            )
            tasks.append(admission_task)
            observation = None
            deadline = asyncio.get_running_loop().time() + 5
            with psycopg.connect(dsn, autocommit=True) as observer:
                while asyncio.get_running_loop().time() < deadline:
                    if admission_task.done():
                        observation = "admission_completed_before_reset_release"
                        break
                    blockers = observer.execute(
                        "SELECT pg_blocking_pids(%s)", (admission_pid,)
                    ).fetchone()[0]
                    if blockers:
                        assert reset_pid in blockers, (
                            reset_pid,
                            admission_pid,
                            blockers,
                        )
                        observation = "admission_blocked_by_exact_reset_pid"
                        break
                    # Poll a concrete PostgreSQL condition; elapsed time is
                    # never interpreted as completion or lock acquisition.
                    await asyncio.sleep(0.01)
            assert observation is not None, (
                "No admission completion or owned blocker observed"
            )
            release.set()
            reset_result, admitted = await asyncio.wait_for(
                asyncio.gather(reset_task, admission_task), timeout=5
            )
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()

    assert admitted is not None
    after = snapshot(dsn)
    with psycopg.connect(dsn) as conn:
        audit_before, audit_after = conn.execute(
            "SELECT before,after FROM admin_audit_log WHERE action='reset_quota'"
        ).fetchone()
    initial = next(
        (c for owner, month, c in before[0] if (owner, month) == (uid, "2026-09")), 0
    )
    final = next(
        c for owner, month, c in after[0] if (owner, month) == (uid, "2026-09")
    )
    proof = {
        "observation": observation,
        "reset_pid": reset_pid,
        "admission_pid": admission_pid,
        "initial_account": initial,
        "successful_admissions": 1,
        "audit_before": audit_before,
        "audit_after": audit_after,
        "final_account": final,
        "conservation_expected": initial + 1 - audit_before["quota_used"],
        "clock_seam": "private now() fixed2026-09-30T22:30Z; session Europe/Paris",
        "account_row_present_initially": account_row_present,
    }
    print("QUOTA_RESET_RACE " + json.dumps(proof, sort_keys=True))
    if output := os.environ.get("DC_QUOTA_PROOF_DIR"):
        suffix = "existing" if account_row_present else "absent"
        (Path(output) / f"reset-admission-{suffix}-observation.json").write_text(
            json.dumps(proof, indent=2), encoding="utf-8"
        )
    assert final == initial + 1 - audit_before["quota_used"]
    assert audit_after == reset_result == {"quota_used": 0, "month": "2026-09"}
    expected_keys = {(owner, month): c for owner, month, c in before[1]}
    expected_keys[key, "2026-09"] = expected_keys.get((key, "2026-09"), 0) + 1
    assert after[1] == sorted(
        (owner, month, c) for (owner, month), c in expected_keys.items()
    )
    assert [r for r in after[0] if r[:2] != (uid, "2026-09")] == [
        r for r in before[0] if r[:2] != (uid, "2026-09")
    ]
