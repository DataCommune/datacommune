"""COR-06: official excerpt -> real COPY/swap -> ASGI, on disposable PostGIS."""

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.publication_readiness import require_publication
from app.commune.router import router as commune_router
from app.config import settings
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.etl.checkpoint import CheckpointManager
from app.etl.hooks import DependentMatviewHook
from app.etl.runner import PipelineRunner
from app.etl.schemas import EtlResult
from app.etl_loyers.config import OUTPUT_COLUMNS
from app.etl_loyers.pipeline import LoyersPipeline
from app.loyers.router import router as loyers_router
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.integration.publication_fixture import install_publication_journal

pytestmark = pytest.mark.destructive
FIXTURES = Path(__file__).parents[1] / "fixtures" / "loyers"
OFFICIAL = json.loads((FIXTURES / "official-2025.json").read_text(encoding="utf-8"))
NEW_COLUMNS = (
    "id_zone",
    "type_prediction",
    "nb_observations_commune",
    "nb_observations_maille",
    "r2_ajuste",
    "trimestre_reference",
    "charges_comprises",
    "meuble",
    "surface_reference_m2",
    "surface_piece_reference_m2",
)


def test_official_source_to_api_and_historical_nulls(pg_dsn, tmp_path, monkeypatch):
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", pg_dsn)
    command.upgrade(cfg, "091_dpe_source_lifecycle")
    with psycopg.connect(pg_dsn) as conn:
        # Simulate a pre-092 interrupted COPY generation, including old data.
        conn.execute(
            "CREATE TABLE _loyers_staging_flat ("
            + ",".join(f"{column} TEXT" for column in OUTPUT_COLUMNS[:12])
            + ")"
        )
        conn.execute("INSERT INTO _loyers_staging_flat (code_insee) VALUES ('old')")
        conn.execute("""
            INSERT INTO indicateurs_loyers (code_insee, type_habitat, loyer_m2, annee)
            VALUES ('99998', 'appartement', 8.25, 2023), ('99999', 'maison', 9.75, 2025)
        """)
    command.upgrade(cfg, "092_loyers_prediction_metadata")
    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute(
            f"SELECT {','.join(NEW_COLUMNS)} FROM indicateurs_loyers"
        ).fetchall()
        assert rows == [(None,) * len(NEW_COLUMNS)] * 2
        assert conn.execute(
            "SELECT annee, loyer_m2 FROM indicateurs_loyers ORDER BY annee"
        ).fetchall() == [(2023, Decimal("8.25")), (2025, Decimal("9.75"))]
    # Downgrade is exercised only before publication, on this disposable DB.
    command.downgrade(cfg, "091_dpe_source_lifecycle")
    command.upgrade(cfg, "092_loyers_prediction_metadata")
    # The publication rebuilds the commune view chain with the application
    # DDL, which reads the schema of the head (eligibilite_fixe.offres since
    # migration 106, plan 04.1-16): publish on a head schema, as production.
    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        install_publication_journal(conn)
        # Schema 092 predates the scope tables; /v1/loyers and /v1/commune
        # read the scope first (DATA-03). loyers is one of the commune sources.
        install_instance_scope(conn, COMMUNE_SOURCES, DEPARTEMENTS)
    monkeypatch.setattr(settings, "database_url", pg_dsn)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    pipeline = LoyersPipeline()
    monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: pg_dsn)
    pipeline.create_staging_table(pg_dsn)
    for kind in ("appartement", "maison", "1-2_pieces", "3+_pieces"):
        path = FIXTURES / f"{kind}.csv"
        pipeline.validate_schema(path)
        assert pipeline.stage(pipeline.transform(path)) == 2
    pipeline.swap()
    with psycopg.connect(pg_dsn) as conn:
        for row in OFFICIAL:
            source = row["values"]
            conn.execute(
                """INSERT INTO demographie
                (code_insee, nom_commune, code_departement, population, annee)
                VALUES (%s, %s, %s, 100, 2025) ON CONFLICT DO NOTHING""",
                (source["INSEE_C"], source["LIBGEO"], source["DEP"]),
            )
    DependentMatviewHook().on_run_complete(
        "loyers",
        EtlResult(
            dataset_name="loyers",
            stages=[],
            total_rows_loaded=8,
            total_duration_seconds=0,
            success=True,
        ),
    )

    async def requests():
        engine = create_async_engine(
            pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
        )
        sessions = async_sessionmaker(engine)
        app = FastAPI()
        app.include_router(loyers_router, dependencies=[Depends(require_publication)])
        app.include_router(commune_router)

        async def database():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = database
        payloads = {"loyers": {}, "communes": {}}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture"
            ) as client:
                for row in OFFICIAL:
                    source = row["values"]
                    code = source["INSEE_C"]
                    response = await client.get(
                        "/v1/loyers", params={"code_insee": code}
                    )
                    assert response.status_code == 200, response.text
                    payloads["loyers"][code] = response.json()
                    actual = next(
                        r
                        for r in response.json()["donnees"]
                        if r["type_habitat"] == row["type_habitat"]
                    )
                    assert actual["type_prediction"] == source["TYPPRED"]
                    assert actual["id_zone"] == source["id_zone"]
                    assert actual["nb_observations_commune"] == int(source["nbobs_com"])
                    assert actual["nb_observations_maille"] == int(source["nbobs_mail"])
                    assert actual["r2_ajuste"] == pytest.approx(
                        float(source["R2_adj"].replace(",", "."))
                    )
                    for field, source_field in (
                        ("loyer_m2", "loypredm2"),
                        ("loyer_m2_bas", "lwr.IPm2"),
                        ("loyer_m2_haut", "upr.IPm2"),
                    ):
                        assert actual[field] == float(
                            round(Decimal(source[source_field].replace(",", ".")), 2)
                        )
                    assert actual["trimestre_reference"] == 3
                    assert actual["charges_comprises"] is True
                    assert actual["meuble"] is False
                    if row["type_habitat"] in {"appartement", "maison"}:
                        commune = await client.get(
                            "/v1/commune", params={"code_insee": code}
                        )
                        assert commune.status_code == 200, commune.text
                        payloads["communes"][code] = commune.json()
                        assert (
                            commune.json()["donnees"][0][
                                "loyers_" + row["type_habitat"]
                            ]
                            == actual
                        )
        finally:
            await engine.dispose()
        if output := os.environ.get("COR06_API_FIXTURE_OUTPUT"):
            Path(output).write_text(
                json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    asyncio.run(requests())


def test_old_checkpoint_invalidated_only_for_mutating_run(
    pg_dsn, tmp_path, monkeypatch
):
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", pg_dsn)
    command.upgrade(cfg, "092_loyers_prediction_metadata")
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    pipeline = LoyersPipeline()
    monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("CREATE TABLE _loyers_staging_flat (code_insee TEXT)")
        conn.execute("INSERT INTO _loyers_staging_flat VALUES ('old')")
        conn.execute(
            "CREATE TABLE indicateurs_loyers_staging (LIKE indicateurs_loyers)"
        )
    checkpoint = CheckpointManager("loyers", pipeline.staging_table_name)
    # Preserve an actual pre-v2 checkpoint byte-for-byte in dry-run mode.
    checkpoint.path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.path.write_bytes(
        json.dumps(
            {
                "dataset_name": "loyers",
                "staging_table": pipeline.staging_table_name,
                "scope_key": "",
                "loaded_files": ["appartement.csv"],
            }
        ).encode()
    )
    saved = checkpoint.path.read_bytes()
    monkeypatch.setattr(pipeline, "download", lambda: [FIXTURES / "appartement.csv"])
    runner = PipelineRunner()
    assert runner.run(pipeline, dry_run=True).success
    assert checkpoint.path.read_bytes() == saved
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute("SELECT * FROM _loyers_staging_flat").fetchall() == [
            ("old",)
        ]

    # Stop after an actual COPY into the fresh generation. No publication or
    # national row/coverage gate is bypassed to manufacture an end-to-end run.
    def inspect_fresh_generation():
        assert not checkpoint.exists()
        assert pipeline.stage(pipeline.transform(FIXTURES / "appartement.csv")) == 2
        with psycopg.connect(pg_dsn) as conn:
            assert conn.execute(
                "SELECT count(*) FROM _loyers_staging_flat"
            ).fetchone() == (2,)
        raise RuntimeError("intentional interruption after fresh COPY")

    monkeypatch.setattr(pipeline, "download", inspect_fresh_generation)
    monkeypatch.setattr(runner, "_check_preflight_disk", lambda *args: None)
    result = runner.run(pipeline)
    assert not result.success
    assert "intentional interruption after fresh COPY" in result.error
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute(
            "SELECT to_regclass('_loyers_staging_flat')"
        ).fetchone() == (None,)
