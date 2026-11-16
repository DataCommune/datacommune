"""C36 (Ianis, 2026-10-10): a visitor of the public key may make 120 requests a minute.

Both ceilings a session of the public acceptance key meets are played with their
real defaults (never monkeypatched): the per-IP ceiling of RateLimitMiddleware,
then the per-visitor-IP allowance `_enforce_quota` gives every dc_anon_ key.
Only the database is replaced.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth import dependencies
from app.auth.models import ApiKeyInfo
from app.common.exceptions import ErreurApiError
from app.rate_limit import RateLimitMiddleware
from scripts.provision_synthetic_probe_key import RECETTE_QUOTA

pytestmark = pytest.mark.unit

PUBLIC_CEILING_PER_MINUTE = 120
_PUBLIC_KEY = "dc_anon_" + "c" * 43


def _public_key_app() -> Starlette:
    """One route behind the real middleware, admitting the acceptance key."""
    key = ApiKeyInfo(
        id=36,
        name="Owner acceptance public key (03-13)",
        quota_monthly=RECETTE_QUOTA,
        user_id=None,
        key_prefix=_PUBLIC_KEY[:16] + "...",
        revoked=False,
        created_at=datetime.now(UTC),
    )

    async def _read(request: Request) -> JSONResponse:
        try:
            await dependencies._enforce_quota(request, key, AsyncMock())
        except ErreurApiError as exc:
            return JSONResponse({"title": exc.message}, status_code=exc.code)
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/v1/communes/search", _read)])
    app.add_middleware(RateLimitMiddleware)
    return app


@pytest.mark.asyncio
async def test_public_key_session_gets_120_requests_a_minute_and_the_121st_is_refused(
    monkeypatch,
):
    monkeypatch.setattr(dependencies, "reserve_quota", AsyncMock(return_value=1))
    transport = ASGITransport(app=_public_key_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        statuses = []
        for _ in range(PUBLIC_CEILING_PER_MINUTE):
            response = await client.get(
                "/v1/communes/search?q=nantes", headers={"X-API-Key": _PUBLIC_KEY}
            )
            statuses.append(response.status_code)
        accepted = statuses.count(200)
        assert accepted == PUBLIC_CEILING_PER_MINUTE, (
            f"first refusal at request {statuses.index(429) + 1}"
            if 429 in statuses
            else statuses
        )
        assert response.headers["X-RateLimit-Limit"] == str(PUBLIC_CEILING_PER_MINUTE)
        assert response.headers["X-RateLimit-Remaining"] == "0"

        refused = await client.get(
            "/v1/communes/search?q=nantes", headers={"X-API-Key": _PUBLIC_KEY}
        )
        assert refused.status_code == 429
        assert refused.headers["X-RateLimit-Limit"] == str(PUBLIC_CEILING_PER_MINUTE)
        assert int(refused.headers["Retry-After"]) > 0
