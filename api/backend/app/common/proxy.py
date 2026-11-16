"""Uniform error contract for external-API proxy routers (ADR-0004)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException

from app.common.exceptions import ErreurApiError, UpstreamUnavailableError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def proxy_error_boundary(
    service: str, retry_after: int = 60
) -> AsyncIterator[None]:
    """Uniform error contract for external-API proxy routers (ADR-0004 D5).

    - Upstream down (``UpstreamUnavailableError``) -> 503 problem+json + Retry-After.
    - Unexpected proxy failure (parsing, non-httpx) -> 502 problem+json, attributed
      to the upstream service (keeps 500 a reliable "our bug" signal).
    - Endpoint-specific HTTP errors (business 422/404) pass through untouched.

    Ordering matters: ``HTTPException`` re-raises before the catch-all so business
    4xx survive, and ``UpstreamUnavailableError`` is caught before ``Exception`` so a
    known outage is never requalified as a 502.
    """
    try:
        yield
    except HTTPException:
        raise  # business 4xx raised inside the block (bdnb 422/404, rnb 422)
    except UpstreamUnavailableError as exc:
        logger.warning("%s upstream unavailable: %s", service, exc)
        raise ErreurApiError(
            503,
            f"Service {service} temporairement indisponible."
            " Réessayez dans quelques minutes.",
            headers={"Retry-After": str(retry_after)},
        ) from None
    except Exception as exc:
        logger.exception("%s proxy failure: %s", service, exc)  # noqa: TRY401
        raise ErreurApiError(502, f"Réponse invalide du service {service}") from exc
