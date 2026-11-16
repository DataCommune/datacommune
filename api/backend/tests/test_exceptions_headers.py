"""Unit test: ErreurApiError.headers survives RFC 9457 rendering (ADR-0004 D4).

The proxy error contract attaches Retry-After to a 503 via ErreurApiError; the
handler must emit that header on the problem+json response.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.exceptions import RequestValidationError

from app.common.exceptions import (
    ErreurApiError,
    _handle_exception_api,
    _handle_validation_error,
)

pytestmark = pytest.mark.unit


async def test_handler_emits_attached_headers():
    request = MagicMock()
    request.url.path = "/v1/entreprises"
    exc = ErreurApiError(
        503,
        "Service Sirene temporairement indisponible.",
        headers={"Retry-After": "60"},
    )

    resp = await _handle_exception_api(request, exc)

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "60"
    assert "application/problem+json" in resp.headers["content-type"]


async def test_handler_without_headers_is_unchanged():
    request = MagicMock()
    request.url.path = "/x"
    exc = ErreurApiError(500, "Erreur interne du serveur")

    resp = await _handle_exception_api(request, exc)

    assert resp.status_code == 500
    assert "retry-after" not in resp.headers


def _validation_request(route: str) -> MagicMock:
    request = MagicMock()
    request.url.path = route
    request.scope = {"route": SimpleNamespace(path=route)}
    return request


@pytest.mark.parametrize(
    "route",
    [
        "/v1/adresses/recherche",
        "/v1/adresse/{ban_id}",
        "/v1/adresse/{ban_id}/ventes-ressemblantes",
    ],
)
async def test_validation_error_of_an_address_route_is_never_stored(route):
    """Review of 261007-crf WR-06: as every other problem of these routes."""
    resp = await _handle_validation_error(
        _validation_request(route), RequestValidationError([])
    )

    assert resp.status_code == 422
    assert resp.headers["cache-control"] == "no-store"


async def test_validation_error_elsewhere_keeps_its_headers():
    resp = await _handle_validation_error(
        _validation_request("/v1/communes"), RequestValidationError([])
    )

    assert resp.status_code == 422
    assert "cache-control" not in resp.headers
