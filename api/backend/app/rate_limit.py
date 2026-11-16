"""In-memory rate limiting middleware for public API protection.

Uses a sliding window counter per bucket (IP or API key).
No external dependency (no Redis) — suitable for single-instance deployments.
Thread-safe via threading.Lock for concurrent access to shared state.
"""

import logging
import secrets
import threading
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.service import hash_api_key
from app.common.exceptions import ErreurApiError
from app.config import settings
from app.mcp.canal import est_appel_mcp
from app.middleware import _get_client_ip

logger = logging.getLogger(__name__)

# Sliding window: track request timestamps per bucket
_buckets: dict[str, list[float]] = defaultdict(list)
# Window each bucket was last checked with, so the purge keeps a bucket exactly
# as long as its own window is open (a per-minute bucket is not held for a day).
_bucket_windows: dict[str, int] = {}
_last_cleanup: float = 0.0
_lock = threading.Lock()
_WINDOW_SECONDS = 60
_CLEANUP_INTERVAL = 300  # purge stale buckets every 5 min
# Longest window any caller may ask for: one day, for the per-IP daily ceiling
# of the public anonymous key (auth/dependencies.py). The periodic purge never
# drops a bucket whose own window is still open: doing so would silently reset
# an hourly or daily counter and hand back an unlimited allowance.
_MAX_WINDOW_SECONDS = 86_400


def _is_internal_unlimited_key(request: Request) -> bool:
    """Match the configured key only on data routes that revalidate it in DB."""
    path = request.url.path
    if not path.startswith("/v1/") or path.startswith(
        ("/v1/admin", "/v1/health", "/v1/validation")
    ):
        return False
    expected_hash = settings.internal_unlimited_key_hash
    candidate = request.headers.get("X-API-Key", "")
    if not expected_hash or not candidate:
        return False
    return secrets.compare_digest(hash_api_key(candidate), expected_hash)


def _check_rate(
    bucket: str, limit: int, window_seconds: int = _WINDOW_SECONDS
) -> tuple[bool, int, int]:
    """Check and record a request for the given bucket.

    Returns (allowed, remaining, reset_seconds). `window_seconds` defaults to the
    per-minute window used by the global middleware; per-route callers may ask for
    a longer one (see the AB-01 email routes in auth/router.py).
    Thread-safe: all access to _buckets/_last_cleanup is under _lock.
    """
    global _last_cleanup

    if window_seconds > _MAX_WINDOW_SECONDS:
        raise ValueError(
            f"window_seconds {window_seconds} exceeds _MAX_WINDOW_SECONDS "
            f"{_MAX_WINDOW_SECONDS}; raise the purge horizon first"
        )

    with _lock:
        now = time.monotonic()

        # Periodic cleanup of stale buckets, each over its own window so a bucket
        # with an open hourly or daily window is never purged out from under
        # itself. A bucket of unknown window is kept for the longest one.
        if now - _last_cleanup > _CLEANUP_INTERVAL:
            _last_cleanup = now
            stale = [
                k
                for k, v in _buckets.items()
                if not v or v[-1] < now - _bucket_windows.get(k, _MAX_WINDOW_SECONDS)
            ]
            for k in stale:
                del _buckets[k]
                _bucket_windows.pop(k, None)

        _bucket_windows[bucket] = max(window_seconds, _bucket_windows.get(bucket, 0))

        # Trim expired timestamps from this bucket
        window_start = now - window_seconds
        timestamps = _buckets[bucket]
        while timestamps and timestamps[0] < window_start:
            timestamps.pop(0)

        if len(timestamps) >= limit:
            reset = int(timestamps[0] + window_seconds - now) + 1
            return False, 0, reset

        timestamps.append(now)
        remaining = limit - len(timestamps)
        return True, remaining, window_seconds


# One bucket per IP for every exposed authentication route (C45).
AUTH_BUCKET_PREFIX = "auth:ip:"


async def limite_authentification(request: Request) -> None:
    """Route dependency of the exposed authentication routes (C45).

    10 requests a minute per IP (``rate_limit_auth_per_ip``), one bucket shared
    by login, account creation, e-mail verification, password reset, password
    change and key by e-mail, on top of the navigation ceiling of the
    middleware (C36). Declared on the route, it runs before the body is
    validated: an invalid payload counts as much as a password guess. The
    stricter per-route limits of the e-mail routes (AB-01) stay as they are.
    """
    client_ip = _get_client_ip(request)
    allowed, _, reset = _check_rate(
        f"{AUTH_BUCKET_PREFIX}{client_ip}", settings.rate_limit_auth_per_ip
    )
    if not allowed:
        logger.warning("auth_rate_limited", extra={"client_ip": client_ip})
        raise ErreurApiError(
            429,
            "Trop de demandes",
            "Reessayez dans quelques minutes.",
            headers={"Cache-Control": "no-store", "Retry-After": str(reset)},
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply per-IP and per-API-key rate limits."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting for health, admin, static, and docs
        if request.url.path.startswith(
            (
                "/v1/health",
                # Map images have their own three buckets (app/carte): a page
                # of several images must not spend the navigation ceiling of
                # the data calls behind the same NAT.
                "/v1/carte/",
                "/admin",
                "/static",
                "/docs",
                "/redoc",
                "/openapi.json",
                "/stripe",
                "/app",
            )
        ):
            return await call_next(request)

        # Only the exact configured HMAC digest on /v1 data routes bypasses the
        # infrastructure ceiling. Those route dependencies validate the key in
        # the database and require an active internal plan owned by a verified admin.
        if _is_internal_unlimited_key(request):
            return await call_next(request)

        # An MCP tool call replaying /v1 in process (D-143): its POST /mcp was
        # already counted here; the visitor's own buckets count the read in
        # check_quota_hybrid. The mark lives in the ASGI scope, out of reach of
        # a network request.
        if est_appel_mcp(request):
            return await call_next(request)

        client_ip = _get_client_ip(request)

        # D-11: Enforce the per-IP ceiling even when an arbitrary X-API-Key
        # header is present (except the exact HMAC-allowlisted internal key
        # handled above). This middleware runs before auth (no DB
        # session), so it cannot tell a valid key from a garbage one -- a
        # header-presence check used to route ANY non-empty key value to a
        # private key: bucket at a generous per-key limit,
        # letting a spray of distinct fake keys from one IP bypass the IP
        # ceiling entirely. The real per-validated-key allowance is enforced
        # downstream, after the key is resolved against the DB, in
        # auth/dependencies.py::_enforce_quota (`plan:{key_info.id}` bucket, or
        # per visitor IP for the public dc_anon_ key shared by every visitor).
        bucket = f"ip:{client_ip}"
        limit = settings.rate_limit_per_ip

        allowed, remaining, reset = _check_rate(bucket, limit)

        if not allowed:
            logger.warning(
                "rate_limited",
                extra={"bucket": bucket, "client_ip": client_ip},
            )
            return JSONResponse(
                status_code=429,
                content={
                    "type": "about:blank",
                    "title": "Trop de requetes",
                    "status": 429,
                    "detail": (
                        f"Limite: {limit} requetes/{_WINDOW_SECONDS}s. "
                        f"Reessayez dans {reset}s."
                    ),
                },
                headers={
                    # This response bypasses route-level privacy headers.
                    "Cache-Control": "no-store",
                    "Retry-After": str(reset),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset),
                },
                media_type="application/problem+json",
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)
        return response
