"""COR23: independent frozen-source expectations through real SQL/HTTP.

Only wrapper-owned child PostGIS is used. Download is replaced by frozen bytes;
real transforms/COPY/swaps and complete commune DDL are retained. No renderer,
auth, runner-wide national gates or production generation is certified here.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.common.publication_readiness import require_publication
from app.commune.matview_ddl import recreate_commune_synthese
from app.commune.router import router as commune_router
from app.config import settings
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_revenus import pipeline as income_module
from app.etl_vacance import pipeline as vacancy_module
from app.loyers.router import router as loyers_router
from app.revenus.router import router as revenus_router
from app.vacance.router import router as vacance_router
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.oracles import communal_sources as oracle

pytestmark = pytest.mark.destructive
TABLES = {"revenus": "revenus", "loyers": "indicateurs_loyers", "vacance": "vacance"}


def _where(case):
    return sql.SQL(" AND ").join(
        sql.SQL("{}=%s").format(sql.Identifier(k)) for k in case["key"]
    )


def _read_sql(dsn, case):
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            sql.SQL("SELECT to_jsonb(t) FROM {} t WHERE {}").format(
                sql.Identifier(TABLES[case["dataset"]]), _where(case)
            ),
            tuple(case["key"].values()),
        ).fetchone()
    assert row is not None
    return row[0]


def _change_stock(dsn, case, field, value):
    with psycopg.connect(dsn) as conn:
        updated = conn.execute(
            sql.SQL("UPDATE {} SET {}=%s WHERE {}").format(
                sql.Identifier(TABLES[case["dataset"]]),
                sql.Identifier(field),
                _where(case),
            ),
            (value, *case["key"].values()),
        )
        assert updated.rowcount == 1
        recreate_commune_synthese(conn)


async def _requests(dsn, cases):
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))

    async def database():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()
    for router in (revenus_router, loyers_router, vacance_router):
        app.include_router(router, dependencies=[Depends(require_publication)])
    app.include_router(commune_router)
    app.dependency_overrides[get_db] = database
    payloads, comparisons, rejected = {}, [], []

    async def response_for(client, case):
        code = next(iter(case["key"].values()))
        response = await client.get(
            "/v1/" + case["dataset"], params={"code_insee": code}
        )
        assert response.status_code == 200, response.text
        matching = [
            r
            for r in response.json()["donnees"]
            if all(r.get(k) == v for k, v in case["key"].items())
        ]
        assert len(matching) == 1, case["id"]
        return response.json(), matching[0]

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            for case in cases:
                sql_row = _read_sql(dsn, case)
                oracle.assert_row(case, sql_row)
                payload, actual = await response_for(client, case)
                oracle.assert_row(case, actual)
                code = next(iter(case["key"].values()))
                response = await client.get("/v1/commune", params={"code_insee": code})
                assert response.status_code == 200, response.text
                commune = next(
                    r for r in response.json()["donnees"] if r["code_insee"] == code
                )
                for public_field, expected_field in case["commune_fields"].items():
                    assert (
                        commune[public_field] == case["fields"][expected_field]["value"]
                    )
                if case.get("commune_object"):
                    oracle.assert_row(case, commune[case["commune_object"]])
                payloads[case["id"]] = {"dataset": payload, "commune": response.json()}
                comparisons.append(
                    {
                        "case": case["id"],
                        "sql_fields": list(oracle.expected_row(case)),
                        "http_fields": list(oracle.expected_row(case)),
                        "commune_fields": list(case["commune_fields"]),
                        "commune_object": case.get("commune_object"),
                    }
                )

            # Genuine DB corruptions are visible over HTTP and rejected by the
            # oracle. Restore each value and rebuild before the next mutation.
            indexed = {case["id"]: case for case in cases}
            corruptions = [
                ("revenus:44109", "revenu_median", 24170, "wrong DISP series"),
                ("revenus:44109", "annee", 2022, "wrong vintage"),
                ("revenus:44109", "revenu_median", 2032, "annual interpreted monthly"),
                ("revenus:01019", "revenu_median", 0, "secret replaced by zero"),
                (
                    "loyers:05066:appartement",
                    "loyer_m2",
                    117.12,
                    "monthly interpreted annual",
                ),
                ("loyers:05066:appartement", "annee", 2026, "wrong vintage"),
                (
                    "loyers:05066:appartement",
                    "type_prediction",
                    "commune",
                    "wrong geography",
                ),
                (
                    "loyers:05066:appartement",
                    "nb_observations_commune",
                    484,
                    "maille count substituted",
                ),
                (
                    "loyers:05066:appartement",
                    "charges_comprises",
                    False,
                    "wrong charges definition",
                ),
                ("loyers:05066:appartement", "r2_ajuste", None, "known metadata lost"),
                ("vacance:44109", "taux_vacance", 0.1, "percentage interpreted ratio"),
                ("vacance:44109", "annee", 2025, "denominator year substituted"),
                ("vacance:02343", "taux_vacance", None, "measured zero erased"),
                (
                    "vacance:01009",
                    "nb_logements_prives",
                    100,
                    "secret denominator fabricated",
                ),
            ]
            for case_id, field, wrong, reason in corruptions:
                case = indexed[case_id]
                previous = _read_sql(dsn, case)[field]
                try:
                    _change_stock(dsn, case, field, wrong)
                    _, actual = await response_for(client, case)
                    with pytest.raises(AssertionError):
                        oracle.assert_row(case, actual)
                    rejected.append(
                        {
                            "case": case_id,
                            "field": field,
                            "wrong": wrong,
                            "reason": reason,
                            "boundary": "committed SQL corruption -> real source "
                            "HTTP -> independent rejection",
                        }
                    )
                finally:
                    _change_stock(dsn, case, field, previous)
                _, restored = await response_for(client, case)
                oracle.assert_row(case, restored)
    finally:
        await engine.dispose()
    return {
        "payloads": payloads,
        "comparisons": comparisons,
        "sql_negative_controls": rejected,
    }


def test_frozen_sources_sql_http_and_negative_controls(
    isolated_pg_dsn, monkeypatch, tmp_path
):
    manifest = oracle.load_manifest()
    cases = manifest["cases"]
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    # env.py reads settings and overrides Config.sqlalchemy.url.
    command.upgrade(cfg, "head")
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    for module in (income_module, vacancy_module):
        monkeypatch.setattr(
            module, "get_download_dir", lambda dataset: tmp_path / dataset
        )
    vacancy = vacancy_module.VacancePipeline()
    vacancy_fixture = oracle.FIXTURES / "cor08_lovac/official-communes26.csv"

    async def download_vacancy_fixture():
        return [vacancy_fixture]

    monkeypatch.setattr(vacancy, "_async_download", download_vacancy_fixture)
    imports = [
        (
            income_module.RevenusPipeline(),
            [oracle.FIXTURES / "cor05_revenus/FILO2021_DEC_COM.csv"],
            9,
        ),
        (
            LoyersPipeline(),
            [oracle.FIXTURES / "loyers" / (kind + ".csv") for kind in oracle.KINDS],
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
    result = asyncio.run(_requests(isolated_pg_dsn, cases))
    result.update(
        {
            "manifest_sha256_lf": oracle.sha(oracle.MANIFEST, lf=True),
            "source_cases": len(cases),
            "source_files": manifest["sources"],
            "boundary": "frozen CSV -> real validate_schema/transform/COPY/swap "
            "-> SQL -> real source and full commune SQL/ASGI",
            "not_certified": [
                "runner and national gates",
                "frontend renderer",
                "authentication",
                "network acquisition",
                "production publication",
            ],
        }
    )
    if output := os.environ.get("L08_ORACLES_OUTPUT"):
        Path(output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    assert len(result["comparisons"]) == 22
    assert len(result["sql_negative_controls"]) == 14
