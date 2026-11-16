"""L10: mutate every frozen source-oracle cell through committed SQL and HTTP.

This matrix measures detection coverage within the existing 22 source cases.
It also demonstrates blind spots by changing every emitted unclaimed field.
No source expectation is inferred from the application or its HTTP response.
"""

import asyncio
import json
import os
from collections import Counter
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
from app.config import settings
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_revenus import pipeline as income_module
from app.etl_vacance import pipeline as vacancy_module
from app.loyers.router import router as loyers_router
from app.revenus.router import router as revenus_router
from app.vacance.router import router as vacance_router
from tests.integration.instance_scope_fixture import install_scope_at
from tests.oracles import communal_sources as oracle
from tests.test_stabilisation_l10_source_labels import source_cases_with_labels

pytestmark = pytest.mark.destructive
TABLES = {"revenus": "revenus", "loyers": "indicateurs_loyers", "vacance": "vacance"}


def _where(selector):
    return sql.SQL(" AND ").join(
        sql.SQL("{}=%s").format(sql.Identifier(field)) for field in selector
    )


def _stock(dsn, case, selector=None):
    selector = selector or case["key"]
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            sql.SQL("SELECT to_jsonb(t) FROM {} t WHERE {}").format(
                sql.Identifier(TABLES[case["dataset"]]), _where(selector)
            ),
            tuple(selector.values()),
        ).fetchone()
    assert row is not None, (case["id"], selector)
    return row[0]


def _write(dsn, case, selector, field, value):
    with psycopg.connect(dsn) as conn:
        changed = conn.execute(
            sql.SQL("UPDATE {} SET {}=%s WHERE {}").format(
                sql.Identifier(TABLES[case["dataset"]]),
                sql.Identifier(field),
                _where(selector),
            ),
            (value, *selector.values()),
        )
        assert changed.rowcount == 1


def _wrong(value, field):
    if field == "code_departement":
        return "99"
    if field in ("code_insee", "code_commune"):
        return "99990"
    if type(value) is bool:
        return not value
    if isinstance(value, str):
        return "wrong" if value != "wrong" else "other"
    if type(value) is float:
        # Exactly representable mutation: do not confuse PostgreSQL's JSON
        # rendering precision with a failed SQL write.
        return 0.5 if value != 0.5 else 0.25
    return 1 if value is None else value + 1


async def _exercise(dsn, cases):  # noqa: C901
    original = {case["id"]: case for case in oracle.load_manifest()["cases"]}
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))

    async def database():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()
    for router in (revenus_router, loyers_router, vacance_router):
        app.include_router(router, dependencies=[Depends(require_publication)])
    app.dependency_overrides[get_db] = database
    result = {"positive": [], "certified_mutations": [], "unclaimed_mutations": []}

    async def request(client, case):
        response = await client.get(
            "/v1/" + case["dataset"],
            params={"code_insee": next(iter(case["key"].values()))},
        )
        assert response.status_code == 200, response.text
        matches = [
            row
            for row in response.json()["donnees"]
            if all(row.get(key) == value for key, value in case["key"].items())
        ]
        assert len(matches) <= 1
        return response.json(), matches[0] if matches else None

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            for case in cases:
                stock = _stock(dsn, case)
                oracle.assert_row(case, stock)
                _, baseline = await request(client, case)
                assert baseline is not None
                oracle.assert_row(case, baseline)
                fields = oracle.expected_row(case)
                unclaimed = sorted(set(baseline) - set(fields))
                result["positive"].append(
                    {
                        "case": case["id"],
                        "certified_fields": list(fields),
                        "unclaimed_fields": unclaimed,
                        "actual": baseline,
                    }
                )
                # Every covered cell and every extra emitted field is mutated.
                # The source expectation remains fixed throughout this loop.
                for field in [*fields, *unclaimed]:
                    assert field in stock, (case["id"], field)
                    previous = stock[field]
                    # Unclaimed labels can be NULL; use a text mutation there.
                    wrong = (
                        "L10 unverified"
                        if field in unclaimed and previous is None
                        else _wrong(previous, field)
                    )
                    selector = dict(case["key"])
                    if field in selector:
                        selector[field] = wrong
                    try:
                        _write(dsn, case, case["key"], field, wrong)
                        assert _stock(dsn, case, selector)[field] == wrong
                        payload, actual = await request(client, case)
                        evidence = {
                            "case": case["id"],
                            "field": field,
                            "sql_before": previous,
                            "sql_wrong": wrong,
                            "http_status": 200,
                            "actual": actual,
                        }
                        if field in unclaimed:
                            assert actual is not None
                            assert actual[field] != baseline[field]
                            oracle.assert_row(case, actual)
                            evidence["outcome"] = "outside_oracle_not_detected"
                            result["unclaimed_mutations"].append(evidence)
                        elif actual is None:
                            assert field in case["key"]
                            evidence["outcome"] = "source_identity_absent"
                            evidence["payload"] = payload
                            result["certified_mutations"].append(evidence)
                        elif actual[field] == fields[field]:
                            # Only the documented secret-component guard may
                            # mask a rate corruption before reaching the oracle.
                            assert case["dataset"] == "vacance"
                            assert field.startswith("taux_vacance")
                            assert fields[field] is None
                            numerator = {
                                "taux_vacance": "nb_logements_vacants",
                                "taux_vacance_moins_2ans": "nb_vacants_moins_2ans",
                                "taux_vacance_plus_2ans": "nb_vacants_plus_2ans",
                            }[field]
                            assert (
                                stock["nb_logements_prives"] is None
                                or stock[numerator] is None
                            )
                            oracle.assert_row(case, actual)
                            evidence["outcome"] = "secret_component_guard_masked"
                            result["certified_mutations"].append(evidence)
                        else:
                            if field not in oracle.expected_row(original[case["id"]]):
                                oracle.assert_row(original[case["id"]], actual)
                                evidence["old_oracle_accepts_wrong_label"] = True
                            with pytest.raises(AssertionError) as rejected:
                                oracle.assert_row(case, actual)
                            assert field in str(rejected.value)
                            evidence["outcome"] = "oracle_value_rejected"
                            evidence["rejection"] = str(rejected.value)
                            result["certified_mutations"].append(evidence)
                    finally:
                        _write(dsn, case, selector, field, previous)
                    _, restored = await request(client, case)
                    assert restored == baseline
                    assert _stock(dsn, case) == stock
                    oracle.assert_row(case, restored)
            # Confirm the full cohort remains correct after all mutations.
            for case in cases:
                _, actual = await request(client, case)
                oracle.assert_row(case, actual)
    finally:
        await engine.dispose()
    result["outcomes"] = dict(
        Counter(
            row["outcome"]
            for key in ("certified_mutations", "unclaimed_mutations")
            for row in result[key]
        )
    )
    return result


def test_every_frozen_oracle_cell_and_unclaimed_response_field(
    isolated_pg_dsn, monkeypatch, tmp_path
):
    manifest = oracle.load_manifest()
    cases = source_cases_with_labels()
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    monkeypatch.setenv("DATABASE_URL", isolated_pg_dsn)
    command.upgrade(cfg, "head")
    # The routes read the scope first (DATA-03): host the datasets exercised.
    install_scope_at(isolated_pg_dsn, ("loyers", "revenus", "vacance"), DEPARTEMENTS)
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
    result = asyncio.run(_exercise(isolated_pg_dsn, cases))
    assert len(result["positive"]) == 22
    assert (
        len(result["certified_mutations"])
        == sum(len(oracle.expected_row(case)) for case in cases)
        == 316
    )
    assert len(result["unclaimed_mutations"]) == sum(
        len(row["unclaimed_fields"]) for row in result["positive"]
    )
    result.update(
        {
            "source_manifest_sha256_lf": oracle.sha(oracle.MANIFEST, lf=True),
            "sources": manifest["sources"],
            "imported_rows": len(cases),
            "source_positive_cells": 316,
            "independent_cases": cases,
            "boundary": "frozen sources -> real ETL/COPY/swap -> SQL -> source ASGI",
            "not_certified": [
                "Commune projection",
                "frontend renderer",
                "auth/quota",
                "runner national gates",
                "production",
                "other source rows",
            ],
            "release_stable": False,
            "gate_58_2": "blocked",
        }
    )
    if output := os.environ.get("L10_ORACLE_MATRIX_OUTPUT"):
        Path(output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf8"
        )
