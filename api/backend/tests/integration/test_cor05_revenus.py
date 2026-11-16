"""Frozen official CSV -> real ETL publication -> real ASGI revenue endpoint.

Only the disposable wrapper DSN is accepted. The isolated schema runs the
actual revenue migration; auth/middleware and GPS are outside this boundary.
"""

import asyncio
import csv
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import Depends, FastAPI
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.common.publication_readiness import require_publication
from app.commune.dependencies import COMMUNE_EDGES, COMMUNE_SOURCES
from app.commune.router import router as commune_router
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.etl_revenus import pipeline as module
from app.revenus.router import router
from tests.integration.instance_scope_fixture import install_instance_scope
from tests.integration.publication_fixture import install_publication_journal

pytestmark = pytest.mark.destructive
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cor05_revenus"


def source_rows(name):
    with (FIXTURES / name).open(encoding="utf-8-sig", newline="") as stream:
        return {row["CODGEO"]: row for row in csv.DictReader(stream, delimiter=";")}


def numeric(value):
    return None if value == "s" else float(value.replace(",", "."))


def test_official_import_api_and_payload(pg_dsn, monkeypatch, tmp_path):  # noqa: C901
    schema = "cor05_" + uuid4().hex
    connect = psycopg.connect
    options = f"-c search_path={schema} -c statement_timeout=10000"
    with connect(pg_dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with connect(pg_dsn, options=options) as conn:
            migration_path = next(
                (Path(__file__).resolve().parents[2] / "migrations/versions").glob(
                    "029_*.py"
                )
            )
            spec = importlib.util.spec_from_file_location(
                "cor05_migration", migration_path
            )
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.op = SimpleNamespace(execute=conn.execute)
            migration.upgrade()
            conn.execute(
                "CREATE TABLE etl_old_tables (table_name text PRIMARY KEY, "
                "dataset_name text, created_at timestamptz DEFAULT now())"
            )
        monkeypatch.setattr(
            psycopg, "connect", lambda dsn, **kw: connect(dsn, options=options, **kw)
        )
        pipeline = module.RevenusPipeline()
        monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: pg_dsn)
        monkeypatch.setattr(module, "get_download_dir", lambda _: tmp_path)
        source = FIXTURES / module.MAIN_CSV_NAME
        pipeline.validate_schema(source)
        transformed = pipeline.transform(source)
        assert pipeline.stage(transformed) == 9
        pipeline.swap()
        with connect(pg_dsn, options=options) as conn:
            assert conn.execute("SELECT count(*) FROM revenus").fetchone()[0] == 9
            assert (
                conn.execute("SELECT count(*) FROM etl_old_tables").fetchone()[0] == 1
            )
            # Minimal SQL projection, NOT the full production synthesis rebuild.
            # Real dependency guard remains enabled; DPE content is out of scope.
            conn.execute(
                "CREATE TABLE matview_metadata (matview_name text, "
                "last_refresh_status text)"
            )
            install_publication_journal(conn)
            # /v1/revenus and /v1/commune read the scope first (DATA-03).
            install_instance_scope(conn, COMMUNE_SOURCES, DEPARTEMENTS)
            for dataset, table in COMMUNE_SOURCES.items():
                if dataset != "revenus":
                    conn.execute(
                        sql.SQL("CREATE TABLE {} (code_insee text)").format(
                            sql.Identifier(table)
                        )
                    )
            intermediate = [
                (view, source)
                for view, source in COMMUNE_EDGES
                if view != "v_commune_synthese"
            ]
            for view, source in intermediate:
                conn.execute(
                    sql.SQL(
                        "CREATE MATERIALIZED VIEW {} AS SELECT code_insee FROM {}"
                    ).format(sql.Identifier(view), sql.Identifier(source))
                )
            dependencies = [
                source
                for view, source in COMMUNE_EDGES
                if view == "v_commune_synthese" and source != "revenus"
            ]
            joins = " ".join(
                f"LEFT JOIN {source} x{i} USING (code_insee)"
                for i, source in enumerate(dependencies)
            )
            conn.execute(
                """CREATE MATERIALIZED VIEW v_commune_synthese AS
                SELECT r.code_insee, r.nom_commune, r.code_departement,
                r.revenu_median, r.indice_gini, r.part_imposee,
                r.taux_pauvrete, r.annee AS annee_revenus
                FROM revenus r """
                + joins
            )

        async def request():
            engine = create_async_engine(
                pg_dsn.replace("postgresql://", "postgresql+asyncpg://"),
                connect_args={
                    "server_settings": {
                        "search_path": schema,
                        "statement_timeout": "10000",
                    }
                },
            )

            async def db():
                async with AsyncSession(engine) as session:
                    yield session

            app = FastAPI()
            app.include_router(router, dependencies=[Depends(require_publication)])
            app.include_router(commune_router)
            app.dependency_overrides[get_db] = db
            responses = {}
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://local.test"
                ) as client:
                    for code in source_rows(module.MAIN_CSV_NAME):
                        response = await client.get(
                            "/v1/revenus", params={"code_insee": code}
                        )
                        assert response.status_code == 200
                        responses[code] = response.json()
                    commune_response = await client.get(
                        "/v1/commune", params={"code_insee": "44109"}
                    )
                    assert commune_response.status_code == 200
                    responses["commune"] = commune_response.json()
            finally:
                await engine.dispose()
            return responses

        responses = asyncio.run(request())
        declared = source_rows(module.MAIN_CSV_NAME)
        poverty = source_rows(module.POVERTY_CSV_NAME)
        available = source_rows("FILO2021_DISP_COM.csv")
        for code, response in responses.items():
            if code == "commune":
                continue
            row = next(row for row in response["donnees"] if row["code_insee"] == code)
            assert row["annee"] == 2021
            for field, column in {
                "revenu_median": "Q221",
                "revenu_q1": "Q121",
                "revenu_q3": "Q321",
                "revenu_d1": "D121",
                "revenu_d9": "D921",
                "indice_gini": "GI21",
                "part_imposee": "PMIMP21",
                "nb_menages_fiscaux": "NBMEN21",
            }.items():
                assert row[field] == numeric(declared[code][column]), (code, field)
            assert row["taux_pauvrete"] == numeric(poverty[code]["TP6021"])
            if code != "01019":
                assert row["revenu_median"] != numeric(available[code]["Q221"])
        # Parent + arrondissement stay distinguishable, never summed or averaged.
        assert {r["code_insee"] for r in responses["75056"]["donnees"]} == {
            "75056",
            "75101",
        }
        assert len(responses["75101"]["donnees"]) == 1
        assert responses["01019"]["donnees"][0]["revenu_median"] is None
        commune = responses["commune"]["donnees"][0]
        assert commune["revenu_median"] == 24380
        assert commune["indice_gini"] == 0.412
        assert commune["taux_pauvrete"] == 17.0
        assert commune["annee_revenus"] == 2021
        if output := os.environ.get("COR05_API_FIXTURE_OUTPUT"):
            Path(output).write_text(
                json.dumps(
                    {
                        "boundary": (
                            "Real /v1/revenus and /v1/commune ASGI after "
                            "official frozen "
                            "CSV import. Commune uses minimal SQL projection, not full "
                            "production synthesis rebuild. "
                            "Real dependency guard enabled;"
                            " DPE content, authentication and GPS excluded."
                        ),
                        "payload": responses.pop("commune"),
                        "oracle": {
                            "code_insee": "44109",
                            "revenu_median": 24380,
                            "indice_gini": 0.412,
                            "taux_pauvrete": 17.0,
                            "annee_revenus": 2021,
                            "benchmark_dec_metropole": 22900,
                            "counterexample_disp_metropole": 23080,
                        },
                        "responses": responses,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    finally:
        with connect(pg_dsn) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
