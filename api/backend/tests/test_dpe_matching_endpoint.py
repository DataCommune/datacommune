"""The comparison route keeps inventory trust boundaries and never attributes."""

import logging
from datetime import date
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import check_quota_hybrid
from app.common.exceptions import ErreurApiError
from app.dpe.references import InvalidReferenceError, RefreshRequiredError
from app.dpe.schemas import DpeInventoryDistribution, DpeInventoryItem, DpeInventoryPage
from app.main import app

pytestmark = pytest.mark.smoke
PATH = "/v1/bien/dpe/comparaison"
SECRET = "PRIVATE-SENTINEL-MATCHING"


def page():
    return DpeInventoryPage(
        state="ready",
        context_ref="opaque-context",
        items=[
            DpeInventoryItem(
                document_ref="opaque-one", type_batiment="maison", surface_habitable=80
            ),
            DpeInventoryItem(document_ref="opaque-two", type_batiment=None),
        ],
        total=7,
        distribution=DpeInventoryDistribution(unknown=7),
        page_size=2,
        has_more=True,
        next_cursor="opaque-next",
        read_state="complete",
    )


@pytest.mark.parametrize(
    "target",
    [
        {},
        {"type_bien": "maison"},
        {"surface": {"value": 80, "nature": "habitable_dpe"}},
    ],
)
async def test_compares_all_page_items_without_filtering(client, monkeypatch, target):
    source = page()
    loader = AsyncMock(return_value=source)
    monkeypatch.setattr("app.logements.router.load_dpe_inventory", loader)
    response = await client.post(
        PATH,
        json={
            "contexte": "opaque-context",
            "curseur": "opaque-previous",
            "limite": 2,
            "cible": target,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["inventory"] == source.model_dump(mode="json")
    assert body["attribution"]["status"] == "unassigned"
    assert body["attribution"]["diagnostic"] is None
    assert [item["document_ref"] for item in body["comparisons"]] == [
        "opaque-one",
        "opaque-two",
    ]
    assert body["rule_version"]
    assert response.headers["cache-control"] == "no-store"
    assert str(response.request.url).endswith(PATH)
    loader.assert_awaited_once()
    assert loader.await_args.kwargs == {
        "context_ref": "opaque-context",
        "cursor": "opaque-previous",
        "limit": 2,
    }


async def test_default_empty_target_and_standard_page_bound(client, monkeypatch):
    loader = AsyncMock(return_value=page())
    monkeypatch.setattr("app.logements.router.load_dpe_inventory", loader)
    response = await client.post(PATH, json={"contexte": "opaque-context"})
    assert response.status_code == 200
    assert loader.await_args.kwargs == {
        "context_ref": "opaque-context",
        "cursor": None,
        "limit": 20,
    }


async def test_midnight_boundary_requires_refresh_after_comparison(client, monkeypatch):
    source = page().model_copy(update={"as_of_date": date(2026, 9, 8)})
    monkeypatch.setattr(
        "app.logements.router.load_dpe_inventory", AsyncMock(return_value=source)
    )
    monkeypatch.setattr("app.dpe.references.paris_today", lambda: date(2026, 9, 9))
    response = await client.post(PATH, json={"contexte": "opaque-context"})
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert "comparisons" not in response.json()


async def test_route_requires_real_authentication():
    app.dependency_overrides.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(PATH, json={"contexte": "opaque-context"})
    assert response.status_code in {401, 403}
    assert response.headers["cache-control"] == "no-store"


async def test_quota_is_not_bypassed(client):
    async def exhausted():
        raise ErreurApiError(429, "Quota épuisé", headers={"Retry-After": "60"})

    app.dependency_overrides[check_quota_hybrid] = exhausted
    response = await client.post(PATH, json={"contexte": "opaque-context"})
    assert response.status_code == 429
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", PATH),
        ("GET", "/v1/bien/dpe/inventaire"),
        ("GET", "/v1/bien/dpe/document"),
    ],
)
async def test_ip_rate_limit_before_routing_is_also_no_store(
    client, monkeypatch, method, path
):
    monkeypatch.setattr("app.rate_limit._check_rate", lambda *_: (False, 0, 60))
    loader = AsyncMock()
    monkeypatch.setattr("app.logements.router.load_dpe_inventory", loader)
    response = await client.request(method, path)
    assert response.status_code == 429
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers["retry-after"] == "60"
    assert response.headers["x-ratelimit-remaining"] == "0"
    loader.assert_not_awaited()


@pytest.mark.parametrize(
    "patch",
    [
        {"contexte": ""},
        {"contexte": SECRET * 100},
        {"contexte": 12},
        {"limite": 0},
        {"limite": 51},
        {"limite": True},
        {"limite": "20"},
        {SECRET: SECRET},
        {"cible": {SECRET: SECRET}},
        {"cible": {"porte": SECRET}},
        {"cible": {"surface": {"value": "80", "nature": "habitable_dpe"}}},
        {"cible": {"surface": {"value": True, "nature": "habitable_dpe"}}},
        {"cible": {"surface": {"value": -1, "nature": "habitable_dpe"}}},
        {"cible": {"surface": {"value": 80}}},
    ],
)
async def test_invalid_inputs_are_constant_private_errors(
    client, monkeypatch, caplog, patch
):
    loader = AsyncMock(return_value=page())
    monkeypatch.setattr("app.logements.router.load_dpe_inventory", loader)
    with caplog.at_level(logging.WARNING):
        response = await client.post(PATH, json={"contexte": "opaque-context", **patch})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert SECRET not in response.text + caplog.text
    assert "input" not in response.json()
    loader.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (InvalidReferenceError(), 400),
        (RefreshRequiredError(), 409),
        (RuntimeError(SECRET), 503),
    ],
)
async def test_reference_generation_and_failure_do_not_become_absence(
    client, monkeypatch, caplog, error, status
):
    monkeypatch.setattr(
        "app.logements.router.load_dpe_inventory", AsyncMock(side_effect=error)
    )
    with caplog.at_level(logging.WARNING):
        response = await client.post(PATH, json={"contexte": "opaque-context"})
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert SECRET not in response.text + caplog.text
    assert "attribution" not in response.json()
