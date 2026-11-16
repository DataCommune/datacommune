"""Frozen LOVAC -> real publication -> ASGI; disposable wrapper database only."""

import asyncio
import csv
import hashlib
import importlib.util
import json
import os
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.etl_vacance import pipeline as module
from app.vacance.router import router
from tests.integration.instance_scope_fixture import install_instance_scope

pytestmark = pytest.mark.destructive
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/cor08_lovac"
RATES = ("taux_vacance", "taux_vacance_moins_2ans", "taux_vacance_plus_2ans")


@pytest.fixture
def database(pg_dsn, monkeypatch):
    schema = "cor08_" + uuid4().hex
    connect = psycopg.connect
    options = f"-c search_path={schema} -c statement_timeout=10000"
    with connect(pg_dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with connect(pg_dsn, options=options) as conn:
            path = (
                Path(__file__).resolve().parents[2]
                / "migrations/versions/035_vacance.py"
            )
            spec = importlib.util.spec_from_file_location("cor08_migration", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.op = SimpleNamespace(execute=conn.execute)
            migration.upgrade()
            conn.execute(
                "CREATE TABLE etl_old_tables (table_name text PRIMARY KEY, "
                "dataset_name text, created_at timestamptz DEFAULT now())"
            )
            # /v1/vacance reads the scope first (DATA-03).
            install_instance_scope(conn, ["vacance"], DEPARTEMENTS)
        monkeypatch.setattr(
            psycopg, "connect", lambda dsn, **kw: connect(dsn, options=options, **kw)
        )
        yield pg_dsn, schema
    finally:
        with connect(pg_dsn) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def request(database, codes):
    async def run():
        dsn, schema = database
        engine = create_async_engine(
            dsn.replace("postgresql://", "postgresql+asyncpg://"),
            connect_args={
                "server_settings": {"search_path": schema, "statement_timeout": "10000"}
            },
        )

        async def db():
            async with AsyncSession(engine) as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = db
        result = {}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://local.test"
            ) as client:
                for code in codes:
                    response = await client.get(
                        "/v1/vacance", params={"code_insee": code}
                    )
                    assert response.status_code == 200
                    result[code] = response.json()
        finally:
            await engine.dispose()
        return result

    return asyncio.run(run())


def test_official_source_import_and_real_api(database, monkeypatch, tmp_path):
    source = FIXTURES / "official-communes26.csv"
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == manifest["fixture_sha256"]
    with source.open(encoding="latin-1", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    pipeline = module.VacancePipeline()
    monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: database[0])
    monkeypatch.setattr(module, "get_download_dir", lambda _: tmp_path)

    async def download_source():
        return [source]

    monkeypatch.setattr(pipeline, "_async_download", download_source)
    (source,) = pipeline.download()
    pipeline.validate_schema(source)
    transformed = pipeline.transform(source)
    assert pipeline.stage(transformed) == 5
    pipeline.swap()
    responses = request(database, [row["CODGEO_26"] for row in rows])
    for source_row in rows:
        row = responses[source_row["CODGEO_26"]]["donnees"][0]

        def number(key, source_row=source_row):
            value = source_row[key]
            return None if value == "s" else int(value)

        total, vacant, long = (
            number(key)
            for key in ("ff_pp_total_25", "pp_vacant_26", "pp_vacant_plus_2ans_26")
        )
        short = vacant - long if vacant is not None and long is not None else None
        assert row["nb_logements_prives"] == total
        assert row["nb_logements_vacants"] == vacant
        assert row["nb_vacants_moins_2ans"] == short
        assert row["nb_vacants_plus_2ans"] == long
        assert row["annee"] == 2026
        for rate, numerator in zip(RATES, [vacant, short, long], strict=True):
            expected = (
                None
                if total is None or total <= 0 or numerator is None or numerator < 0
                else float(
                    (Decimal(numerator) * 100 / total).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_EVEN
                    )
                )
            )
            assert row[rate] == expected
    assert responses["02343"]["donnees"][0]["taux_vacance"] == 0
    assert responses["01009"]["donnees"][0]["nb_logements_vacants"] == 28
    assert all(responses["01009"]["donnees"][0][rate] is None for rate in RATES)
    with psycopg.connect(database[0]) as conn:
        assert conn.execute("SELECT count(*) FROM vacance").fetchone()[0] == 5
        assert (
            conn.execute(
                "SELECT count(*) FROM vacance WHERE nb_logements_prives IS NULL "
                "AND taux_vacance IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
    if output := os.environ.get("COR08_API_FIXTURE_OUTPUT"):
        Path(output).write_text(
            json.dumps(
                {
                    "boundary": (
                        "Actual /v1/vacance ASGI after frozen official CSV "
                        "transform/COPY/swap, migration035. No commune synthesis, "
                        "runner, auth or GPS tested."
                    ),
                    "source_sha256": manifest["raw_sha256"],
                    "responses": responses,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_legacy_stock_rates_need_known_inputs_and_keep_measured_zero(database):
    # Persisted values deliberately reproduce the historical pre-fix zero.
    cases = [(None, 28), (0, 0), (-5, 2), (100, None), (100, -1), (100, 0), (100, 12)]
    with psycopg.connect(database[0]) as conn:
        for index, (total, numerator) in enumerate(cases):
            conn.execute(
                "INSERT INTO vacance(code_commune, nb_logements_prives, "
                "nb_logements_vacants, nb_vacants_moins_2ans, nb_vacants_plus_2ans, "
                "taux_vacance, taux_vacance_moins_2ans, taux_vacance_plus_2ans, "
                "annee) VALUES (%s,%s,%s,%s,%s,0,0,0,2026)",
                (f"449{index:02}", total, numerator, numerator, numerator),
            )
    responses = request(database, [f"449{i:02}" for i in range(len(cases))])
    for index, (total, numerator) in enumerate(cases):
        row = responses[f"449{index:02}"]["donnees"][0]
        expected = (
            0
            if total is not None
            and total > 0
            and numerator is not None
            and numerator >= 0
            else None
        )
        assert [row[rate] for rate in RATES] == [expected] * 3
    with psycopg.connect(database[0]) as conn:
        # Read protection must not mutate the persisted stock.
        assert conn.execute(
            "SELECT count(*) FROM vacance WHERE taux_vacance = 0"
        ).fetchone()[0] == len(cases)


def test_latest_observation_is_selected_per_commune(database):
    with psycopg.connect(database[0]) as conn:
        conn.execute(
            "INSERT INTO vacance(code_commune,nb_logements_prives,"
            "nb_logements_vacants,taux_vacance,annee) VALUES "
            "('44991',100,0,0,2025),('44991',100,12,12,2026),"
            "('44992',100,0,0,2025)"
        )
    responses = request(database, ["44991", "44992"])
    assert responses["44991"]["donnees"][0]["annee"] == 2026
    assert responses["44992"]["donnees"][0]["annee"] == 2025
    assert responses["44992"]["donnees"][0]["taux_vacance"] == 0


def test_explicit_synthetic_missing_and_negative_inputs_import_as_unknown(
    database, monkeypatch, tmp_path
):
    # Explicit synthetic mutations; no empty denominator in the official extract.
    with (FIXTURES / "official-communes26.csv").open(
        encoding="latin-1", newline=""
    ) as stream:
        template = next(csv.DictReader(stream, delimiter=";"))
    cases = [
        ("", "12", "4", [None, None, None]),
        ("0", "0", "0", [None, None, None]),
        ("-5", "12", "4", [None, None, None]),
        ("100", "-1", "0", [None, None, 0]),
        ("100", "2", "5", [2, None, 5]),
        ("100", "2", "-5", [2, None, None]),
        ("100", "", "", [None, None, None]),
    ]
    source = tmp_path / "synthetic-source.csv"
    with source.open("w", encoding="latin-1", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=template, delimiter=";")
        writer.writeheader()
        for index, (total, vacant, long, _) in enumerate(cases):
            writer.writerow(
                dict(
                    template,
                    CODGEO_26=f"449{index:02}",
                    ff_pp_total_25=total,
                    pp_vacant_26=vacant,
                    pp_vacant_plus_2ans_26=long,
                )
            )
    pipeline = module.VacancePipeline()
    monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: database[0])
    monkeypatch.setattr(module, "get_download_dir", lambda _: tmp_path)

    async def download_source():
        return [source]

    monkeypatch.setattr(pipeline, "_async_download", download_source)
    (source,) = pipeline.download()
    pipeline.validate_schema(source)
    assert pipeline.stage(pipeline.transform(source)) == len(cases)
    pipeline.swap()
    responses = request(database, [f"449{i:02}" for i in range(len(cases))])
    for index, (_, _, _, expected) in enumerate(cases):
        row = responses[f"449{index:02}"]["donnees"][0]
        assert [row[rate] for rate in RATES] == expected
