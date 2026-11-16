"""COR-09/13/15: quarantine must stop before SQL, files, locks and exports."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from app.database import get_db
from app.encadrement_loyers.router import router as rents_router
from app.scoring.router import router as scoring_router


@pytest.mark.parametrize(
    "path,params",
    [
        ("/v1/scoring/renov", {"code_departement": department})
        for department in ("33", "44", "85")
    ]
    + [
        (
            "/v1/scoring/renov-target",
            {"code_departement": "44", "etiquette_dpe": "F,G"},
        ),
        ("/v1/encadrement-loyers", {"lat": 48.8566, "lon": 2.3522}),
        ("/v1/encadrement-loyers", {"ville": "PARIS", "numero_quartier": 1}),
    ],
)
async def test_routes_never_query_or_export_unqualified_values(path, params):
    session = Mock()
    session.execute = AsyncMock(
        side_effect=AssertionError("SQL forbidden in quarantine")
    )
    app = FastAPI()
    app.include_router(scoring_router)
    app.include_router(rents_router)

    async def database():
        yield session

    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://fixture"
    ) as client:
        response = await client.get(path, params=params)
    assert response.status_code == 503
    assert response.json()["statut"] == "indisponible"
    assert response.json()["donnees"] == []
    assert "ne signifie pas absence" in response.json()["raison"]
    assert "application/json" in response.headers["content-type"]
    assert "content-disposition" not in response.headers
    session.execute.assert_not_called()


def test_scoring_is_not_an_expected_rebuild_or_source_dependency():
    from app.common.matviews import EXPECTED_MATVIEWS
    from app.etl.hooks import DependentMatviewHook

    assert "scoring_renov_target" not in EXPECTED_MATVIEWS
    assert all(
        name != "scoring_renov_target"
        for refs in DependentMatviewHook._DEPENDENCIES.values()
        for name, _ in refs
    )


@pytest.mark.parametrize(
    "module,name,args",
    [
        ("scoring", "scoring_renov_par_departement", ("44",)),
        ("scoring", "query_scoring_export", ("85",)),
        ("encadrement_loyers", "encadrement_par_point", (48.85, 2.35)),
        ("encadrement_loyers", "encadrement_par_quartier", ("PARIS", 1)),
    ],
)
async def test_internal_queries_cannot_bypass_quarantine(module, name, args):
    from importlib import import_module

    from app.common.unqualified_surfaces import SurfaceQuarantinedError

    session = Mock()
    with pytest.raises(SurfaceQuarantinedError):
        await getattr(import_module(f"app.{module}.queries"), name)(session, *args)
    assert session.mock_calls == []


@pytest.mark.parametrize(
    "value", ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1", "\r=1+1", "safe"]
)
def test_no_csv_serialization_even_for_private_callers(value):
    from app.common.unqualified_surfaces import SurfaceQuarantinedError
    from app.scoring.router import _rows_to_csv

    with pytest.raises(SurfaceQuarantinedError):
        _rows_to_csv([{"adresse": value, "prix_achat": 123456}])


def test_scoring_refresh_and_repair_refuse_before_sql(monkeypatch):
    from app.common.unqualified_surfaces import SurfaceQuarantinedError
    from app.scheduler.jobs import refresh_scoring_renov_target
    from app.scoring.matview_ddl import recreate_scoring_renov_target

    connect = Mock(side_effect=AssertionError("No SQL"))
    monkeypatch.setattr("psycopg.connect", connect)
    with pytest.raises(SurfaceQuarantinedError):
        refresh_scoring_renov_target()
    connection = Mock()
    with pytest.raises(SurfaceQuarantinedError):
        recreate_scoring_renov_target(connection)
    assert connection.mock_calls == []
    connect.assert_not_called()


@pytest.mark.parametrize(
    "method,args",
    [
        ("create_staging_table", ("forbidden",)),
        ("drop_staging_table", ("forbidden",)),
        ("download", ()),
        ("validate_schema", ("forbidden",)),
        ("transform", ("forbidden",)),
        ("stage", ("forbidden",)),
        ("swap", ()),
    ],
)
def test_encadrement_pipeline_cannot_mutate_or_fetch_even_if_constructor_bypassed(
    method, args
):
    from app.common.unqualified_surfaces import SurfaceQuarantinedError
    from app.etl_encadrement_loyers.pipeline import EncadrementLoyersPipeline

    with pytest.raises(SurfaceQuarantinedError):
        EncadrementLoyersPipeline()
    pipeline = EncadrementLoyersPipeline.__new__(EncadrementLoyersPipeline)
    with pytest.raises(SurfaceQuarantinedError):
        getattr(pipeline, method)(*args)


@pytest.mark.parametrize(
    "args", [["encadrement_loyers"], ["--dry-run", "encadrement_loyers"]]
)
def test_manual_etl_refuses_before_constructing_runner(monkeypatch, args):
    import run_etl

    runner = Mock(side_effect=AssertionError("No runner"))
    monkeypatch.setattr(run_etl, "PipelineRunner", runner)
    monkeypatch.setattr(run_etl.sys, "argv", ["run_etl", *args])
    with pytest.raises(SystemExit) as exc:
        run_etl.main()
    assert exc.value.code == 2
    runner.assert_not_called()
    assert "encadrement_loyers" not in run_etl.PIPELINE_MAP


def test_manual_scoring_job_is_an_explicit_nonzero_refusal(monkeypatch, capsys):
    import json

    from app.scheduler import run_job

    monkeypatch.setattr(run_job.sys, "argv", ["run_job", "refresh-scoring-renov"])
    assert run_job.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["statut"] == "indisponible"
    assert payload["donnees"] == []


def test_openapi_advertises_only_json_503_for_quarantined_routes():
    app = FastAPI()
    app.include_router(scoring_router)
    app.include_router(rents_router)
    for route in app.openapi()["paths"].values():
        operation = route["get"]
        assert operation["deprecated"] is True
        assert "200" not in operation["responses"]
        assert set(operation["responses"]["503"]["content"]) == {"application/json"}


async def test_public_examples_are_actual_asgi_quarantine_responses():
    import json
    from pathlib import Path

    examples = json.loads(
        (
            Path(__file__).parents[3]
            / "frontend/src/lib/data/__fixtures__/quarantined-surfaces.json"
        ).read_text(encoding="utf-8")
    )
    app = FastAPI()
    app.include_router(scoring_router)
    app.include_router(rents_router)
    session = Mock()

    async def db():
        yield session

    app.dependency_overrides[get_db] = db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://fixture"
    ) as client:
        for example in examples.values():
            response = await client.get(example["path"], params=example["params"])
            assert response.status_code == example["status"] == 503
            assert response.json() == example["payload"]
    assert session.mock_calls == []
