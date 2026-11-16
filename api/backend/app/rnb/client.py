"""Async httpx proxy for RNB API (Referentiel National des Batiments).

Proxies rnb-api.beta.gouv.fr — no auth for reads, 20 req/s rate limit.
Supports lookup by coordinates, parcel ID, address, and RNB ID.
"""

import logging
import re

import httpx
from fastapi import HTTPException

from app.common.exceptions import UpstreamUnavailableError
from app.common.http import CONTEXTE_TLS
from app.common.proxy_coverage import bounded_rows
from app.config import settings

logger = logging.getLogger(__name__)

RNB_BASE = "https://rnb-api.beta.gouv.fr/api/alpha"

# LB-01: rnb_id / parcelle_id are interpolated into the upstream URL path.
# Both must match a strict format BEFORE interpolation, or any user-supplied
# string could navigate arbitrary paths of the upstream host (path traversal,
# query/fragment injection). Case-insensitive on purpose: RNB IDs are
# case-sensitive base62 — only the *character set* is the security boundary
# here, not the case.
_RNB_ID_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")
_PARCELLE_ID_RE = re.compile(r"^[0-9A-Z]{14}$")

TIMEOUT = httpx.Timeout(
    settings.proxy_read_timeout_s,
    connect=settings.proxy_connect_timeout_s,
)


async def _get(url: str, params: dict | None = None) -> dict | list:
    """Shared GET helper with error handling.

    Returns parsed JSON on success, [] on 404 (no match found). Raises
    UpstreamUnavailableError on 429, upstream 5xx, or connection error.
    """
    try:
        async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                logger.warning("RNB API rate limited (429)")
                raise UpstreamUnavailableError("RNB", "quota depasse (429)")
            if resp.status_code == 404:
                return []
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise UpstreamUnavailableError(
                    "RNB", f"HTTP {exc.response.status_code}"
                ) from exc
            body = resp.json()
            if not isinstance(body, (dict, list)):
                raise UpstreamUnavailableError("RNB", "invalid body")
            return body
    except (httpx.TimeoutException, httpx.ConnectError, ValueError) as exc:
        logger.warning("rnb_request_failed", extra={"error_type": type(exc).__name__})
        raise UpstreamUnavailableError("RNB", "request failed") from exc


async def query_by_coords(
    lat: float, lon: float, radius: int = 50
) -> list[dict] | None:
    """Find buildings closest to a GPS point within radius (metres)."""
    data = await _get(
        f"{RNB_BASE}/buildings/closest/",
        params={"point": f"{lat},{lon}", "radius": min(radius, 1000)},
    )
    if data is None:
        return None
    if isinstance(data, dict):
        if not isinstance(data.get("results"), list):
            raise UpstreamUnavailableError("RNB", "invalid results")
        return bounded_rows(
            data["results"],
            limit=50,
            total=data.get("count"),
            next_page=data.get("next"),
            pagination_known="next" in data,
        )
    if not isinstance(data, list):
        raise UpstreamUnavailableError("RNB", "invalid results")
    return bounded_rows(data, limit=50)


async def query_by_parcelle(parcelle_id: str) -> list[dict] | None:
    """Find buildings on a cadastral parcel.

    Serves the public ``/v1/rnb`` proxy only. The ``with_plots`` opt-in that
    used to request ``withPlots=1`` (the BAN keys of every entrance, Plan A'
    of RESEARCH-L1 §1.1) is gone: Lot C1 replaced that live per-parcel call
    by a local ``rnb_liens`` join, fed in bulk by the departmental RNB export
    whose CSV already carries ``addresses`` and ``plots``. It had no caller
    left in ``app/``.
    """
    if not _PARCELLE_ID_RE.fullmatch(parcelle_id):
        raise HTTPException(
            status_code=422,
            detail="parcelle_id invalide : référence cadastrale de 14 caractères"
            " alphanumériques majuscules attendue",
        )
    data = await _get(f"{RNB_BASE}/buildings/plot/{parcelle_id}/")
    if data is None:
        return None
    if isinstance(data, dict):
        if not isinstance(data.get("results"), list):
            raise UpstreamUnavailableError("RNB", "invalid results")
        return bounded_rows(
            data["results"],
            limit=50,
            total=data.get("count"),
            next_page=data.get("next"),
            pagination_known="next" in data,
        )
    if not isinstance(data, list):
        raise UpstreamUnavailableError("RNB", "invalid results")
    return bounded_rows(data, limit=50)


async def query_by_address(q: str) -> list[dict] | None:
    """Find buildings matching a free-text address (geocoded via BAN)."""
    data = await _get(
        f"{RNB_BASE}/buildings/address/",
        params={"q": q},
    )
    if data is None:
        return None
    if isinstance(data, dict):
        if not isinstance(data.get("results"), list):
            raise UpstreamUnavailableError("RNB", "invalid results")
        return bounded_rows(
            data["results"],
            limit=50,
            total=data.get("count"),
            next_page=data.get("next"),
            pagination_known="next" in data,
        )
    if not isinstance(data, list):
        raise UpstreamUnavailableError("RNB", "invalid results")
    return bounded_rows(data, limit=50)


async def query_by_rnb_id(rnb_id: str) -> dict | None:
    """Get a single building by its RNB ID."""
    if not _RNB_ID_RE.fullmatch(rnb_id):
        raise HTTPException(
            status_code=422,
            detail="rnb_id invalide : identifiant alphanumérique de 6 à 32"
            " caractères attendu",
        )
    data = await _get(f"{RNB_BASE}/buildings/{rnb_id}/")
    if data is None:
        return None
    if isinstance(data, list):
        if not data:
            return None
        if len(data) != 1:
            raise UpstreamUnavailableError("RNB", "ambiguous detail response")
        data = data[0]
    if not isinstance(data, dict) or not data.get("rnb_id"):
        raise UpstreamUnavailableError("RNB", "invalid detail response")
    return data
