"""Exercise the closed DPE wire contract without altering the Golden oracle."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.common.proxy_coverage import ProxySource
from app.common.schemas import ReponseApi
from app.database import get_db
from app.resolve import router as resolve_router
from app.resolve.schemas import (
    RESOLUTION_RAISONS,
    DatasetResolutionState,
    ResultatMultiResolution,
)
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from scripts.verify_golden_cases import _dataset_state

pytestmark = pytest.mark.unit

CASES = [
    ("value", None, "value"),
    ("error", "temporary_error", "temporary_error"),
    ("absent", "out_of_coverage", "out_of_coverage"),
]


def assert_closed_dpe(block, presence, reason, expected, raison=None):
    # Spell out the public contract independently of the model's fields.
    assert block == {
        "dataset": "dpe",
        "presence": presence,
        "absence_reason": reason,
        "raison": raison,
    }
    assert (
        _dataset_state({"dataset_states": {"dpe": block}}, "dpe", "address") == expected
    )


@pytest.mark.parametrize("presence,reason,expected", CASES)
@pytest.mark.parametrize("mode", ["python", "json", "json_text"])
def test_dpe_model_serializes_closed_state(presence, reason, expected, mode):
    state = DatasetResolutionState(
        dataset="dpe", presence=presence, absence_reason=reason
    )
    block = (
        json.loads(state.model_dump_json())
        if mode == "json_text"
        else state.model_dump(mode=mode)
    )
    assert_closed_dpe(block, presence, reason, expected)


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(resolve_router.router)

    async def db():
        yield object()

    app.dependency_overrides[get_db] = db
    # _resolve_request is stubbed below: the scope is resolved, never read.
    app.dependency_overrides[get_instance_scope] = lambda: InstanceScope(
        datasets=frozenset(), departements=()
    )
    return app


@pytest.mark.parametrize("presence,reason,expected", CASES)
def test_unchanged_golden_oracle_accepts_actual_serialized_dpe(
    presence, reason, expected
):
    state = DatasetResolutionState(
        dataset="dpe", presence=presence, absence_reason=reason
    )
    payload = {"dataset_states": {"dpe": json.loads(state.model_dump_json())}}
    assert _dataset_state(payload, "dpe", "address") == expected


@pytest.mark.parametrize("presence,reason,expected", CASES)
async def test_real_resolve_http_keeps_closed_dpe_and_proxy_sources(
    app, monkeypatch, presence, reason, expected
):
    source = ProxySource(
        statut="partiel",
        nombre_retournes=1,
        total=51,
        complet=False,
        limite=1,
        pages_lues=1,
    )
    result = ResultatMultiResolution(
        type_detecte="gps",
        latitude=47.2,
        longitude=-1.5,
        datasets={"dpe": None},
        dpe_inventory={
            "version": 2,
            "state": {"value": "ready", "error": "unavailable", "absent": "absent"}[
                presence
            ],
            "context_ref": "synthetic-context" if presence == "value" else None,
            "items": [{"document_ref": "synthetic-document"}]
            if presence == "value"
            else [],
            "total": {"value": 1, "error": None, "absent": 0}[presence],
            "distribution": {"unknown": 1 if presence == "value" else 0},
            "page_size": 20,
            "has_more": False,
            "read_state": "partial" if presence == "error" else "complete",
        },
        dpe_attribution={
            "version": 2,
            "status": {"value": "unassigned", "error": "error", "absent": "absent"}[
                presence
            ],
            "reason": {
                "value": "dwelling_identity_unproven",
                "error": "temporary_error",
                "absent": "not_reported",
            }[presence],
        },
        dataset_states={
            "dpe": DatasetResolutionState(
                dataset="dpe",
                presence=presence,
                absence_reason=reason,
                raison=RESOLUTION_RAISONS.get(reason),
            ),
            "rnb": DatasetResolutionState(
                dataset="rnb", presence="value", sources={"batiments": source}
            ),
        },
    )
    # Stub the service boundary only: the real route and response model serialize.
    monkeypatch.setattr(
        resolve_router,
        "_resolve_request",
        AsyncMock(return_value=ReponseApi(donnees=[result])),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local.test"
    ) as client:
        response = await client.get(
            "/v1/resolve", params={"q": "47.2,-1.5", "datasets": "dpe,rnb"}
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    states = response.json()["donnees"][0]["dataset_states"]
    assert_closed_dpe(
        states["dpe"], presence, reason, expected, RESOLUTION_RAISONS.get(reason)
    )
    assert states["rnb"] == {
        "dataset": "rnb",
        "presence": "value",
        "absence_reason": None,
        "raison": None,
        "sources": {
            "batiments": {
                "statut": "partiel",
                "nombre_retournes": 1,
                "total": 51,
                "complet": False,
                "limite": 1,
                "pages_lues": 1,
            }
        },
    }


@pytest.mark.parametrize(
    "sources", [{}, {"invented": ProxySource(statut="indisponible")}]
)
def test_dpe_rejects_proxy_provenance_instead_of_hiding_it(sources):
    with pytest.raises(ValidationError, match="DPE state cannot carry proxy sources"):
        DatasetResolutionState(dataset="dpe", presence="value", sources=sources)


def test_proxy_failure_preserves_unknown_coverage_nulls():
    state = DatasetResolutionState(
        dataset="risques",
        presence="error",
        absence_reason="temporary_error",
        sources={"risques_naturels": ProxySource(statut="indisponible")},
    )
    assert json.loads(state.model_dump_json())["sources"] == {
        "risques_naturels": {
            "statut": "indisponible",
            "nombre_retournes": None,
            "total": None,
            "complet": None,
            "limite": None,
            "pages_lues": 0,
        }
    }
