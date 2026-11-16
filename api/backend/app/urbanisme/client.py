"""Async httpx client wrapper for API Carto GPU (IGN).

Queries PLU zones and surface prescriptions in parallel for a given GPS
point.  Uses GeoJSON Point geometry (lon,lat order per RFC 7946).
"""

import asyncio
import json
import logging

import httpx

from app.common.exceptions import UpstreamUnavailableError
from app.common.http import CONTEXTE_TLS
from app.common.proxy_coverage import bounded_rows, unavailable
from app.config import settings

logger = logging.getLogger(__name__)

APICARTO_BASE = "https://apicarto.ign.fr/api/gpu"

TIMEOUT = httpx.Timeout(
    settings.proxy_urbanisme_read_timeout_s,
    connect=settings.proxy_connect_timeout_s,
)


def _safe_result(endpoint: str, result: object) -> dict | None:
    """Return a valid response body without logging URL, body or exception text."""
    if isinstance(result, Exception):
        logger.warning(
            "apicarto_request_failed",
            extra={"endpoint": endpoint, "error_type": type(result).__name__},
        )
        return None
    if not isinstance(result, httpx.Response):
        logger.warning(
            "apicarto_response_invalid_type",
            extra={"endpoint": endpoint, "response_type": type(result).__name__},
        )
        return None
    if result.status_code != 200:
        logger.warning(
            "apicarto_response_error",
            extra={
                "endpoint": endpoint,
                "status_code": result.status_code,
                "body_length": len(result.content),
            },
        )
        return None
    try:
        data = result.json()
    except (ValueError, json.JSONDecodeError):
        logger.warning(
            "apicarto_response_unparseable",
            extra={"endpoint": endpoint, "body_length": len(result.content)},
        )
        return None
    if not isinstance(data, dict):
        logger.warning(
            "apicarto_response_invalid_type",
            extra={"endpoint": endpoint, "response_type": type(data).__name__},
        )
        return None
    features = data.get("features")
    if not isinstance(features, list) or any(
        not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict)
        for feature in features
    ):
        return None
    return data


async def query_urbanisme(lat: float, lon: float) -> dict:
    """Query PLU zones and surface prescriptions in parallel.

    CRITICAL: GeoJSON uses lon,lat order (not lat,lon).

    Returns dict with keys:
    - zones_plu: list of zone features (FeatureCollection.features)
    - prescriptions: list of prescription features
    """
    # Build GeoJSON Point geometry -- lon,lat order per RFC 7946
    geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})

    async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            client.get(
                f"{APICARTO_BASE}/zone-urba",
                params={"geom": geom},
            ),
            client.get(
                f"{APICARTO_BASE}/prescription-surf",
                params={"geom": geom},
            ),
            return_exceptions=True,
        )

    endpoint_names = ["zone-urba", "prescription-surf"]
    parsed: list[dict | None] = []

    for endpoint, result in zip(endpoint_names, results, strict=True):
        parsed.append(_safe_result(endpoint, result))

    # Fail-closed: if ALL upstreams failed, raise rather than returning empty data.
    # A single non-None parsed result means at least one upstream responded (D-09).
    if all(p is None for p in parsed):
        raise UpstreamUnavailableError("API Carto GPU", "all upstreams unavailable")

    payload = {}
    sources = {}
    for key, block in zip(["zones_plu", "prescriptions"], parsed, strict=True):
        if block is None:
            payload[key] = None
            sources[key] = unavailable().model_dump()
        else:
            try:
                rows = bounded_rows(
                    block["features"],
                    limit=100,
                    total=block.get("numberMatched", block.get("totalFeatures")),
                    pagination_known=False,
                )
                payload[key] = list(rows)
                sources[key] = rows.coverage.model_dump()
            except UpstreamUnavailableError:
                payload[key] = None
                sources[key] = unavailable().model_dump()
    if all(state["statut"] == "indisponible" for state in sources.values()):
        raise UpstreamUnavailableError("API Carto GPU", "invalid sub-source responses")
    return {**payload, "sources": sources}
