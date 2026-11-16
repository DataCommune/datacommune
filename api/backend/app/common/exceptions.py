"""Custom exception classes and FastAPI exception handlers (RFC 9457)."""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.common import error_redaction
from app.common.schemas import ProblemDetail
from app.config import settings
from app.middleware import route_template

logger = logging.getLogger(__name__)


class UpstreamUnavailableError(Exception):
    """Upstream API unreachable — raised in proxy clients, NOT cached by @cached."""

    def __init__(self, service: str, reason: str = "") -> None:
        self.service = service
        self.reason = reason
        msg = (
            f"{service} indisponible: {reason}" if reason else f"{service} indisponible"
        )
        super().__init__(msg)


class ErreurApiError(Exception):
    """Custom API exception with HTTP status code and message.

    ``headers`` lets a caller attach response headers (e.g. ``Retry-After`` on a
    proxy 503) that survive the RFC 9457 rendering — used by proxy_error_boundary.
    ``type_code``: the constant ``code`` extension member of a typed problem
    (``ProblemeAdresse``), for a check that raises before the route body.
    """

    def __init__(
        self,
        code: int,
        message: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        type_code: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        self.headers = headers
        self.type_code = type_code
        super().__init__(message)


async def _handle_exception_api(request: Request, exc: ErreurApiError) -> JSONResponse:
    """Handle ErreurApiError with RFC 9457 ProblemDetail format."""
    body = ProblemDetail(
        title=exc.message,
        status=exc.code,
        detail=exc.detail,
        instance=_route_path(request),
    ).model_dump(exclude_none=True)
    if exc.type_code is not None:
        body["code"] = exc.type_code
    # A 401 of an address route is refused before its body: no-store joins the
    # headers of the error (C60), as for the 422 and the 500.
    headers = {**(exc.headers or {}), **(_validation_headers(request) or {})}
    return JSONResponse(
        status_code=exc.code,
        content=body,
        media_type="application/problem+json",
        headers=headers or None,
    )


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors with RFC 9457 format."""
    details = "; ".join(
        f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    body = ProblemDetail(
        title="Erreur de validation des donnees",
        status=422,
        detail=details,
        instance=_route_path(request),
    ).model_dump(exclude_none=True)
    return JSONResponse(
        status_code=422,
        content=body,
        media_type="application/problem+json",
        headers=_validation_headers(request),
    )


# Routes whose every problem carries no-store (app.adresse.router.NO_STORE):
# their validation errors too, so that no refusal is ever kept by a cache
# (review of 261007-crf, WR-06). Matched on the route template.
_NO_STORE_ROUTE_PREFIXES = ("/v1/adresse/", "/v1/adresses/", "/v1/carte/")


def _route_path(request: Request) -> str | None:
    """Template of the matched route, None without one; never the real path.

    The ``instance`` of every problem body (C59 I2, C60): the real path can
    carry a BAN key or an address typed as a key.
    """
    template = getattr(request.scope.get("route"), "path", None)
    return template if isinstance(template, str) else None


def _validation_headers(request: Request) -> dict[str, str] | None:
    route = _route_path(request) or ""
    if route.startswith(_NO_STORE_ROUTE_PREFIXES):
        return {"Cache-Control": "no-store"}
    return None


async def _handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions with RFC 9457 format."""
    request_id = uuid.uuid4().hex
    log_unhandled_exception(request, exc, request_id)
    body = ProblemDetail(
        title="Erreur interne du serveur",
        status=500,
        instance=_route_path(request),
    ).model_dump(exclude_none=True)
    return JSONResponse(
        status_code=500,
        content=body,
        media_type="application/problem+json",
        headers={"X-Request-ID": request_id},
    )


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Catch unhandled exceptions INSIDE the CORS middleware stack.

    Starlette's ExceptionMiddleware (where add_exception_handler lives)
    sits OUTSIDE user-added middlewares, so its responses bypass CORS.
    This middleware catches exceptions before they escape CORS, ensuring
    error responses always include proper Access-Control-Allow-Origin.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 - logged by log_unhandled_exception
            request_id = uuid.uuid4().hex
            log_unhandled_exception(request, exc, request_id)
            body = ProblemDetail(
                title="Erreur interne du serveur",
                status=500,
                instance=_route_path(request),
            ).model_dump(exclude_none=True)
            return JSONResponse(
                status_code=500,
                content=body,
                media_type="application/problem+json",
                # The visitor can quote it; the log line carries the same id. Address
                # routes also get no-store (_validation_headers, 261012-lgx I2).
                headers={
                    "X-Request-ID": request_id,
                    **(_validation_headers(request) or {}),
                },
            )


UNHANDLED_EXCEPTION_EVENT = "unhandled_exception"

# Pure helpers shared with the server loggers' filter (app.logging_config).
_exception_chain = error_redaction.exception_chain
_qualified_type = error_redaction.qualified_type
_traceback_without_messages = error_redaction.traceback_without_messages


def log_unhandled_exception(request: Request, exc: Exception, request_id: str) -> None:
    """Log an unhandled exception without echoing visitor input (C60).

    Always: exception type, route template (never the raw URL), method and a
    request id returned to the visitor. Outside DEBUG, whatever the error, no
    exception message of the chain (cause and context) is logged, only its
    frames and types: a message can quote a value the visitor typed (an
    address, a key, a cursor), whichever library wrote it. With DEBUG the
    full exception is logged, for local debugging.
    """
    fields = {
        "error_type": _qualified_type(exc),
        "route": route_template(request),
        "method": request.method,
        "request_id": request_id,
    }
    # The fields are also in the message: the structlog formatter of
    # logging_config renders the message and exc_info, not stdlib `extra`.
    message = UNHANDLED_EXCEPTION_EVENT + "".join(f" {k}=%s" for k in fields)
    args = tuple(fields.values())
    if settings.debug:
        logger.exception(message, *args, extra=fields)
        return
    fields["traceback"] = _traceback_without_messages(exc)
    logger.error(message + "\n%s", *args, fields["traceback"], extra=fields)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all custom exception handlers on the FastAPI app."""
    app.add_exception_handler(ErreurApiError, _handle_exception_api)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
