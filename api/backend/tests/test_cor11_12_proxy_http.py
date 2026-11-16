"""Real ASGI routers/Resolve on mocked upstreams; no DB or live HTTP."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import FastAPI

from app.bdnb import client as bdnb_client
from app.bdnb.router import router as bdnb_router
from app.common.exceptions import ErreurApiError, _handle_exception_api
from app.database import get_db
from app.errial import client as errial_client
from app.errial.router import router as errial_router
from app.georisques.router import router as risks_router
from app.resolve import multi
from app.resolve import router as resolve_router
from app.resolve.schemas import ResultatResolution
from app.rnb.router import router as rnb_router
from app.scope.dependencies import get_instance_scope
from app.urbanisme.router import router as gpu_router
from tests.scope_doubles import FULL_SCOPE

pytestmark = pytest.mark.unit


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.add_exception_handler(ErreurApiError, _handle_exception_api)
    for router in [
        gpu_router,
        risks_router,
        rnb_router,
        bdnb_router,
        errial_router,
        resolve_router.router,
    ]:
        app.include_router(router)

    async def db():
        yield object()

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE

    @asynccontextmanager
    async def factory():
        yield object()

    monkeypatch.setattr(resolve_router, "async_session", factory)
    monkeypatch.setattr(
        resolve_router,
        "_resolve_gps",
        AsyncMock(
            return_value=[
                ResultatResolution(
                    type_detecte="gps",
                    latitude=47.2,
                    longitude=-1.5,
                    code_insee="44109",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        resolve_router, "get_provenance_facts", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        multi,
        "reverse_geocoder",
        AsyncMock(return_value=[{"ban_id": "synthetic-ban", "code_commune": "44109"}]),
    )
    bdnb_client._parcelle_cache.clear()
    bdnb_client._adresse_cache.clear()
    errial_client._avis_cache.clear()
    yield app
    bdnb_client._parcelle_cache.clear()
    bdnb_client._adresse_cache.clear()
    errial_client._avis_cache.clear()


async def test_real_resolve_and_public_gpu_keep_partial_states(app):
    with respx.mock as mock:
        mock.get("https://apicarto.ign.fr/api/gpu/zone-urba").respond(
            200,
            json={
                "features": [
                    {"properties": {"libelle": "UA", "typezone": "U"}},
                    {"properties": {"libelle": "NB", "typezone": "N"}},
                ],
                "totalFeatures": 2,
            },
        )
        mock.get("https://apicarto.ign.fr/api/gpu/prescription-surf").respond(503)
        mock.get("https://georisques.gouv.fr/api/v1/gaspar/risques").respond(503)
        mock.get("https://georisques.gouv.fr/api/v1/rga").respond(
            200, json={"exposition": "Faible"}
        )
        for name in ["radon", "installations_classees"]:
            mock.get(f"https://georisques.gouv.fr/api/v1/{name}").respond(
                200, json={"results": 0, "data": []}
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            response = await client.get(
                "/v1/resolve",
                params={"q": "47.2,-1.5", "datasets": "risques,urbanisme"},
            )
            assert response.status_code == 200
            payload = response.json()
            resolved = payload["donnees"][0]
            assert resolved["datasets"]["risques"]["risques_naturels"] is None
            assert (
                resolved["dataset_states"]["risques"]["sources"]["risques_naturels"][
                    "statut"
                ]
                == "indisponible"
            )
            assert {
                warning["dataset"]
                for warning in resolved["avertissements"]
                if warning["code"] == "SOURCE_INCOMPLETE"
            } == {"risques", "urbanisme"}
            assert len(resolved["datasets"]["urbanisme"]["zones_plu"]) == 2
            direct = await client.get(
                "/v1/urbanisme", params={"lat": 47.2, "lon": -1.5}
            )
            assert direct.status_code == 200
            assert direct.json()["statut"] == "partiel"
            assert direct.json()["donnees"]["zones_plu"][0]["libelle"] == "UA"
            assert direct.json()["donnees"]["prescriptions"] is None
    if output := os.environ.get("COR11_12_API_FIXTURE_OUTPUT"):
        Path(output).write_text(
            json.dumps(
                {
                    "boundary": (
                        "Actual /v1/resolve and /v1/urbanisme ASGI, "
                        "real proxy clients, synthetic mocked upstream bodies. "
                        "GPS identity and provenance DB calls stubbed; no DB."
                    ),
                    "resolve": payload,
                    "urbanisme": direct.json(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_http_bounded_counts_and_resolve_list_compatibility(app):
    with respx.mock as mock:
        mock.get("https://rnb-api.beta.gouv.fr/api/alpha/buildings/closest/").respond(
            200,
            json={
                "count": 51,
                "results": [{"rnb_id": f"ABC{i:03}"} for i in range(50)],
                "next": "https://untrusted.example/page2",
            },
        )
        mock.get(
            "https://api.bdnb.io/v1/bdnb/donnees/batiment_groupe_complet/parcelle"
        ).respond(
            200, json=[{"batiment_groupe_id": f"synthetic-{i}"} for i in range(11)]
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            response = await client.get("/v1/rnb", params={"lat": 47.2, "lon": -1.5})
            data = response.json()["donnees"]
            assert response.status_code == 200
            assert response.json()["statut"] == "partiel"
            assert data["nombre_retournes"] == 50 and data["total"] == 51
            assert data["couverture"]["complet"] is False
            response = await client.get(
                "/v1/bdnb", params={"parcelle_id": "44109000AB0001"}
            )
            data = response.json()["donnees"]
            assert response.status_code == 200
            assert data["nombre_retournes"] == 10 and data["total"] is None
            assert data["couverture"]["complet"] is False
            response = await client.get(
                "/v1/resolve", params={"q": "47.2,-1.5", "datasets": "rnb"}
            )
            data = response.json()["donnees"][0]
            assert isinstance(data["datasets"]["rnb"], list)
            assert len(data["datasets"]["rnb"]) == 50
            assert data["dataset_states"]["rnb"]["sources"]["batiments"]["total"] == 51
            assert data["avertissements"][0]["code"] == "SOURCE_INCOMPLETE"


@pytest.mark.parametrize(
    "body",
    [
        {"hasError": True, "errorList": ["private marker"]},
        {"summary": {"commune": {}}},
    ],
)
async def test_errial_has_error_is_unavailable_in_public_and_resolve(app, body):
    with respx.mock as mock:
        mock.get("https://errial.georisques.gouv.fr/api/cadastre/-1.5/47.2").respond(
            200, json=[{"properties": {"code": "000-AB-0001", "commune": "44109"}}]
        )
        mock.get("https://errial.georisques.gouv.fr/api/avis").respond(200, json=body)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            direct = await client.get(
                "/v1/errial",
                params={"code_insee": "44109", "code_parcelle": "000-AB-0001"},
            )
            assert direct.status_code == 503
            assert "private marker" not in direct.text
            response = await client.get(
                "/v1/resolve", params={"q": "47.2,-1.5", "datasets": "errial"}
            )
            assert response.status_code == 200
            data = response.json()["donnees"][0]
            assert data["dataset_states"]["errial"]["presence"] == "error"
            assert "errial" not in data["datasets"]
            assert "private marker" not in response.text


async def test_rnb_known_missing_id_is_empty_but_invalid_body_is_unavailable(app):
    with respx.mock as mock:
        route = mock.get("https://rnb-api.beta.gouv.fr/api/alpha/buildings/ABC123/")
        route.respond(404)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            response = await client.get("/v1/rnb", params={"rnb_id": "ABC123"})
            assert response.status_code == 200
            data = response.json()["donnees"]
            assert data["batiments"] == [] and data["total"] == 0
            assert data["couverture"]["statut"] == "vide"
            route.respond(200, json={})
            response = await client.get("/v1/rnb", params={"rnb_id": "ABC123"})
            assert response.status_code == 503
