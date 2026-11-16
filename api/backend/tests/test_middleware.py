"""Unit tests for client IP extraction and trusted proxy handling."""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import app.rate_limit as rate_limit_mod
from app.config import settings
from app.middleware import _get_client_ip
from app.rate_limit import RateLimitMiddleware


def _request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": raw_headers,
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_forwarded_headers_are_ignored_by_default(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    request = _request(
        {
            "cf-connecting-ip": "203.0.113.10",
            "x-forwarded-for": "198.51.100.20",
        }
    )

    assert _get_client_ip(request) == "127.0.0.1"


def test_cloudflare_header_is_used_behind_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    request = _request({"cf-connecting-ip": "203.0.113.10"})

    assert _get_client_ip(request) == "203.0.113.10"


def test_invalid_forwarded_ip_falls_back_to_peer(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    request = _request(
        {
            "cf-connecting-ip": "not-an-ip",
            "x-forwarded-for": "also-invalid, 203.0.113.10",
        }
    )

    assert _get_client_ip(request) == "127.0.0.1"


# ---------------------------------------------------------------------------
# D-11: bucket-selection hardening -- header presence must not bypass the
# per-IP ceiling. See app/rate_limit.py::RateLimitMiddleware.dispatch.
# ---------------------------------------------------------------------------


def _build_probe_app() -> Starlette:
    """Minimal Starlette app wrapping ONLY RateLimitMiddleware -- isolated
    from the full FastAPI app (no DB, no auth) so tests drive the
    bucket-selection decision directly.
    """

    async def _ok(request):
        return PlainTextResponse("ok")

    probe_app = Starlette(
        routes=[
            Route("/probe", _ok),
            Route("/v1/probe", _ok),
            Route("/v1/validation/probe", _ok),
            Route("/auth/login", _ok),
        ]
    )
    probe_app.add_middleware(RateLimitMiddleware)
    return probe_app


@pytest.mark.asyncio
async def test_distinct_garbage_keys_from_one_ip_hit_the_ip_ceiling(monkeypatch):
    """D-11 regression: N+1 requests carrying DISTINCT garbage X-API-Key
    values from the SAME client IP must be capped by the shared ip: bucket
    once rate_limit_per_ip is exceeded -- the fake keys must NOT each get
    their own generous key: bucket (the pre-fix bypass).
    """
    monkeypatch.setattr(settings, "rate_limit_per_ip", 3)
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(3):
            resp = await client.get("/probe", headers={"X-API-Key": f"garbage-{i}"})
            assert resp.status_code == 200, (
                f"request {i} should still be under the IP ceiling"
            )

        # 4th request: yet another DISTINCT garbage key, same IP -> capped.
        resp = await client.get("/probe", headers={"X-API-Key": "garbage-final"})
        assert resp.status_code == 429

    rate_limit_mod._buckets.clear()


@pytest.mark.asyncio
async def test_unkeyed_request_under_limit_is_still_allowed(monkeypatch):
    """No regression: an unkeyed request from an IP under the limit is still
    allowed (the fix must not break the pre-existing unkeyed path).
    """
    monkeypatch.setattr(settings, "rate_limit_per_ip", 30)
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/probe")
        assert resp.status_code == 200

    rate_limit_mod._buckets.clear()


@pytest.mark.asyncio
async def test_exact_internal_key_hash_bypasses_ip_ceiling(monkeypatch):
    """Only the configured HMAC digest may bypass the infrastructure ceiling."""
    internal_key = "dc_live_internal-test-key"
    monkeypatch.setattr(settings, "rate_limit_per_ip", 1)
    monkeypatch.setattr(
        settings,
        "internal_unlimited_key_hash",
        rate_limit_mod.hash_api_key(internal_key),
    )
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/v1/probe", headers={"X-API-Key": internal_key})
        second = await client.get("/v1/probe", headers={"X-API-Key": internal_key})

    assert first.status_code == 200
    assert second.status_code == 200
    assert "X-RateLimit-Limit" not in second.headers
    rate_limit_mod._buckets.clear()


@pytest.mark.asyncio
async def test_internal_key_cannot_bypass_auth_route_ceiling(monkeypatch):
    internal_key = "dc_live_internal-test-key"
    monkeypatch.setattr(settings, "rate_limit_per_ip", 1)
    monkeypatch.setattr(
        settings,
        "internal_unlimited_key_hash",
        rate_limit_mod.hash_api_key(internal_key),
    )
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/auth/login", headers={"X-API-Key": internal_key})
        second = await client.get("/auth/login", headers={"X-API-Key": internal_key})

    assert first.status_code == 200
    assert second.status_code == 429
    rate_limit_mod._buckets.clear()


@pytest.mark.asyncio
async def test_internal_key_cannot_bypass_validation_route_ceiling(monkeypatch):
    internal_key = "dc_live_internal-test-key"
    monkeypatch.setattr(settings, "rate_limit_per_ip", 1)
    monkeypatch.setattr(
        settings,
        "internal_unlimited_key_hash",
        rate_limit_mod.hash_api_key(internal_key),
    )
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/v1/validation/probe", headers={"X-API-Key": internal_key}
        )
        second = await client.get(
            "/v1/validation/probe", headers={"X-API-Key": internal_key}
        )

    assert first.status_code == 200
    assert second.status_code == 429
    rate_limit_mod._buckets.clear()


# ---------------------------------------------------------------------------
# D-06 contract lock-in: deterministic 429, bounded Retry-After, per-IP
# isolation and window expiry via an INJECTED monotonic clock -- no wall-clock
# sleep and no network. These characterization tests pin the existing
# app/rate_limit.py behaviour so a regression flips them red.
# ---------------------------------------------------------------------------


class _FakeMonotonic:
    """Deterministic replacement for time.monotonic() -- advanced explicitly."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


@pytest.mark.asyncio
async def test_ip_bucket_sub_limit_then_at_limit_then_over_limit(monkeypatch):
    """D-06: requests pass under and at the ceiling, then 429 deterministically
    on the first request over it, with a bounded positive-integer Retry-After.
    """
    monkeypatch.setattr(settings, "rate_limit_per_ip", 3)
    clock = _FakeMonotonic()
    monkeypatch.setattr(rate_limit_mod.time, "monotonic", clock)
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # sub-limit + at-limit: the first 3 requests are allowed.
        for i in range(3):
            resp = await client.get("/probe")
            assert resp.status_code == 200
            assert resp.headers["X-RateLimit-Remaining"] == str(3 - (i + 1))

        # over-limit: the 4th request is blocked deterministically.
        blocked = await client.get("/probe")
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Remaining"] == "0"
        retry = int(blocked.headers["Retry-After"])
        assert 0 < retry <= rate_limit_mod._WINDOW_SECONDS + 1

    rate_limit_mod._buckets.clear()


@pytest.mark.asyncio
async def test_window_expiry_via_injected_clock_allows_again(monkeypatch):
    """D-06: advancing the injected monotonic clock past the sliding window
    resets the bucket -- proven WITHOUT any sleep(60).
    """
    monkeypatch.setattr(settings, "rate_limit_per_ip", 1)
    clock = _FakeMonotonic()
    monkeypatch.setattr(rate_limit_mod.time, "monotonic", clock)
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/probe")
        assert first.status_code == 200

        blocked = await client.get("/probe")
        assert blocked.status_code == 429

        # Jump past the window without sleeping.
        clock.value += rate_limit_mod._WINDOW_SECONDS + 1
        allowed_again = await client.get("/probe")
        assert allowed_again.status_code == 200

    rate_limit_mod._buckets.clear()


@pytest.mark.asyncio
async def test_distinct_ips_do_not_share_a_bucket(monkeypatch):
    """D-06: two identities (IPs) are isolated -- a saturated IP never consumes
    another IP's allowance.
    """
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "rate_limit_per_ip", 1)
    clock = _FakeMonotonic()
    monkeypatch.setattr(rate_limit_mod.time, "monotonic", clock)
    rate_limit_mod._buckets.clear()

    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        a1 = await client.get("/probe", headers={"cf-connecting-ip": "203.0.113.1"})
        a2 = await client.get("/probe", headers={"cf-connecting-ip": "203.0.113.1"})
        # A distinct IP still holds its full, independent allowance.
        b1 = await client.get("/probe", headers={"cf-connecting-ip": "203.0.113.2"})

    assert a1.status_code == 200
    assert a2.status_code == 429
    assert b1.status_code == 200
    rate_limit_mod._buckets.clear()


# ---------------------------------------------------------------------------
# API WR-03 (decision n° 26): the request log carries the route template,
# never the real path (a BAN key or a DPE number next to the client IP).
# ---------------------------------------------------------------------------

BAN_KEY = "85191_0420_00012"


def _logged_app(monkeypatch):
    from fastapi import APIRouter, FastAPI

    from app.middleware import RequestLoggingMiddleware

    recorded: list[tuple] = []

    def fake_log(*args, **_kwargs):
        recorded.append(args)

        async def noop():
            return None

        return noop()

    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", staticmethod(fake_log))
    router = APIRouter(prefix="/v1")

    @router.get("/adresse/{ban_id}")
    async def adresse(ban_id: str):
        return {"ok": True}

    @router.get("/dpe/{numero}")
    async def dpe(numero: str):
        return {"ok": True}

    @router.get("/health")
    async def health():
        return {"ok": True}

    application = FastAPI()
    application.include_router(router)
    application.add_middleware(RequestLoggingMiddleware)
    return application, recorded


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        (f"/v1/adresse/{BAN_KEY}", "/v1/adresse/{ban_id}"),
        ("/v1/dpe/2185E0000001A", "/v1/dpe/{numero}"),
        (f"/v1/inconnue/{BAN_KEY}", "<unmatched>"),
    ],
)
async def test_the_request_log_carries_the_route_template_only(
    monkeypatch, caplog, path, logged
):
    application, recorded = _logged_app(monkeypatch)
    caplog.set_level("INFO", logger="app.middleware")

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://t"
    ) as client:
        await client.get(path, headers={"x-forwarded-for": "203.0.113.10"})

    assert [args[1] for args in recorded] == [logged]
    record = next(r for r in caplog.records if r.getMessage() == "http_request")
    assert record.path == logged
    assert BAN_KEY not in caplog.text and "2185E0000001A" not in caplog.text
    assert BAN_KEY not in str(recorded) and "2185E0000001A" not in str(recorded)


async def test_skipped_prefixes_are_still_not_written(monkeypatch):
    application, recorded = _logged_app(monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://t"
    ) as client:
        await client.get("/v1/health")

    assert recorded == []
