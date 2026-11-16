"""/v1/datasets on a migrated database: seeded statuses are normalized (03-11).

The migrations seed ``dataset_metadata`` with ``'unknown'`` (004, 018),
``'disponible'`` without any import (039) and ``'proxy'`` (039). « Données &
méthode » words a closed vocabulary only, and never says « imported » for a
dataset no successful run has loaded. Only a success in ``etl_runs`` makes a
dataset ``disponible``.
"""

from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.exceptions import register_exception_handlers
from app.common.source_catalog import DATASET_CATALOG
from app.config import settings
from app.database import get_db
from app.datasets.router import router as datasets_router
from app.scope import SCOPE_EXCLUDED_KEYS

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
SERVED_STATUSES = {
    "disponible",
    "non_importe",
    "proxy_amont",
    "hors_couverture",
    "quarantined",
}
HOSTED = sorted(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    return isolated_pg_dsn


@pytest.fixture
async def api(migrated, instance_scope):
    instance_scope(migrated, HOSTED, ["44", "85"])
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(datasets_router)
    app.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c
    finally:
        await engine.dispose()


async def _served(api) -> dict[str, dict]:
    response = await api.get("/v1/datasets")
    assert response.status_code == 200, response.text
    return {entry["nom"]: entry for entry in response.json()["donnees"]}


async def test_seeded_rows_are_served_in_the_closed_vocabulary(api, migrated):
    with psycopg.connect(migrated) as conn:
        seeded = dict(conn.execute("SELECT dataset_name, status FROM dataset_metadata"))
    # The migrations do seed the statuses this test is about.
    assert {"unknown", "proxy", "disponible"} <= set(seeded.values())

    served = await _served(api)
    statuts = {entry["statut"] for entry in served.values()}
    assert statuts <= SERVED_STATUSES, statuts
    # No successful run on a freshly migrated base: nothing is « imported ».
    assert not [nom for nom, entry in served.items() if entry["statut"] == "disponible"]
    for nom in ("bdnb", "sirene", "georisques", "errial", "urbanisme", "rnb"):
        assert served[nom]["statut"] == "proxy_amont", nom
    for nom in ("ban", "dvf", "fiscalite", "vacance"):
        assert served[nom]["statut"] == "non_importe", nom


async def test_a_successful_run_makes_a_dataset_available(api, migrated):
    with psycopg.connect(migrated) as conn:
        conn.execute(
            "UPDATE dataset_metadata SET status = 'disponible', "
            "last_updated_at = NOW() WHERE dataset_name = 'dvf'"
        )
        conn.execute(
            "INSERT INTO etl_runs (dataset_name, status, started_at, completed_at) "
            "VALUES ('dvf', 'success', NOW() - INTERVAL '1 hour', NOW())"
        )
    try:
        served = await _served(api)
        assert served["dvf"]["statut"] == "disponible"
        assert served["ban"]["statut"] == "non_importe"
    finally:
        with psycopg.connect(migrated) as conn:
            conn.execute("DELETE FROM etl_runs WHERE dataset_name = 'dvf'")
