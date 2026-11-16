"""HTTP contract for the protected, opaque DPE document routes."""

import logging
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.auth.dependencies import check_quota_hybrid
from app.common.exceptions import ErreurApiError
from app.common.schemas import ProblemDetail
from app.dpe.references import InvalidReferenceError, RefreshRequiredError
from app.dpe.schemas import (
    DpeConsultedDocument,
    DpeInventoryDistribution,
    DpeInventoryItem,
    DpeInventoryPage,
)
from app.main import app

pytestmark = pytest.mark.smoke

CONTEXT = "opaque-context"
DOCUMENT = "opaque-document"


def _page(*, items: int = 1) -> DpeInventoryPage:
    return DpeInventoryPage(
        state="ready",
        context_ref=CONTEXT,
        items=[
            DpeInventoryItem(
                document_ref=f"opaque-document-{index}",
                type_batiment="appartement",
                etiquette_dpe="D",
            )
            for index in range(items)
        ],
        total=items,
        distribution=DpeInventoryDistribution(D=items),
        page_size=20,
        has_more=False,
        read_state="complete",
    )


def _document() -> DpeConsultedDocument:
    return DpeConsultedDocument(
        context_ref=CONTEXT,
        document_ref=DOCUMENT,
        type_batiment="appartement",
        etiquette_dpe="D",
        validity_by_date="unknown",
    )


@pytest.fixture()
async def public_client():
    """Use the real protected-router dependency without auth overrides."""
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


class TestProtection:
    @pytest.mark.parametrize(
        ("path", "params"),
        [
            ("/v1/bien/dpe/inventaire", {"contexte": CONTEXT}),
            (
                "/v1/bien/dpe/document",
                {"contexte": CONTEXT, "reference": DOCUMENT},
            ),
        ],
    )
    async def test_routes_require_existing_auth_quota(
        self, public_client, path, params
    ):
        response = await public_client.get(path, params=params)
        assert response.status_code in {401, 403}
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        ("path", "params"),
        [
            ("/v1/bien/dpe/inventaire", {"contexte": CONTEXT}),
            (
                "/v1/bien/dpe/document",
                {"contexte": CONTEXT, "reference": DOCUMENT},
            ),
        ],
    )
    async def test_routes_keep_the_existing_quota_gate(self, client, path, params):
        async def quota_exhausted():
            raise ErreurApiError(
                429,
                "Quota épuisé",
                headers={"Retry-After": "60"},
            )

        app.dependency_overrides[check_quota_hybrid] = quota_exhausted
        response = await client.get(path, params=params)
        assert response.status_code == 429
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["retry-after"] == "60"


class TestInventoryRoute:
    async def test_returns_direct_closed_page_and_forwards_standard_bound(
        self,
        client,
        monkeypatch,
    ):
        service = AsyncMock(return_value=_page())
        monkeypatch.setattr("app.logements.router.load_dpe_inventory", service)

        response = await client.get(
            "/v1/bien/dpe/inventaire",
            params={"contexte": CONTEXT},
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["state"] == "ready"
        assert "donnees" not in response.json()
        service.assert_awaited_once()
        assert service.await_args.kwargs == {
            "context_ref": CONTEXT,
            "cursor": None,
            "limit": 20,
        }

    async def test_forwards_opaque_cursor_and_maximum_page_size(
        self,
        client,
        monkeypatch,
    ):
        service = AsyncMock(return_value=_page())
        monkeypatch.setattr("app.logements.router.load_dpe_inventory", service)

        response = await client.get(
            "/v1/bien/dpe/inventaire",
            params={"contexte": CONTEXT, "curseur": "opaque-cursor", "limite": 50},
        )

        assert response.status_code == 200
        assert service.await_args.kwargs == {
            "context_ref": CONTEXT,
            "cursor": "opaque-cursor",
            "limit": 50,
        }

    async def test_empty_inventory_is_a_success_state(self, client, monkeypatch):
        page = DpeInventoryPage(
            state="absent",
            context_ref=CONTEXT,
            total=0,
            distribution=DpeInventoryDistribution(),
            page_size=20,
            has_more=False,
            read_state="complete",
        )
        monkeypatch.setattr(
            "app.logements.router.load_dpe_inventory",
            AsyncMock(return_value=page),
        )

        response = await client.get(
            "/v1/bien/dpe/inventaire",
            params={"contexte": CONTEXT},
        )

        assert response.status_code == 200
        assert response.json()["state"] == "absent"
        assert response.json()["total"] == 0


class TestDocumentRoute:
    async def test_returns_allowlisted_document_without_envelope(
        self,
        client,
        monkeypatch,
    ):
        service = AsyncMock(return_value=_document())
        monkeypatch.setattr("app.logements.router.load_dpe_document", service)

        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": CONTEXT, "reference": DOCUMENT},
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["document_ref"] == DOCUMENT
        assert "donnees" not in response.json()
        assert service.await_args.kwargs == {
            "context_ref": CONTEXT,
            "document_ref": DOCUMENT,
        }

    async def test_cross_target_document_is_rejected_without_leaking_refs(
        self,
        client,
        monkeypatch,
    ):
        foreign = "opaque-document-from-another-address"
        monkeypatch.setattr(
            "app.logements.router.load_dpe_document",
            AsyncMock(side_effect=InvalidReferenceError()),
        )

        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": CONTEXT, "reference": foreign},
        )

        assert response.status_code == 400
        assert response.headers["cache-control"] == "no-store"
        assert CONTEXT not in response.text
        assert foreign not in response.text


class TestErrors:
    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (InvalidReferenceError(), 400),
            (RefreshRequiredError(), 409),
            (RuntimeError("secret-sql-and-token"), 503),
        ],
    )
    async def test_service_errors_are_distinct_constant_and_no_store(
        self,
        client,
        monkeypatch,
        caplog,
        error,
        status,
    ):
        caplog.set_level(logging.WARNING)
        monkeypatch.setattr(
            "app.logements.router.load_dpe_document",
            AsyncMock(side_effect=error),
        )

        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": CONTEXT, "reference": DOCUMENT},
        )

        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"].startswith("application/problem+json")
        public_text = response.text + caplog.text
        assert CONTEXT not in public_text
        assert DOCUMENT not in public_text
        assert "secret-sql-and-token" not in public_text

    async def test_validation_is_bounded_constant_and_no_store(
        self,
        client,
        monkeypatch,
    ):
        service = AsyncMock()
        monkeypatch.setattr("app.logements.router.load_dpe_inventory", service)
        sentinel = "sensitive-reference-" + ("x" * 600)

        response = await client.get(
            "/v1/bien/dpe/inventaire",
            params={"contexte": sentinel},
        )

        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"].startswith("application/problem+json")
        assert sentinel not in response.text
        assert "input" not in response.json()
        service.assert_not_awaited()

    async def test_missing_context_is_422_and_no_store(self, client):
        response = await client.get("/v1/bien/dpe/inventaire")
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"


def test_openapi_exposes_only_fixed_opaque_navigation_contract():
    schema = app.openapi()
    inventory = schema["paths"]["/v1/bien/dpe/inventaire"]["get"]
    document = schema["paths"]["/v1/bien/dpe/document"]["get"]
    inventory_params = {
        parameter["name"]: parameter for parameter in inventory["parameters"]
    }

    assert set(inventory_params) == {"contexte", "curseur", "limite"}
    assert inventory_params["contexte"]["required"] is True
    assert {"400", "409", "422", "503"} <= set(inventory["responses"])
    assert {"400", "409", "422", "503"} <= set(document["responses"])
    for operation in (inventory, document):
        for status in ("200", "400", "409", "422", "503"):
            assert "Cache-Control" in operation["responses"][status]["headers"]
        for status in ("400", "409", "422", "503"):
            content = operation["responses"][status]["content"]
            assert set(content) == {"application/problem+json"}
            assert (
                content["application/problem+json"]["schema"]["additionalProperties"]
                is False
            )


def test_problem_detail_rejects_unpublished_fields():
    with pytest.raises(ValidationError):
        ProblemDetail.model_validate(
            {
                "title": "Erreur",
                "status": 400,
                "private_sentinel": "must-not-cross-the-boundary",
            }
        )
