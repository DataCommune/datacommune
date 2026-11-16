"""L09: committed source corruptions through full Commune DDL and real HTTP.

All writes use the wrapper-owned isolated child. Frozen sources and the closed
132-cell L08 oracle are reused without changing application or old evidence.
"""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.commune.matview_ddl import recreate_commune_synthese
from app.commune.router import router as commune_router
from app.config import settings
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_revenus import pipeline as income_module
from app.etl_vacance import pipeline as vacancy_module
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.oracles import communal_sources as source
from tests.oracles import commune_projection as oracle

pytestmark = pytest.mark.destructive
TABLES = {"revenus": "revenus", "loyers": "indicateurs_loyers", "vacance": "vacance"}
# Each mutation names its expected, actually observable HTTP failure class.
MUTATIONS = [
    ("revenus:44109", {"revenu_median": 24170}, "wrong DISP series", "value_mismatch"),
    (
        "revenus:44109",
        {"revenu_median": 2032},
        "annual to monthly unit",
        "value_mismatch",
    ),
    ("revenus:44109", {"annee": 2020}, "older vintage", "value_mismatch"),
    (
        "revenus:01019",
        {"revenu_median": 0},
        "secret replaced by zero",
        "value_mismatch",
    ),
    (
        "revenus:44109",
        {"code_insee": "99990"},
        "source identity misassigned",
        "value_mismatch",
    ),
    (
        "loyers:05066:appartement",
        {"loyer_m2": 117.12},
        "monthly to annual unit",
        "value_mismatch",
    ),
    ("loyers:05066:appartement", {"annee": 2024}, "older vintage", "value_mismatch"),
    (
        "loyers:05066:appartement",
        {"type_prediction": "commune"},
        "maille labelled commune",
        "value_mismatch",
    ),
    (
        "loyers:05066:appartement",
        {"id_zone": "WRONG"},
        "wrong prediction zone",
        "value_mismatch",
    ),
    (
        "loyers:05066:appartement",
        {"nb_observations_commune": 484},
        "maille count substituted",
        "value_mismatch",
    ),
    (
        "loyers:05066:appartement",
        {"charges_comprises": False},
        "charges definition reversed",
        "value_mismatch",
    ),
    (
        "loyers:05066:appartement",
        {"r2_ajuste": None},
        "known metadata erased",
        "value_mismatch",
    ),
    (
        "loyers:05066:appartement",
        {"code_insee": "99990"},
        "source identity misassigned",
        "branch_absent",
    ),
    (
        "vacance:44109",
        {"taux_vacance": 0.1},
        "percentage to ratio unit",
        "value_mismatch",
    ),
    (
        "vacance:44109",
        {"annee": 2025},
        "denominator vintage substituted",
        "value_mismatch",
    ),
    ("vacance:02343", {"taux_vacance": None}, "measured zero erased", "value_mismatch"),
    (
        "vacance:01009",
        {"nb_logements_prives": 100, "taux_vacance": 28},
        "secret denominator and rate fabricated",
        "value_mismatch",
    ),
    (
        "vacance:01009",
        {"nb_vacants_plus_2ans": 0},
        "known long vacancy erased",
        "value_mismatch",
    ),
    (
        "vacance:44109",
        {"code_commune": "99990"},
        "source identity misassigned",
        "value_mismatch",
    ),
]


def _stock(dsn, case):
    with psycopg.connect(dsn) as conn:
        record = conn.execute(
            sql.SQL("SELECT to_jsonb(t) FROM {} t WHERE {}").format(
                sql.Identifier(TABLES[case["dataset"]]),
                sql.SQL(" AND ").join(
                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in case["key"]
                ),
            ),
            tuple(case["key"].values()),
        ).fetchone()
        assert record is not None
        return record[0]


def _write(dsn, case, selector, values):
    # Select by the current natural key, including the deliberately moved code.
    with psycopg.connect(dsn) as conn:
        cursor = conn.execute(
            sql.SQL("UPDATE {} SET {} WHERE {}").format(
                sql.Identifier(TABLES[case["dataset"]]),
                sql.SQL(",").join(
                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in values
                ),
                sql.SQL(" AND ").join(
                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in selector
                ),
            ),
            (*values.values(), *selector.values()),
        )
        assert cursor.rowcount == 1
        recreate_commune_synthese(conn)


async def _exercise(dsn, cases):
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))

    async def database():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(commune_router)
    app.dependency_overrides[get_db] = database
    result = {"positive": [], "sql_mutations": [], "availability": []}

    async def request(client, case):
        return await client.get(
            "/v1/commune", params={"code_insee": next(iter(case["key"].values()))}
        )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            indexed = {case["id"]: case for case in cases}
            projected = [case for case in cases if oracle.expected_cells(case)]
            for case in projected:
                response = await request(client, case)
                count = oracle.assert_response(
                    case, response.status_code, response.json()
                )
                result["positive"].append(
                    {
                        "case": case["id"],
                        "cells": oracle.expected_cells(case),
                        "count": count,
                        "payload": response.json(),
                    }
                )
            for case_id, changes, reason, expected_kind in MUTATIONS:
                case = indexed[case_id]
                before = _stock(dsn, case)
                previous = {k: before[k] for k in changes}
                mutated_key = {k: changes.get(k, v) for k, v in case["key"].items()}
                try:
                    _write(dsn, case, case["key"], changes)
                    response = await request(client, case)
                    assert response.status_code == 200, response.text
                    with pytest.raises(oracle.ProjectionMismatchError) as error:
                        oracle.assert_response(
                            case, response.status_code, response.json()
                        )
                    assert error.value.evidence["kind"] == expected_kind
                    evidence = {
                        "case": case_id,
                        "changes": changes,
                        "reason": reason,
                        "status": response.status_code,
                        "rejection": error.value.evidence,
                        "payload": response.json(),
                    }
                finally:
                    _write(dsn, case, mutated_key, previous)
                restored = await request(client, case)
                evidence["restored_cells"] = oracle.assert_response(
                    case, restored.status_code, restored.json()
                )
                result["sql_mutations"].append(evidence)

            # This corruption is intentionally masked by COR08's denominator
            # guard. It is a protected control, never counted as oracle rejection.
            case = indexed["vacance:01009"]
            original = _stock(dsn, case)["taux_vacance"]
            try:
                _write(dsn, case, case["key"], {"taux_vacance": 0})
                response = await request(client, case)
                assert (
                    oracle.assert_response(case, response.status_code, response.json())
                    == 3
                )
                result["masked_control"] = {
                    "case": case["id"],
                    "change": {"taux_vacance": 0},
                    "public_value": response.json()["donnees"][0]["taux_vacance"],
                    "classification": (
                        "denominator guard retained NULL; no oracle rejection"
                    ),
                }
            finally:
                _write(dsn, case, case["key"], {"taux_vacance": original})

            # Absence and publication failure must not pass as wrong-value tests.
            case = indexed["revenus:44109"]
            missing = await client.get("/v1/commune", params={"code_insee": "99990"})
            assert missing.status_code == 200
            with pytest.raises(oracle.ProjectionMismatchError) as error:
                oracle.assert_response(case, missing.status_code, missing.json())
            assert error.value.evidence["kind"] == "commune_absent"
            result["availability"].append(
                {"status": 200, "rejection": error.value.evidence}
            )
            generation = str(uuid4())
            try:
                with psycopg.connect(dsn) as conn:
                    conn.execute(
                        "INSERT INTO etl_publications"
                        "(generation,dataset_name,table_name,status) "
                        "VALUES(%s,'revenus','revenus','failed')",
                        (generation,),
                    )
                response = await request(client, case)
                assert response.status_code == 503
                with pytest.raises(oracle.ProjectionMismatchError) as error:
                    oracle.assert_response(case, response.status_code, response.json())
                assert error.value.evidence["kind"] == "publication_unavailable"
                result["availability"].append(
                    {"status": 503, "rejection": error.value.evidence}
                )
            finally:
                with psycopg.connect(dsn) as conn:
                    assert (
                        conn.execute(
                            "DELETE FROM etl_publications WHERE generation=%s",
                            (generation,),
                        ).rowcount
                        == 1
                    )
            result["final_restoration_cells"] = 0
            for case in projected:
                response = await request(client, case)
                result["final_restoration_cells"] += oracle.assert_response(
                    case, response.status_code, response.json()
                )
    finally:
        await engine.dispose()
    return result


def test_frozen_commune_projection_rejects_sql_corruptions(
    isolated_pg_dsn, monkeypatch, tmp_path
):
    manifest = source.load_manifest()
    cases = manifest["cases"]
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    monkeypatch.setenv("DATABASE_URL", isolated_pg_dsn)
    # env.py reads settings and overrides Config.sqlalchemy.url.
    command.upgrade(cfg, "head")
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    for module in (income_module, vacancy_module):
        monkeypatch.setattr(
            module, "get_download_dir", lambda dataset: tmp_path / dataset
        )
    vacancy = vacancy_module.VacancePipeline()
    vacancy_fixture = source.FIXTURES / "cor08_lovac/official-communes26.csv"

    async def download_vacancy_fixture():
        return [vacancy_fixture]

    monkeypatch.setattr(vacancy, "_async_download", download_vacancy_fixture)
    imports = [
        (
            income_module.RevenusPipeline(),
            [source.FIXTURES / "cor05_revenus/FILO2021_DEC_COM.csv"],
            9,
        ),
        (
            LoyersPipeline(),
            [source.FIXTURES / "loyers" / (kind + ".csv") for kind in source.KINDS],
            8,
        ),
        (
            vacancy,
            vacancy.download(),
            5,
        ),
    ]
    for pipeline, paths, expected_count in imports:
        monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: isolated_pg_dsn)
        pipeline.create_staging_table(isolated_pg_dsn)
        count = 0
        for path in paths:
            pipeline.validate_schema(path)
            count += pipeline.stage(pipeline.transform(path))
        assert count == expected_count
        pipeline.swap()
    with psycopg.connect(isolated_pg_dsn) as conn:
        # Synthetic demographic anchors only; no demographic claim is made.
        for code in sorted({next(iter(case["key"].values())) for case in cases}):
            conn.execute(
                "INSERT INTO demographie(code_insee,nom_commune,code_departement,"
                "population,annee) "
                "VALUES(%s,'Oracle anchor',%s,100,2025)",
                (code, code[:2]),
            )
        recreate_commune_synthese(conn)
        # The routes read the scope first (DATA-03): host the commune sources.
        install_instance_scope(conn, COMMUNE_SOURCES, DEPARTEMENTS)
    result = asyncio.run(_exercise(isolated_pg_dsn, cases))
    result.update(
        {
            "source_manifest_sha256_lf": source.sha(source.MANIFEST, lf=True),
            "source_files": manifest["sources"],
            "imported_rows": len(cases),
            "unprojected_cases": [
                c["id"] for c in cases if not oracle.expected_cells(c)
            ],
            "boundary": (
                "frozen CSV -> validate_schema/transform/COPY/swap "
                "-> complete Commune DDL -> SQL read -> real /v1/commune ASGI"
            ),
            "not_certified": [
                "frontend renderer",
                "auth/quota middleware",
                "network acquisition",
                "runner national gates",
                "production publication",
            ],
        }
    )
    assert len(result["positive"]) == 18
    assert sum(item["count"] for item in result["positive"]) == 132
    assert len(result["sql_mutations"]) == 19
    assert result["final_restoration_cells"] == 132
    if output := os.environ.get("L09_COMMUNE_OUTPUT"):
        Path(output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
