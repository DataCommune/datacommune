"""Async httpx proxy for API Recherche d'Entreprises.

Proxies the /near_point endpoint from recherche-entreprises.api.gouv.fr
in real-time. No auth required (free public API, 7 req/s rate limit).
Raises UpstreamUnavailableError on 429/5xx/connection errors so the router
can answer 503 + Retry-After (Phase 22 proxy convention).
"""

import logging

import httpx

from app.common.exceptions import UpstreamUnavailableError
from app.common.http import CONTEXTE_TLS
from app.config import settings

logger = logging.getLogger(__name__)

RECHERCHE_BASE = "https://recherche-entreprises.api.gouv.fr"

TIMEOUT = httpx.Timeout(
    settings.proxy_read_timeout_s,
    connect=settings.proxy_connect_timeout_s,
)


async def entreprises_a_proximite(
    lat: float,
    lon: float,
    radius_km: float = 5.0,
    activite_principale: str | None = None,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """Proxy API Recherche d'Entreprises -- near_point endpoint.

    Returns parsed JSON on success. Raises UpstreamUnavailableError on rate
    limit (429), upstream 5xx, or connection error.

    NOTE: API uses "long" not "lon" as query parameter name.
    """
    params: dict = {
        "lat": lat,
        "long": lon,
        "radius": radius_km,
        "page": page,
        "per_page": min(per_page, 25),  # API max = 25
    }
    if activite_principale:
        params["activite_principale"] = activite_principale

    try:
        async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
            resp = await client.get(f"{RECHERCHE_BASE}/near_point", params=params)

            if resp.status_code == 429:
                logger.warning("API Recherche d'Entreprises rate limited (429)")
                raise UpstreamUnavailableError("Sirene", "quota depasse (429)")

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise UpstreamUnavailableError(
                    "Sirene", f"HTTP {exc.response.status_code}"
                ) from exc
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("Invalid Sirene results envelope")  # noqa: TRY004
            if any(not isinstance(result, dict) for result in data["results"]):
                raise ValueError("Invalid Sirene result record")
            return data

    except (
        httpx.TimeoutException,
        httpx.ConnectError,
    ) as exc:
        logger.warning(
            "sirene_request_failed", extra={"error_type": type(exc).__name__}
        )
        raise UpstreamUnavailableError("Sirene", "request failed") from exc
