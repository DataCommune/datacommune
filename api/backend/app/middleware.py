"""Request logging middleware -- logs method, route, status, duration, client IP.

The logged ``path`` is the route template (``/v1/adresse/{ban_id}``), never
the real path: a BAN key or a DPE number next to the client IP would make
the log a personal record of the addresses a visitor looks up (decision
n° 26 of Ianis, 2026-10-05, API WR-03). A request matching no route is
logged as ``<unmatched>``.

Also persists each request to the ``api_request_log`` table via a
fire_and_forget background task (best-effort, never blocks the response).
Failures are logged at WARNING level via the shared helper's done-callback.
"""

import hmac as _hmac
import ipaddress
import logging
import time

from sqlalchemy import text
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.async_helpers import fire_and_forget
from app.config import settings

logger = logging.getLogger(__name__)

# Paths that should not be logged to the DB
_SKIP_LOG_PREFIXES = ("/v1/health", "/admin", "/static", "/app")
# Logged in place of the path of a request that matched no route.
UNMATCHED_ROUTE = "<unmatched>"


def route_template(request: Request) -> str:
    """Template of the matched route; never the real path (decision n° 26)."""
    route = request.scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) else UNMATCHED_ROUTE


def _get_client_ip(request: Request) -> str:
    """Return the peer IP, or a validated forwarded IP behind a trusted proxy."""
    peer_ip = request.client.host if request.client else "unknown"
    if not settings.trust_proxy_headers:
        return peer_ip

    candidates = [
        request.headers.get("cf-connecting-ip"),
        (request.headers.get("x-forwarded-for") or "").split(",")[0].strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return peer_ip


def cle_visiteur(client_ip: str) -> str:
    """Bucket identity of a visitor: the address, or its /64 for IPv6.

    A visitor owns a whole /64 (or more): counting each address would let one
    machine change address to escape its bucket. An IPv4-mapped IPv6 address is
    counted as the IPv4 it carries. Anything unparsable is kept as it is.
    """
    try:
        adresse = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    if isinstance(adresse, ipaddress.IPv6Address):
        if adresse.ipv4_mapped is not None:
            return str(adresse.ipv4_mapped)
        return str(ipaddress.ip_network(f"{adresse}/64", strict=False))
    return str(adresse)


NO_INDEX_HEADER = ("X-Robots-Tag", "noindex, nofollow")


class NoIndexMiddleware:
    """Mark every API response noindex, nofollow (KI-P12).

    The API has no page to rank, and DVF answers must not be indexed (DVF
    reuse conditions). Pure ASGI and outermost, so the header also reaches
    rate-limit refusals, CORS preflights and error responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[NO_INDEX_HEADER[0]] = NO_INDEX_HEADER[1]
            await send(message)

        await self.app(scope, receive, send_with_header)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status code, duration and client IP."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        path = request.url.path
        route = route_template(request)
        client_ip = _get_client_ip(request)

        # Extract api_key_id set by check_quota dependency (if present)
        api_key_id = getattr(request.state, "api_key_id", None)

        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": route,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 1),
                "client_ip": client_ip,
            },
        )

        # Best-effort DB log -- skip admin, static, health, app, and smoke tests
        # SEC-05: validate smoke header with timing-safe comparison.
        # If smoke_test_api_key is empty (default), the header has no effect.
        _smoke_secret = settings.smoke_test_api_key
        _smoke_header = request.headers.get("x-smoke-test")
        is_smoke = bool(
            _smoke_secret
            and _smoke_header
            and _hmac.compare_digest(_smoke_header, _smoke_secret)
        )
        skip_log = path.startswith(_SKIP_LOG_PREFIXES) or is_smoke
        if not skip_log:
            fire_and_forget(
                self._log_to_db(
                    request.method,
                    route,
                    response.status_code,
                    duration_ms,
                    client_ip,
                    api_key_id,
                ),
                task_name="request_log_to_db",
            )

        return response

    @staticmethod
    async def _log_to_db(
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client_ip: str,
        api_key_id: int | None = None,
    ) -> None:
        """Persist a single API request record (best-effort, silent on failure)."""
        try:
            from app.database import engine  # deferred to avoid circular import

            async with engine.connect() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO api_request_log "
                        "(method, path, status_code, duration_ms, "
                        "client_ip, api_key_id) "
                        "VALUES (:m, :p, :s, :d, :ip, :kid)"
                    ),
                    {
                        "m": method[:10],
                        "p": path[:200],
                        "s": status_code,
                        "d": round(duration_ms, 1),
                        "ip": client_ip,
                        "kid": api_key_id,
                    },
                )
                await conn.commit()
        except Exception:  # noqa: BLE001
            logger.warning("request_log_failed", exc_info=True)
