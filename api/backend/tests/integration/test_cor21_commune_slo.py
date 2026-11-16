"""Bounded COR-21 laboratory: actual query and HTTP, disposable database only.

Predeclared local SLO: 20 HTTP reads, concurrency <=4, success p95 <1s,
100ms statement timeout under contention => explicit 503 in <1s, total <30s.
Synthetic 10K-row projection is not a production capacity benchmark.
"""

import asyncio
import json
import math
import time
from unittest.mock import AsyncMock

import psycopg
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def test_commune_bounded_read_and_lock_slo(isolated_pg_dsn, client, monkeypatch):
    from app.database import get_db
    from app.main import app
    from app.middleware import RequestLoggingMiddleware

    dsn = isolated_pg_dsn
    from tests.integration.test_cor20_publication import create_chain, create_fixture

    with psycopg.connect(dsn, connect_timeout=5) as setup:
        create_fixture(setup)
        setup.execute(
            "INSERT INTO demographie SELECT 'X' || lpad(n::text,4,'0'), 'synthetic' "
            "FROM generate_series(1,9999) n"
        )
        create_chain(setup)
        setup.execute(
            "CREATE UNIQUE INDEX cor21_code ON v_commune_synthese(code_insee)"
        )
        setup.execute("ANALYZE v_commune_synthese")

    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        pool_size=4,
        max_overflow=0,
        connect_args={"server_settings": {"statement_timeout": "100"}},
    )
    sessions = async_sessionmaker(engine)
    captured = []

    @event.listens_for(engine.sync_engine, "before_execute")
    def capture_query(_conn, clause, _multiparams, params, _options):
        # The first read is the hosted scope (DATA-03); plan the commune read.
        if not captured and "instance_scope_" not in str(clause):
            captured.append((str(clause), dict(params)))

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    semaphore = asyncio.Semaphore(4)

    async def read(expected):
        async with semaphore:
            start = time.perf_counter()
            response = await asyncio.wait_for(
                client.get("/v1/commune", params={"code_insee": "44999"}),
                timeout=1,
            )
            elapsed = time.perf_counter() - start
            assert response.status_code == expected, response.text
            if expected == 503:
                assert "donnees" not in response.json()
                assert "temporairement indisponible" in response.text
            else:
                assert response.json()["donnees"][0]["code_insee"] == "44999"
            return elapsed

    start = time.perf_counter()
    try:
        successes = await asyncio.gather(*(read(200) for _ in range(15)))
        # REFRESH holds ACCESS EXCLUSIVE until transaction end. The real
        # SELECT must hit PostgreSQL's timeout while this transaction is open.
        with psycopg.connect(dsn, connect_timeout=5) as blocker:
            blocker.execute("SET LOCAL statement_timeout = '2s'")
            blocker.execute("REFRESH MATERIALIZED VIEW v_commune_synthese")
            blocked = await asyncio.gather(*(read(503) for _ in range(4)))
        successes.append(await read(200))
        duration = time.perf_counter() - start
        p95 = sorted(successes)[math.ceil(0.95 * len(successes)) - 1]
        assert p95 < 1
        assert max(blocked) < 1
        assert duration < 30
        async with sessions() as session:
            query, params = captured[0]
            plan = (
                await session.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query),
                    params,
                )
            ).scalar_one()
        print(
            "COR21_LAB="
            + json.dumps(
                {
                    "http_reads": 20,
                    "concurrency_max": 4,
                    "synthetic_rows": 10000,
                    "statement_timeout_ms": 100,
                    "success_p95_seconds": p95,
                    "blocked_seconds": blocked,
                    "success_seconds": successes,
                    "scenario_seconds": duration,
                    "actual_query_plan": plan,
                }
            )
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
