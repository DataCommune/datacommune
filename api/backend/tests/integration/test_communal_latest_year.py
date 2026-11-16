"""Latest local observation, including unknown values, on migrated disposable DB."""

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
from app.etl.base import DEPARTEMENTS
from app.loyers.queries import loyers_par_codes
from app.loyers.router import router as loyers_router
from app.revenus.queries import revenus_par_codes
from app.revenus.router import router as revenus_router
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive

REVENUS = [
    ("44991", 2021, 10000),
    ("44991", 2023, None),
    ("85991", 2022, 20000),
    ("44992", 2025, 30000),
    ("75056", 2020, 31000),
    ("75101", 2024, 32000),
]
LOYERS = [
    ("44991", "appartement", 2021, 10),
    ("44991", "appartement", 2024, None),
    ("44991", "maison", 2022, 15),
    ("85991", "appartement", 2023, 11),
    ("85991", "maison", 2025, 16),
    ("44992", "maison", 2026, 99),
    ("75056", "appartement", 2020, 25),
    ("75101", "appartement", 2024, 30),
    ("75101", "maison", 2021, 35),
]


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    with pytest.MonkeyPatch.context() as context:
        # env.py reads settings directly; configure both inputs to the CHILD.
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    # The routes read the scope first (DATA-03): host the datasets exercised.
    install_scope_at(isolated_pg_dsn, ("loyers", "revenus"), DEPARTEMENTS)
    return isolated_pg_dsn


@pytest.fixture
def database(migrated):
    with psycopg.connect(migrated) as conn:
        conn.execute("TRUNCATE revenus,indicateurs_loyers")
    return migrated


def seed(dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO revenus(code_insee,annee,revenu_median) VALUES(%s,%s,%s)",
            REVENUS,
        )
        cursor.executemany(
            "INSERT INTO indicateurs_loyers(code_insee,type_habitat,annee,loyer_m2) "
            "VALUES(%s,%s,%s,%s)",
            LOYERS,
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


def projection(dataset, rows):
    if dataset == "revenus":
        return [(row["code_insee"], row["annee"], row["revenu_median"]) for row in rows]
    return [
        (row["code_insee"], row["type_habitat"], row["annee"], row["loyer_m2"])
        for row in rows
    ]


async def query(dsn, dataset, codes):
    engine = read_engine(dsn)
    try:
        async with AsyncSession(engine) as session:
            assert (
                await session.execute(text("SHOW transaction_read_only"))
            ).scalar_one() == "on"
            return await {"revenus": revenus_par_codes, "loyers": loyers_par_codes}[
                dataset
            ](session, codes)
    finally:
        await engine.dispose()


async def test_revenus_latest_per_code_keeps_null_and_plm_identity(database):
    seed(database)
    codes = ["85991", "75101", "75056", "44991", "44991"]
    expected = [
        ("44991", 2023, None),
        ("75056", 2020, 31000),
        ("75101", 2024, 32000),
        ("85991", 2022, 20000),
    ]
    assert projection("revenus", await query(database, "revenus", codes)) == expected
    assert (
        projection("revenus", await query(database, "revenus", codes[::-1])) == expected
    )


async def test_loyers_latest_per_code_and_type_keeps_null_and_stable_order(database):
    seed(database)
    codes = ["85991", "75101", "75056", "44991", "44991"]
    expected = [
        ("44991", "appartement", 2024, None),
        ("75056", "appartement", 2020, 25),
        ("75101", "appartement", 2024, 30),
        ("85991", "appartement", 2023, 11),
        ("44991", "maison", 2022, 15),
        ("75101", "maison", 2021, 35),
        ("85991", "maison", 2025, 16),
    ]
    assert projection("loyers", await query(database, "loyers", codes)) == expected
    assert (
        projection("loyers", await query(database, "loyers", codes[::-1])) == expected
    )


@pytest.mark.parametrize("dataset", ["revenus", "loyers"])
async def test_truly_empty_table_is_empty(database, dataset):
    assert await query(database, dataset, ["44991", "75056"]) == []


@pytest.mark.parametrize("dataset", ["revenus", "loyers"])
@pytest.mark.parametrize("codes", [[], ["00000"]])
async def test_no_matching_codes_is_empty(database, dataset, codes):
    seed(database)
    assert await query(database, dataset, codes) == []


@pytest.fixture
async def http_client(database):
    engine = read_engine(database)

    async def db():
        async with AsyncSession(engine) as session:
            yield session

    miniapp = FastAPI()
    miniapp.include_router(revenus_router, dependencies=[Depends(require_publication)])
    miniapp.include_router(loyers_router, dependencies=[Depends(require_publication)])
    miniapp.dependency_overrides[get_db] = db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=miniapp), base_url="http://local.test"
        ) as client:
            yield client
    finally:
        await engine.dispose()


@pytest.mark.parametrize("dataset", ["revenus", "loyers"])
@pytest.mark.parametrize("code", ["44991", "85991", "75056", "75101"])
async def test_http_preserves_each_line_year_and_parent_arrondissement(
    database, http_client, dataset, code
):
    seed(database)
    response = await http_client.get("/v1/" + dataset, params={"code_insee": code})
    assert response.status_code == 200
    selected_codes = {code, "75101"} if code == "75056" else {code}
    source = REVENUS if dataset == "revenus" else LOYERS
    latest = {}
    for row in source:
        key = row[0] if dataset == "revenus" else row[:2]
        year = row[1] if dataset == "revenus" else row[2]
        if row[0] in selected_codes and (key not in latest or year > latest[key][-2]):
            latest[key] = row
    order = (
        (lambda row: row[0]) if dataset == "revenus" else (lambda row: (row[1], row[0]))
    )
    expected = sorted(latest.values(), key=order)
    assert projection(dataset, response.json()["donnees"]) == expected
    assert "meta" in response.json()


@pytest.mark.parametrize("dataset", ["revenus", "loyers"])
async def test_http_without_location_is_422_without_invented_gps(http_client, dataset):
    response = await http_client.get("/v1/" + dataset)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "table,columns,values",
    [
        ("revenus", "code_insee,annee", "'44991',2023"),
        (
            "indicateurs_loyers",
            "code_insee,type_habitat,annee",
            "'44991','appartement',2024",
        ),
    ],
)
def test_existing_unique_keys_prevent_tied_latest_observations(
    database, table, columns, values
):
    seed(database)
    with (
        psycopg.connect(database) as conn,
        pytest.raises(psycopg.errors.UniqueViolation),
    ):
        conn.execute(f"INSERT INTO {table}({columns}) VALUES({values})")


@pytest.mark.parametrize("dataset", ["revenus", "loyers"])
@pytest.mark.parametrize("has_other_rows", [False, True])
async def test_http_empty_table_or_unknown_code_stays_empty(
    database,
    http_client,
    dataset,
    has_other_rows,
):
    if has_other_rows:
        seed(database)
    response = await http_client.get("/v1/" + dataset, params={"code_insee": "00000"})
    assert response.status_code == 200
    assert response.json()["donnees"] == []
