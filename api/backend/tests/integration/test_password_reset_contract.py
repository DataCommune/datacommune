"""COR-16: real row races and JWT revocation; no outbound email or shared DB."""

import asyncio
import inspect
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import router, service
from app.auth.dependencies import check_quota_hybrid, get_current_user
from app.common.exceptions import ErreurApiError, _handle_exception_api
from app.config import settings
from app.database import get_db

pytestmark = pytest.mark.destructive


def test_reset_sequential_concurrent_and_old_sessions(isolated_pg_dsn, monkeypatch):
    dsn = isolated_pg_dsn
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "database_url", dsn)
    monkeypatch.setattr(settings, "debug", True)
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    command.upgrade(cfg, "head")
    email = "reset-contract@example.com"
    initial = service.hash_password("initial-password")
    with psycopg.connect(dsn) as conn:
        uid = conn.execute(
            "INSERT INTO users(email,password_hash,is_verified) "
            "VALUES(%s,%s,true) RETURNING id",
            (email, initial),
        ).fetchone()[0]

    def reset_token(password_hash):
        # Allows the same counterexample to run against the original signature.
        if "password_hash" in inspect.signature(service.create_reset_token).parameters:
            return service.create_reset_token(email, password_hash)
        return service.create_reset_token(email)

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
        app.add_exception_handler(ErreurApiError, _handle_exception_api)
        app.dependency_overrides[get_db] = db
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://local.test"
            ) as client:
                login = await client.post(
                    "/auth/login", json={"email": email, "password": "initial-password"}
                )
                assert login.status_code == 200
                old_token = login.json()["access_token"]
                token = reset_token(initial)
                body = {"token": token, "new_password": "changed-password"}
                assert (
                    await client.post("/auth/reset-password", json=body)
                ).status_code == 200
                assert (
                    await client.post("/auth/reset-password", json=body)
                ).status_code == 400

                from starlette.requests import Request

                request = Request(
                    {
                        "type": "http",
                        "headers": [(b"cookie", f"access_token={old_token}".encode())],
                    }
                )
                async with sessions() as session:
                    for dependency in (get_current_user, check_quota_hybrid):
                        with pytest.raises(ErreurApiError) as raised:
                            if dependency is get_current_user:
                                await dependency(request, session)
                            else:
                                await dependency(request, None, session)
                        assert raised.value.code == 401

                new_login = await client.post(
                    "/auth/login", json={"email": email, "password": "changed-password"}
                )
                assert new_login.status_code == 200
                current_token = new_login.json()["access_token"]
                current_request = Request(
                    {
                        "type": "http",
                        "headers": [
                            (b"cookie", f"access_token={current_token}".encode())
                        ],
                    }
                )
                async with sessions() as session:
                    assert (await get_current_user(current_request, session)).id == uid
                with psycopg.connect(dsn) as conn:
                    current_hash = conn.execute(
                        "SELECT password_hash FROM users WHERE id=%s", (uid,)
                    ).fetchone()[0]
                concurrent_token = reset_token(current_hash)
                # Force both handlers to read the same credential generation.
                original_read = router.get_user_by_email
                barrier = asyncio.Barrier(2)

                async def synchronized_read(*args):
                    user = await original_read(*args)
                    await barrier.wait()
                    return user

                monkeypatch.setattr(router, "get_user_by_email", synchronized_read)
                responses = await asyncio.gather(
                    *[
                        client.post(
                            "/auth/reset-password",
                            json={
                                "token": concurrent_token,
                                "new_password": f"concurrent-password-{i}",
                            },
                        )
                        for i in range(2)
                    ]
                )
                assert sorted(response.status_code for response in responses) == [
                    200,
                    400,
                ]
                with psycopg.connect(dsn) as conn:
                    final_hash = conn.execute(
                        "SELECT password_hash FROM users WHERE id=%s", (uid,)
                    ).fetchone()[0]
                winner = next(
                    i
                    for i, response in enumerate(responses)
                    if response.status_code == 200
                )
                assert service.verify_password(
                    f"concurrent-password-{winner}", final_hash
                )
        finally:
            await engine.dispose()

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
