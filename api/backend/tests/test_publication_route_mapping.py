"""Every mounted publication dependency must resolve to an explicit dataset."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

from app.common import publication_readiness as readiness
from app.main import app

pytestmark = pytest.mark.unit


def _has_guard(dependant):
    return dependant.call is readiness.require_publication or any(
        _has_guard(child) for child in dependant.dependencies
    )


GUARDED_PATHS = sorted(
    route.path
    for route in app.routes
    if hasattr(route, "dependant") and _has_guard(route.dependant)
)


@pytest.mark.parametrize("path", GUARDED_PATHS)
async def test_every_effectively_guarded_route_has_explicit_mapping(path, monkeypatch):
    guard = AsyncMock()
    monkeypatch.setattr(readiness, "guard_publication", guard)
    request = Request(
        {"type": "http", "headers": [], "path": path, "query_string": b"datasets=dpe"}
    )
    session = object()
    await readiness.require_publication(request, session)
    guard.assert_awaited_once()
    assert guard.call_args.args[0] is session
    datasets = guard.call_args.args[1]
    assert datasets and set(datasets) <= readiness.PUBLICATION_DATASETS


def test_route_inventory_includes_both_documentary_routes():
    assert "/v1/bien/dpe/inventaire" in GUARDED_PATHS
    assert "/v1/bien/dpe/document" in GUARDED_PATHS


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/bien/logements", {"dpe", "dvf"}),
        ("/v1/bien/dpe/inventaire", {"dpe"}),
        ("/v1/bien/dpe/document", {"dpe"}),
    ],
)
async def test_bien_route_guards_every_journalled_source_it_reads(
    path, expected, monkeypatch
):
    guard = AsyncMock()
    monkeypatch.setattr(readiness, "guard_publication", guard)
    request = Request(
        {"type": "http", "headers": [], "path": path, "query_string": b""}
    )
    await readiness.require_publication(request, object())
    assert set(guard.call_args.args[1]) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/v1/bien/other",
        "/v1/bien/dpe/other",
        "/v1/bien/dpe/document/other",
        "/v1/bien/dpe/inventaire-extra",
    ],
)
async def test_unknown_bien_paths_cannot_inherit_dpe_admission(path, monkeypatch):
    guard = AsyncMock()
    monkeypatch.setattr(readiness, "guard_publication", guard)
    request = Request(
        {"type": "http", "headers": [], "path": path, "query_string": b""}
    )
    with pytest.raises(HTTPException) as error:
        await readiness.require_publication(request, object())
    assert error.value.status_code == 503
    guard.assert_not_awaited()
