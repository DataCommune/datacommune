"""L09: latest exact-code observations, including NULL/zero, on disposable SQL."""

from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.common.publication_readiness import require_publication
from app.config import settings
from app.database import get_db
from app.demographie.queries import demographie_par_codes
from app.demographie.router import router as demographie_router
from app.etl.base import DEPARTEMENTS
from app.fiscalite.queries import fiscalite_par_codes
from app.fiscalite.router import router as fiscalite_router
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive
DATASETS = ("demographie", "fiscalite")
# Synthetic observations, not new official-source or national coverage claims.
# The unrequested 44992 establishes a newer global year than all queried codes.
HISTORY = [
    ("44991", 2021, 10, 5),
    ("44991", 2023, None, 0),
    ("85991", 2022, 9, 8),
    ("85991", 2024, 0, None),
    ("44992", 2026, 99, 99),
    ("75056", 2020, 30, 4),
    ("75101", 2021, 20, 7),
    ("75101", 2025, None, None),
    ("75102", 2022, 0, 0),
    ("13055", 2019, 40, 6),
    ("13201", 2023, 41, 7),
    ("69123", 2020, 50, 8),
    ("69381", 2024, 0, 0),
]
# Explicit oracle: no invocation of the implementation to derive expectations.
EXPECTED = {
    "13055": ("13055", 2019, 40, 6),
    "13201": ("13201", 2023, 41, 7),
    "44991": ("44991", 2023, None, 0),
    "69123": ("69123", 2020, 50, 8),
    "69381": ("69381", 2024, 0, 0),
    "75056": ("75056", 2020, 30, 4),
    "75101": ("75101", 2025, None, None),
    "75102": ("75102", 2022, 0, 0),
    "85991": ("85991", 2024, 0, None),
}
HTTP_CODES = {
    **{code: [code] for code in EXPECTED},
    "75056": ["75056", "75101", "75102"],
    "13055": ["13055", "13201"],
    "69123": ["69123", "69381"],
}


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    # The routes read the scope first (DATA-03): host the datasets exercised.
    install_scope_at(isolated_pg_dsn, ("demographie", "fiscalite"), DEPARTEMENTS)
    return isolated_pg_dsn


@pytest.fixture
def database(migrated):
    with psycopg.connect(migrated) as conn:
        conn.execute("TRUNCATE demographie, fiscalite_locale")
    return migrated


def seed(dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO demographie(code_insee,annee,population,nb_logements) "
            "VALUES(%s,%s,%s,%s)",
            HISTORY,
        )
        cursor.executemany(
            "INSERT INTO fiscalite_locale(code_insee,annee,taux_tfpb,taux_teom) "
            "VALUES(%s,%s,%s,%s)",
            HISTORY,
        )


def read_engine(dsn):
    return create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "1000",
            }
        },
    )


def project(dataset, rows):
    fields = (
        ("population", "nb_logements")
        if dataset == "demographie"
        else ("taux_tfpb", "taux_teom")
    )
    return [(r["code_insee"], r["annee"], r[fields[0]], r[fields[1]]) for r in rows]


async def query(dsn, dataset, codes):
    engine = read_engine(dsn)
    try:
        async with AsyncSession(engine) as session:
            assert (
                await session.execute(text("SHOW transaction_read_only"))
            ).scalar_one() == "on"
            return await {
                "demographie": demographie_par_codes,
                "fiscalite": fiscalite_par_codes,
            }[dataset](session, codes)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dataset", DATASETS)
async def test_latest_per_exact_code_preserves_unknown_zero_and_stable_order(
    database, dataset
):
    seed(database)
    codes = [*reversed(EXPECTED), "44991", "00000"]
    expected = list(EXPECTED.values())
    assert project(dataset, await query(database, dataset, codes)) == expected
    assert project(dataset, await query(database, dataset, codes[::-1])) == expected
    # Direct SQL lookup must never expand an arrondissement into its parent.
    assert project(dataset, await query(database, dataset, ["75101"])) == [
        EXPECTED["75101"]
    ]


@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("has_rows", [False, True])
async def test_empty_table_or_unmatched_codes_stays_empty(database, dataset, has_rows):
    if has_rows:
        seed(database)
    assert await query(database, dataset, ["00000"]) == []
    assert await query(database, dataset, []) == []


@pytest.fixture
async def http_client(database):
    engine = read_engine(database)

    async def db():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()
    for router in (demographie_router, fiscalite_router):
        app.include_router(router, dependencies=[Depends(require_publication)])
    app.dependency_overrides[get_db] = db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            yield client
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("code", HTTP_CODES)
async def test_http_preserves_row_year_and_exact_plm_identities(
    database, http_client, dataset, code
):
    seed(database)
    response = await http_client.get("/v1/" + dataset, params={"code_insee": code})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert project(dataset, payload["donnees"]) == [
        EXPECTED[c] for c in HTTP_CODES[code]
    ]
    assert "meta" in payload
    for row in payload["donnees"]:
        assert type(row["annee"]) is int
        assert type(row["code_insee"]) is str


@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("has_rows", [False, True])
async def test_http_empty_or_missing_code_is_an_honest_empty(
    database, http_client, dataset, has_rows
):
    if has_rows:
        seed(database)
    response = await http_client.get("/v1/" + dataset, params={"code_insee": "00000"})
    assert response.status_code == 200
    assert response.json()["donnees"] == []


@pytest.mark.parametrize("dataset", DATASETS)
async def test_location_is_required_without_invented_coordinates(http_client, dataset):
    assert (await http_client.get("/v1/" + dataset)).status_code == 422


@pytest.mark.parametrize("table", ["demographie", "fiscalite_locale"])
def test_existing_unique_keys_prevent_ambiguous_latest_year(database, table):
    seed(database)
    with (
        psycopg.connect(database) as conn,
        pytest.raises(psycopg.errors.UniqueViolation),
    ):
        conn.execute(f"INSERT INTO {table}(code_insee,annee) VALUES('44991',2023)")
