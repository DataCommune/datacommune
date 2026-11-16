"""COR-17: real local transactions; the Stripe provider is strictly simulated."""

import asyncio
import itertools
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin import queries as admin_queries
from app.billing import queries, router, service
from app.config import settings
from app.database import get_db

pytestmark = pytest.mark.destructive


@pytest.fixture()
def billing_db(isolated_pg_dsn, monkeypatch):
    dsn = isolated_pg_dsn
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "database_url", dsn)
    monkeypatch.setattr(
        settings, "jwt_secret_key", "cor17-fixture-only-not-a-credential"
    )
    monkeypatch.setattr(
        settings, "stripe_price_business_monthly", "price_business_fixture"
    )
    monkeypatch.setattr(settings, "stripe_price_pro_monthly", "price_pro_fixture")
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    command.upgrade(cfg, "head")
    with psycopg.connect(dsn) as conn:
        # This module owns its child DB; each case starts without prior accounts.
        conn.execute("DROP FUNCTION IF EXISTS reject_billing_write() CASCADE")
        conn.execute("TRUNCATE users CASCADE")
        conn.execute("TRUNCATE stripe_events")
        uid = conn.execute(
            "INSERT INTO users(email,password_hash,is_verified,stripe_customer_id) "
            "VALUES('buyer@example.com','!fixture',true,'cus_fixture') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_keys(key_hash,name,user_id,quota_monthly) "
            "VALUES('billingfixture','fixture',%s,500)",
            (uid,),
        )
    return dsn, uid


def event(event_id, event_type, status="active", **overrides):
    obj = {
        "id": "sub_fixture",
        "customer": "cus_fixture",
        "status": status,
        "items": {
            "data": [
                {
                    "price": {"id": "price_pro_fixture"},
                    "current_period_start": 1700000000,
                    "current_period_end": 1702592000,
                }
            ]
        },
        "cancel_at_period_end": False,
        **overrides,
    }
    return {
        "id": event_id,
        "type": event_type,
        "created": 1700000000,
        "data": {"object": obj},
    }


def test_old_and_same_second_events_reconcile_the_current_provider_state(
    billing_db, monkeypatch
):
    dsn, uid = billing_db
    canonical = event("canonical", "customer.subscription.updated", "canceled")["data"][
        "object"
    ]
    provider = Mock()
    provider.subscriptions.retrieve_async = AsyncMock(return_value=canonical)
    monkeypatch.setattr("stripe.StripeClient", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(
        router,
        "verify_webhook_signature",
        lambda payload, _signature: json.loads(payload),
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
        app.include_router(router.router)
        app.dependency_overrides[get_db] = db
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture"
            ) as client:
                for payload in [
                    event("evt_deleted", "customer.subscription.deleted", "canceled"),
                    event("evt_old", "customer.subscription.updated", "active"),
                ]:
                    response = await client.post("/stripe/webhook", json=payload)
                    assert response.status_code == 200, response.text
            with psycopg.connect(dsn) as conn:
                assert conn.execute(
                    "SELECT status FROM subscriptions WHERE user_id=%s", (uid,)
                ).fetchone() == ("canceled",)
                assert conn.execute(
                    "SELECT quota_monthly FROM api_keys WHERE user_id=%s", (uid,)
                ).fetchone() == (500,)
            assert provider.subscriptions.retrieve_async.await_count == 2
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@asynccontextmanager
async def client_for(dsn, monkeypatch, retrieve, *, user=None, checkout=None):
    provider = Mock()
    provider.subscriptions.retrieve_async = retrieve
    if checkout is not None:
        provider.checkout.sessions.create_async = checkout
    monkeypatch.setattr("stripe.StripeClient", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(
        router,
        "verify_webhook_signature",
        lambda payload, _signature: json.loads(payload),
    )
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    sessions = async_sessionmaker(engine)

    async def db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_db] = db
    if user is not None:
        app.dependency_overrides[router.get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            yield client, sessions
    finally:
        await engine.dispose()


def state(dsn):
    with psycopg.connect(dsn) as conn:
        return tuple(
            conn.execute(query).fetchall()
            for query in [
                "SELECT id,stripe_customer_id FROM users ORDER BY id",
                "SELECT user_id,stripe_subscription_id,plan_name,status,source "
                "FROM subscriptions ORDER BY id",
                "SELECT user_id,quota_monthly FROM api_keys ORDER BY id",
                "SELECT event_id,event_type FROM stripe_events ORDER BY event_id",
            ]
        )


def test_all_event_permutations_and_same_timestamps_converge(billing_db, monkeypatch):
    dsn, _uid = billing_db
    canonical = event("canonical", "customer.subscription.updated", "canceled")["data"][
        "object"
    ]
    retrieve = AsyncMock(return_value=canonical)

    async def exercise():
        async with client_for(dsn, monkeypatch, retrieve) as (client, _):
            for n, order in enumerate(
                itertools.permutations(["created", "updated", "deleted"])
            ):
                for kind in order:
                    payload = event(
                        f"evt_{n}_{kind}", f"customer.subscription.{kind}", "active"
                    )
                    response = await client.post("/stripe/webhook", json=payload)
                    assert response.status_code == 200, response.text
                    assert state(dsn)[1][0][3] == "canceled"
                    assert state(dsn)[2][0][1] == 500
            assert len(state(dsn)[3]) == 18
            assert retrieve.await_count == 18

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "status",
    [
        "trialing",
        "past_due",
        "unpaid",
        "paused",
        "incomplete",
        "incomplete_expired",
        "canceled",
    ],
)
def test_only_active_status_grants_plan_quota(billing_db, monkeypatch, status):
    dsn, _uid = billing_db
    canonical = event("canonical", "", status)["data"]["object"]

    async def exercise():
        async with client_for(dsn, monkeypatch, AsyncMock(return_value=canonical)) as (
            client,
            _,
        ):
            response = await client.post(
                "/stripe/webhook",
                json=event("evt_status", "customer.subscription.updated"),
            )
            assert response.status_code == 200
            assert state(dsn)[1][0][3] == status
            assert state(dsn)[2][0][1] == 500

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "failed_table", ["users", "subscriptions", "api_keys", "stripe_events"]
)
def test_failure_after_each_write_rolls_back_everything_then_retry_succeeds(
    billing_db, monkeypatch, failed_table
):
    dsn, uid = billing_db
    binding = service.make_checkout_binding(uid, None)
    canonical = event("canonical", "", metadata=binding)["data"]["object"]
    checkout = event(
        "evt_atomic",
        "checkout.session.completed",
        subscription="sub_fixture",
        metadata=binding,
        customer_email="victim@example.com",
    )
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE users SET stripe_customer_id=NULL WHERE id=%s", (uid,))
        conn.execute(
            "CREATE FUNCTION reject_billing_write() RETURNS trigger LANGUAGE "
            "plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected billing write'; END $$"
        )
        conn.execute(
            f"CREATE TRIGGER reject_billing_write AFTER INSERT OR UPDATE ON "
            f"{failed_table} FOR EACH ROW EXECUTE FUNCTION reject_billing_write()"
        )
    before = state(dsn)

    async def exercise():
        retrieve = AsyncMock(return_value=canonical)
        async with client_for(dsn, monkeypatch, retrieve) as (client, _):
            response = await client.post("/stripe/webhook", json=checkout)
            assert response.status_code == 503, response.text
            assert response.json()["retryable"] is True
            assert state(dsn) == before
            with psycopg.connect(dsn) as conn:
                conn.execute(f"DROP TRIGGER reject_billing_write ON {failed_table}")
            response = await client.post("/stripe/webhook", json=checkout)
            assert response.status_code == 200, response.text
            after = state(dsn)
            assert after[0][0][1] == "cus_fixture"
            assert after[1][0][3] == "active"
            assert after[2][0][1] == 5000
            assert len(after[3]) == 1
            response = await client.post("/stripe/webhook", json=checkout)
            assert response.json()["status"] == "already_processed"
            assert state(dsn) == after
            assert retrieve.await_count == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["duplicate", "same_subscription", "same_customer"])
def test_concurrent_events_serialize_before_provider_read(
    billing_db, monkeypatch, kind
):
    dsn, _uid = billing_db

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def retrieve(sid):
            calls.append(sid)
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return event("canonical", "", "canceled", id=sid)["data"]["object"]

        async with client_for(dsn, monkeypatch, retrieve) as (client, _):
            first = event("evt_race_a", "customer.subscription.updated")
            second = event(
                "evt_race_a" if kind == "duplicate" else "evt_race_b",
                "customer.subscription.updated",
                id="sub_second" if kind == "same_customer" else "sub_fixture",
            )
            task1 = asyncio.create_task(client.post("/stripe/webhook", json=first))
            await asyncio.wait_for(entered.wait(), 3)
            task2 = asyncio.create_task(client.post("/stripe/webhook", json=second))
            try:
                async with asyncio.timeout(3):
                    while True:
                        with psycopg.connect(dsn) as conn:
                            waiting = conn.execute(
                                "SELECT count(*) FROM pg_stat_activity WHERE "
                                "datname=current_database() AND "
                                "wait_event='advisory'"
                            ).fetchone()[0]
                        if waiting:
                            break
                        await asyncio.sleep(0.01)
                assert len(calls) == 1, (
                    "provider snapshot must be fetched only after the lock"
                )
            finally:
                release.set()
            responses = await asyncio.gather(task1, task2)
            assert [r.status_code for r in responses] == [200, 200]
            assert len(calls) == (1 if kind == "duplicate" else 2)
            assert len(state(dsn)[3]) == (1 if kind == "duplicate" else 2)
            assert all(row[3] == "canceled" for row in state(dsn)[1])

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "case",
    [
        "unsigned_new",
        "tampered",
        "provider_customer",
        "existing_customer",
        "signed_customer",
        "checkout_mismatch",
        "subscription_owner",
        "missing_account",
    ],
)
def test_unproved_or_discordant_customer_refuses_without_any_write(
    billing_db, monkeypatch, case
):
    dsn, uid = billing_db
    binding = service.make_checkout_binding(uid, None)
    canonical = event("canonical", "", metadata=binding)["data"]["object"]
    checkout = event(
        "evt_conflict",
        "checkout.session.completed",
        subscription="sub_fixture",
        metadata=binding,
        customer_email="buyer@example.com",
    )
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE users SET stripe_customer_id=NULL WHERE id=%s", (uid,))
        if case == "unsigned_new":
            canonical["metadata"] = {}
            checkout["data"]["object"]["metadata"] = {}
        elif case == "tampered":
            canonical["metadata"] = {**binding, "dc_signature": "0" * 64}
        elif case == "provider_customer":
            canonical["customer"] = "cus_wrong"
        elif case == "existing_customer":
            conn.execute(
                "UPDATE users SET stripe_customer_id='cus_already_linked' WHERE id=%s",
                (uid,),
            )
        elif case == "signed_customer":
            canonical["metadata"] = service.make_checkout_binding(uid, "cus_different")
            checkout["data"]["object"]["metadata"] = canonical["metadata"]
        elif case == "checkout_mismatch":
            checkout["data"]["object"]["metadata"] = service.make_checkout_binding(
                uid, None
            )
        elif case == "subscription_owner":
            other = conn.execute(
                "INSERT INTO users(email,password_hash) "
                "VALUES('other@example.com','!fixture') RETURNING id"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO "
                "subscriptions(user_id,stripe_subscription_id,stripe_price_id,sourc"
                "e) VALUES(%s,'sub_fixture','price_pro_fixture','stripe')",
                (other,),
            )
        else:
            canonical["metadata"] = service.make_checkout_binding(999999, None)
            checkout["data"]["object"]["metadata"] = canonical["metadata"]
    before = state(dsn)

    async def exercise():
        async with client_for(dsn, monkeypatch, AsyncMock(return_value=canonical)) as (
            client,
            _,
        ):
            response = await client.post("/stripe/webhook", json=checkout)
            assert response.status_code == 409, response.text
            assert state(dsn) == before

    asyncio.run(exercise())


def test_grants_and_internal_rights_win_across_webhook_and_admin_readers(
    billing_db, monkeypatch
):
    dsn, uid = billing_db
    with psycopg.connect(dsn) as conn:
        for sid, plan, source in [
            ("manual_a", "business", "manual_grant"),
            ("manual_b", "enterprise", "manual_grant"),
        ]:
            conn.execute(
                "INSERT INTO "
                "subscriptions(user_id,stripe_subscription_id,stripe_price_id,plan_"
                "name,status,source,created_at) VALUES(%s,%s,'',%s,'active',%s,'202"
                "0-01-01')",
                (uid, sid, plan, source),
            )
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO subscriptions(user_id,stripe_subscription_id,"
            "stripe_price_id,plan_name,status,source,created_at) "
            "VALUES(%s,'sub_canceled_newer','','pro','canceled','stripe',"
            "NOW() + INTERVAL '1 day')",
            (uid,),
        )
    canonical = event("canonical", "")["data"]["object"]

    async def exercise():
        async with client_for(dsn, monkeypatch, AsyncMock(return_value=canonical)) as (
            client,
            sessions,
        ):
            for expected_plan, quota in [("enterprise", 100000), ("internal", -1)]:
                response = await client.post(
                    "/stripe/webhook",
                    json=event("evt_" + expected_plan, "customer.subscription.updated"),
                )
                assert response.status_code == 200, response.text
                assert state(dsn)[2][0][1] == quota
                async with sessions() as db:
                    effective = await queries.get_active_subscription(db, uid)
                    assert effective["plan_name"] == expected_plan
                    from sqlalchemy import text

                    listed = (
                        (
                            await db.execute(
                                text(admin_queries._LIST_SELECT + " WHERE u.id=:uid"),
                                {"uid": uid},
                            )
                        )
                        .mappings()
                        .one()
                    )
                    assert listed["plan"] == expected_plan
                    detail = await admin_queries.get_admin_user_detail(db, uid)
                    assert detail["plan"] == expected_plan
                    assert detail["subscription"]["plan_name"] == expected_plan
                    assert detail["subscription"]["status"] == "active"
                if expected_plan == "enterprise":
                    with psycopg.connect(dsn) as conn:
                        conn.execute(
                            "INSERT INTO "
                            "subscriptions(user_id,stripe_subscription_id,stripe_pr"
                            "ice_id,plan_name,status,source,created_at) VALUES(%s,'"
                            "internal_fixture','','internal','active','manual_grant"
                            "','2010-01-01')",
                            (uid,),
                        )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "problem",
    ["unknown_price", "no_items", "multi_items", "provider_down", "missing_customer"],
)
def test_incomplete_provider_state_is_retryable_not_acknowledged(
    billing_db, monkeypatch, problem
):
    dsn, _uid = billing_db
    canonical = event("canonical", "")["data"]["object"]
    payload = event("evt_incomplete", "customer.subscription.updated")
    if problem == "unknown_price":
        canonical["items"]["data"][0]["price"]["id"] = "price_not_configured"
    if problem == "no_items":
        canonical["items"]["data"] = []
    if problem == "multi_items":
        canonical["items"]["data"] *= 2
    if problem == "missing_customer":
        payload["data"]["object"].pop("customer")
    before = state(dsn)
    retrieve = (
        AsyncMock(side_effect=RuntimeError("provider unavailable"))
        if problem == "provider_down"
        else AsyncMock(return_value=canonical)
    )

    async def exercise():
        async with client_for(dsn, monkeypatch, retrieve) as (client, _):
            response = await client.post("/stripe/webhook", json=payload)
            assert response.status_code == (
                409 if problem == "missing_customer" else 503
            )
            assert state(dsn) == before

    asyncio.run(exercise())


def user_info(uid):
    from datetime import UTC, datetime

    from app.auth.models import UserInfo

    now = datetime.now(UTC)
    return UserInfo(
        id=uid,
        email="buyer@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=True,
        created_at=now,
        updated_at=now,
    )


def test_checkout_account_binding_survives_mutable_email(billing_db, monkeypatch):
    dsn, uid = billing_db
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE users SET stripe_customer_id=NULL WHERE id=%s", (uid,))
    captured = {}

    async def create(params):
        captured.update(params)
        return Mock(url="https://checkout.fixture/session")

    async def retrieve(_sid):
        return event(
            "canonical", "", metadata=captured["subscription_data"]["metadata"]
        )["data"]["object"]

    async def exercise():
        async with client_for(
            dsn, monkeypatch, retrieve, user=user_info(uid), checkout=create
        ) as (client, _):
            response = await client.post(
                "/billing/checkout", json={"plan": "pro", "user_id": 999999}
            )
            assert response.status_code == 200, response.text
            assert service.read_checkout_binding(captured["metadata"])["uid"] == uid
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    "UPDATE users SET email='changed@example.com' WHERE id=%s", (uid,)
                )
                conn.execute(
                    "INSERT INTO users(email,password_hash) "
                    "VALUES('buyer@example.com','!fixture')"
                )
            response = await client.post(
                "/stripe/webhook",
                json=event(
                    "evt_checkout",
                    "checkout.session.completed",
                    subscription="sub_fixture",
                    metadata=captured["metadata"],
                    customer_details={"email": "buyer@example.com"},
                ),
            )
            assert response.status_code == 200, response.text
            current = state(dsn)
            assert current[0][0] == (uid, "cus_fixture")
            assert current[0][1][1] is None
            assert current[1][0][0] == uid

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["grant", "sync"])
def test_admin_audit_failure_rolls_back_plan_and_quota_then_retries(
    billing_db, monkeypatch, operation
):
    from starlette.requests import Request

    from app.admin import queries as admin

    dsn, uid = billing_db
    with psycopg.connect(dsn) as conn:
        admin_id = conn.execute(
            "INSERT INTO users(email,password_hash,is_admin) "
            "VALUES('admin@example.com','!fixture',true) RETURNING id"
        ).fetchone()[0]
        sid = conn.execute(
            "INSERT INTO "
            "subscriptions(user_id,stripe_subscription_id,stripe_price_id,plan_name"
            ",status,source) VALUES(%s,'sub_fixture','price_pro_fixture','pro','act"
            "ive','stripe') RETURNING id",
            (uid,),
        ).fetchone()[0]
        conn.execute(
            "CREATE FUNCTION reject_billing_write() RETURNS trigger LANGUAGE "
            "plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected audit'; END $$"
        )
        conn.execute(
            "CREATE TRIGGER reject_billing_write AFTER INSERT ON admin_audit_log "
            "FOR EACH ROW EXECUTE FUNCTION reject_billing_write()"
        )
    before = state(dsn)
    canonical = event("canonical", "")["data"]["object"]
    canonical["items"]["data"][0]["price"]["id"] = "price_business_fixture"

    async def exercise():
        async with client_for(dsn, monkeypatch, AsyncMock(return_value=canonical)) as (
            _,
            sessions,
        ):

            async def action():
                async with sessions() as db:
                    kwargs = {
                        "db": db,
                        "admin": user_info(admin_id),
                        "reason": "fixture audit atomicity",
                        "request": Request(
                            {"type": "http", "headers": [], "client": ("127.0.0.1", 1)}
                        ),
                    }
                    if operation == "sync":
                        return await admin.admin_sync_subscription_from_stripe(
                            **kwargs, sub_id=sid
                        )
                    return await admin.admin_grant_plan(
                        **kwargs,
                        target_user_id=uid,
                        plan_name="business",
                        end_date=None,
                    )

            from sqlalchemy.exc import DBAPIError

            with pytest.raises(DBAPIError, match="injected audit"):
                await action()
            assert state(dsn) == before
            with psycopg.connect(dsn) as conn:
                assert (
                    conn.execute("SELECT count(*) FROM admin_audit_log").fetchone()[0]
                    == 0
                )
                conn.execute("DROP TRIGGER reject_billing_write ON admin_audit_log")
            result = await action()
            assert result["quota_monthly"] == 25000
            assert state(dsn)[2][0][1] == 25000
            with psycopg.connect(dsn) as conn:
                assert (
                    conn.execute("SELECT count(*) FROM admin_audit_log").fetchone()[0]
                    == 1
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("first", ["grant", "webhook"])
def test_grant_and_webhook_share_account_serialization_without_deadlock(
    billing_db, monkeypatch, first
):
    from starlette.requests import Request

    from app.admin import queries as admin

    dsn, uid = billing_db
    with psycopg.connect(dsn) as conn:
        admin_id = conn.execute(
            "INSERT INTO users(email,password_hash,is_admin) "
            "VALUES('admin@example.com','!fixture',true) RETURNING id"
        ).fetchone()[0]
    canonical = event("canonical", "")["data"]["object"]

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        original = queries.upsert_subscription

        async def retrieve(_sid):
            if first == "grant":
                entered.set()
                await release.wait()
            return canonical

        async def upsert(*args, **kwargs):
            result = await original(*args, **kwargs)
            if first == "webhook":
                entered.set()
                await release.wait()
            return result

        monkeypatch.setattr(queries, "upsert_subscription", upsert)
        async with client_for(dsn, monkeypatch, retrieve) as (client, sessions):

            async def grant():
                async with sessions() as db:
                    return await admin.admin_grant_plan(
                        db,
                        user_info(admin_id),
                        uid,
                        "business",
                        None,
                        "concurrent fixture grant",
                        Request(
                            {"type": "http", "headers": [], "client": ("127.0.0.1", 1)}
                        ),
                    )

            task = asyncio.create_task(
                client.post(
                    "/stripe/webhook",
                    json=event("evt_grant_race", "customer.subscription.updated"),
                )
            )
            await asyncio.wait_for(entered.wait(), 3)
            grant_task = asyncio.create_task(grant())
            if first == "grant":
                await asyncio.wait_for(grant_task, 3)
            else:
                async with asyncio.timeout(3):
                    while True:
                        with psycopg.connect(dsn) as conn:
                            waiting = conn.execute(
                                "SELECT count(*) FROM pg_stat_activity WHERE "
                                "datname=current_database() AND "
                                "wait_event='advisory'"
                            ).fetchone()[0]
                        if waiting:
                            break
                        await asyncio.sleep(0.01)
            release.set()
            response, _ = await asyncio.wait_for(asyncio.gather(task, grant_task), 5)
            assert response.status_code == 200, response.text
            async with sessions() as db:
                assert (await queries.get_active_subscription(db, uid))[
                    "source"
                ] == "manual_grant"
            assert state(dsn)[2][0][1] == 25000

    asyncio.run(exercise())


def test_cancellation_keeps_another_active_subscription_and_invoice_reconciles(
    billing_db, monkeypatch
):
    dsn, uid = billing_db
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO "
            "subscriptions(user_id,stripe_subscription_id,stripe_price_id,plan_name"
            ",status,source) VALUES(%s,'sub_other','price_business_fixture','busine"
            "ss','active','stripe')",
            (uid,),
        )
    canonical = event("canonical", "", "canceled")["data"]["object"]

    async def exercise():
        async with client_for(dsn, monkeypatch, AsyncMock(return_value=canonical)) as (
            client,
            _,
        ):
            for kind in ["customer.subscription.deleted", "invoice.payment_failed"]:
                payload = event("evt_" + kind, kind, subscription="sub_fixture")
                response = await client.post("/stripe/webhook", json=payload)
                assert response.status_code == 200, response.text
                assert state(dsn)[2][0][1] == 25000

    asyncio.run(exercise())
