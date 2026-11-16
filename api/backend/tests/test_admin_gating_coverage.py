"""Regression coverage for admin route gating.

The admin gate is applied per endpoint through AdminUser. This file
introspects FastAPI routes so future admin endpoints are covered
automatically instead of relying on a hand-maintained sample list.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.admin.dependencies import require_admin
from app.auth.dependencies import check_quota_hybrid, get_current_user
from app.auth.models import ApiKeyInfo, UserInfo
from app.database import get_db
from app.main import app
from app.rate_limit import _check_rate
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE

pytestmark = pytest.mark.unit


_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_NON_ADMIN_ID = 99
_ADMIN_FORBIDDEN_DETAIL = "Droits administrateur requis"


@dataclass(frozen=True)
class AdminRouteCase:
    method: str
    route_path: str
    request_path: str
    json_body: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        return f"{self.method} {self.route_path}"


def _make_user(uid: int = _NON_ADMIN_ID, is_admin: bool = False) -> UserInfo:
    return UserInfo(
        id=uid,
        email=f"user{uid}@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=is_admin,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _fake_key_info() -> ApiKeyInfo:
    return ApiKeyInfo(
        id=1,
        name="test-key",
        quota_monthly=999999,
        revoked=False,
        created_at=_NOW,
        user_id=_NON_ADMIN_ID,
    )


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=[])
    return db


def _walk_dependants(dependant) -> Iterable:
    yield dependant
    for child in getattr(dependant, "dependencies", []) or []:
        yield from _walk_dependants(child)


def _route_has_dependency(route: APIRoute, dependency) -> bool:
    return any(dep.call is dependency for dep in _walk_dependants(route.dependant))


def _admin_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/v1/admin")
    ]


def _validation_reports_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/v1/validation/reports"
    ]


_BODY_BY_ROUTE: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/v1/admin/api-keys/{key_id}/revoke"): {
        "reason": "admin coverage regression test",
    },
    ("PATCH", "/v1/admin/api-keys/{key_id}/quota"): {
        "quota_monthly": 1000,
        "reason": "admin coverage regression test",
    },
    ("POST", "/v1/admin/etl/{pipeline_name}/trigger"): {
        "reason": "admin coverage regression test",
    },
    ("POST", "/v1/admin/subscriptions/{sub_id}/sync"): {
        "reason": "admin coverage regression test",
    },
    ("POST", "/v1/admin/users/bulk"): {
        "action": "reset_quota",
        "user_ids": [1],
        "reason": "admin coverage regression test",
    },
    ("PATCH", "/v1/admin/users/{user_id}/admin"): {
        "is_admin": True,
        "reason": "admin coverage regression test",
    },
    ("POST", "/v1/admin/users/{user_id}/grant-plan"): {
        "plan": "pro",
        "reason": "admin coverage regression test",
        "end_date": None,
    },
    ("POST", "/v1/admin/users/{user_id}/resend-verification"): {
        "reason": "admin coverage regression test",
    },
    ("POST", "/v1/admin/users/{user_id}/reset-quota"): {
        "reason": "admin coverage regression test",
    },
    ("POST", "/v1/admin/users/{user_id}/revoke-keys"): {
        "reason": "admin coverage regression test",
    },
}


def _request_path(route_path: str) -> str:
    path = route_path.replace("{pipeline_name}", "dpe")
    return re.sub(r"{[^}/]+}", "1", path)


def _admin_route_cases() -> list[AdminRouteCase]:
    cases: list[AdminRouteCase] = []
    for route in _admin_routes():
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            cases.append(  # noqa: PERF401
                AdminRouteCase(
                    method=method,
                    route_path=route.path,
                    request_path=_request_path(route.path),
                    json_body=_BODY_BY_ROUTE.get((method, route.path)),
                )
            )
    return sorted(cases, key=lambda case: case.id)


ADMIN_ROUTE_CASES = _admin_route_cases()


@pytest.fixture()
async def non_admin_client():
    user = _make_user(is_admin=False)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
async def non_admin_dataset_client():
    user = _make_user(is_admin=False)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _mock_db
    app.dependency_overrides[check_quota_hybrid] = _fake_key_info
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def test_all_admin_routes_are_declared_with_require_admin():
    """Every /v1/admin route must carry the AdminUser dependency chain."""
    routes = _admin_routes()
    assert len(routes) >= 22

    missing = [
        f"{','.join(sorted(route.methods - {'HEAD', 'OPTIONS'}))} {route.path}"
        for route in routes
        if not _route_has_dependency(route, require_admin)
    ]
    assert missing == []


def test_validation_reports_surface_is_jwt_auth_with_manual_admin_check():
    """The validation report surface is guarded manually, not by AdminUser."""
    routes = _validation_reports_routes()
    assert len(routes) == 1
    route = routes[0]

    assert _route_has_dependency(route, get_current_user)
    assert not _route_has_dependency(route, require_admin)


def test_datasets_route_stays_read_only_and_not_admin_gated():
    """GET /v1/datasets is protected by quota/auth, not by admin gating."""
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/v1/datasets"
    ]
    assert len(routes) == 1
    route = routes[0]

    assert route.methods - {"HEAD", "OPTIONS"} == {"GET"}
    assert not _route_has_dependency(route, require_admin)


@pytest.mark.parametrize(
    "case",
    ADMIN_ROUTE_CASES,
    ids=[case.id for case in ADMIN_ROUTE_CASES],
)
async def test_non_admin_gets_403_on_every_admin_route(
    non_admin_client: AsyncClient,
    case: AdminRouteCase,
):
    kwargs = {}
    if case.json_body is not None:
        kwargs["json"] = case.json_body

    resp = await non_admin_client.request(case.method, case.request_path, **kwargs)

    assert resp.status_code == 403
    assert _ADMIN_FORBIDDEN_DETAIL in resp.json().get("detail", "")


async def test_validation_reports_manual_gate_returns_403_for_non_admin(
    non_admin_client: AsyncClient,
):
    resp = await non_admin_client.get("/v1/validation/reports")

    assert resp.status_code == 403
    assert "Admin requis" in resp.json().get("detail", "")


async def test_datasets_returns_200_for_authenticated_non_admin(
    non_admin_dataset_client: AsyncClient,
):
    resp = await non_admin_dataset_client.get(
        "/v1/datasets",
        headers={"X-API-Key": "dc_test_key"},
    )

    assert resp.status_code == 200


async def test_non_admin_admin_gate_precedes_rate_limit(
    non_admin_client: AsyncClient,
):
    bucket = f"admin:{_NON_ADMIN_ID}"
    for _ in range(100):
        _check_rate(bucket, 50)

    resp = await non_admin_client.get("/v1/admin/users")

    assert resp.status_code == 403
    assert resp.status_code != 429
    assert _ADMIN_FORBIDDEN_DETAIL in resp.json().get("detail", "")
