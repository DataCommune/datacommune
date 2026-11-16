"""L08-01: recorded contracts are not evidence of recurring revenue."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.overview import router
from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.config import settings
from app.database import get_db

pytestmark = pytest.mark.destructive


@pytest.fixture()
def revenue_db(isolated_pg_dsn, monkeypatch):
    dsn = isolated_pg_dsn
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "database_url", dsn)
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    command.upgrade(cfg, "head")
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE users CASCADE")
        uid = conn.execute(
            "INSERT INTO users(email,password_hash,is_verified,stripe_customer_id) "
            "VALUES ('revenue@example.com','!fixture',true,'cus_fixture') RETURNING id"
        ).fetchone()[0]
    return dsn, uid


def insert_contract(
    conn,
    uid,
    suffix,
    *,
    source="stripe",
    plan="pro",
    status="active",
    sub_id=None,
    price_id="price_annual_fixture",
    created="2026-01-01",
    cancel=False,
):
    conn.execute(
        "INSERT INTO subscriptions(user_id,stripe_subscription_id,stripe_price_id,"
        "plan_name,status,source,created_at,cancel_at_period_end) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uid,
            sub_id or f"sub_{suffix}",
            price_id,
            plan,
            status,
            source,
            created,
            cancel,
        ),
    )


def overview(dsn):
    async def exercise():
        engine = create_async_engine(
            dsn.replace("postgresql://", "postgresql+asyncpg://")
        )
        sessions = async_sessionmaker(engine)

        async def db():
            async with sessions() as session:
                # Enforced by PostgreSQL, not just a mocked commit assertion.
                await session.execute(text("SET TRANSACTION READ ONLY"))
                yield session

        now = datetime.now(UTC)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_current_user] = lambda: UserInfo(
            id=99001,
            email="admin@example.com",
            name=None,
            company=None,
            is_admin=True,
            is_verified=True,
            created_at=now,
            updated_at=now,
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture"
            ) as client:
                response = await client.get("/v1/admin/overview")
                assert response.status_code == 200, response.text
                return response.json()
        finally:
            await engine.dispose()

    return asyncio.run(exercise())


def assert_unavailable(payload):
    assert payload["mrr_eur_estimated"] is None
    assert payload["mrr_status"] == "unavailable"
    assert payload["mrr_reason"] == "billing_amounts_not_persisted"
    assert payload["users_count"] == 1
    assert payload["active_keys_count"] == 0
    assert payload["requests_24h"] == 0


@pytest.mark.parametrize("plan", [None, "pro", "internal", "free"])
def test_gratis_grants_and_empty_stock_never_become_revenue(revenue_db, plan):
    dsn, uid = revenue_db
    if plan is not None:
        with psycopg.connect(dsn) as conn:
            insert_contract(
                conn,
                uid,
                "grant",
                source="manual_grant",
                plan=plan,
                sub_id="manual_grant_fixture",
            )
    payload = overview(dsn)
    assert_unavailable(payload)
    if plan == "pro":
        capture = os.environ.get("MRR_CAPTURE_PATH")
        if capture:
            Path(capture).write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        else:
            fixture = (
                Path(__file__).parents[4]
                / "docs/audit-stabilisation-2026-09-08/lot-08/mrr-api-fixture.json"
            )
            assert payload == json.loads(fixture.read_text(encoding="utf-8"))


def test_history_overrides_and_cancel_do_not_replace_contract_state(revenue_db):
    dsn, uid = revenue_db
    with psycopg.connect(dsn) as conn:
        insert_contract(conn, uid, "annual", cancel=True)
        insert_contract(conn, uid, "second", plan="business")
        insert_contract(conn, uid, "cancelled", status="canceled", created="2026-09-01")
        insert_contract(
            conn,
            uid,
            "grant",
            source="manual_grant",
            sub_id="manual_grant_override",
            created="2026-09-02",
        )
        insert_contract(
            conn,
            uid,
            "internal",
            source="manual_grant",
            plan="internal",
            created="2026-09-03",
        )
    payload = overview(dsn)
    assert_unavailable(payload)
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE subscriptions SET status='canceled' "
            "WHERE stripe_subscription_id='sub_annual'"
        )
    assert_unavailable(overview(dsn))


def test_inconsistent_active_stripe_rows_never_produce_nominal_revenue(revenue_db):
    dsn, uid = revenue_db
    with psycopg.connect(dsn) as conn:
        insert_contract(conn, uid, "valid")
        insert_contract(conn, uid, "unknown", plan="unmapped")
        insert_contract(conn, uid, "badid", sub_id="manual_grant_wrong_source")
        insert_contract(conn, uid, "noprice", price_id="")
        insert_contract(conn, uid, "inactive", status="past_due")
    payload = overview(dsn)
    assert_unavailable(payload)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE users SET stripe_customer_id=NULL WHERE id=%s", (uid,))
    payload = overview(dsn)
    assert_unavailable(payload)
