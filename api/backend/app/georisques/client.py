"""Async httpx client wrapper for Georisques API v1 with parallel gather.

Proxies natural risk endpoints (GASPAR, radon, RGA, ICPE) and polluted
sites (SSP/BASOL/SIS) in real-time.  Per-endpoint failures return None
for graceful degradation rather than raising exceptions.
"""

import asyncio
import json
import logging

import httpx

from app.common.exceptions import UpstreamUnavailableError
from app.common.http import CONTEXTE_TLS
from app.common.proxy_coverage import ProxySource, bounded_rows, unavailable
from app.config import settings

logger = logging.getLogger(__name__)

GEORISQUES_BASE = "https://georisques.gouv.fr/api/v1"

# The upstream closes the connection when a compressed body is offered (observed
# 2026-10-26: httpx's default gzip/deflate gives RemoteProtocolError, identity
# gives 200), so every client aimed at GEORISQUES_BASE sends this header.
GEORISQUES_HEADERS = {"Accept-Encoding": "identity"}

TIMEOUT = httpx.Timeout(
    settings.proxy_read_timeout_s,
    connect=settings.proxy_connect_timeout_s,
)


def _format_latlon(lat: float, lon: float) -> str:
    """Format coordinates for Georisques API.

    CRITICAL: Georisques expects lon,lat order despite param name "latlon".
    """
    return f"{lon},{lat}"


def _safe_json(response: httpx.Response) -> dict | None:
    """Extract JSON body if status is 200, else log and return None.

    Handles empty body, non-dict JSON, and JSONDecodeError gracefully.
    """
    if response.status_code != 200:
        logger.warning(
            "georisques_response_error",
            extra={
                "status_code": response.status_code,
                "body_length": len(response.content),
            },
        )
        return None

    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError):
        logger.warning(
            "georisques_response_unparseable",
            extra={"body_length": len(response.content)},
        )
        return None

    if not isinstance(data, dict):
        logger.warning(
            "georisques_response_invalid_type",
            extra={"response_type": type(data).__name__},
        )
        return None

    return data


async def query_all_risks(  # noqa: C901
    lat: float,
    lon: float,
    code_insee: str | None = None,
    rayon_m: int = 1000,
) -> dict:
    """Query 4 Georisques risk endpoints in parallel via asyncio.gather.

    Returns dict with keys: risques_naturels, argiles, radon, icpe.
    Each value is the parsed JSON data block or None on failure.
    """
    latlon = _format_latlon(lat, lon)

    async with httpx.AsyncClient(
        verify=CONTEXTE_TLS,
        base_url=GEORISQUES_BASE,
        timeout=TIMEOUT,
        headers=GEORISQUES_HEADERS,
    ) as client:
        # Build per-endpoint params
        gaspar_params: dict = {"page_size": 50}
        if code_insee:
            gaspar_params["code_insee"] = code_insee
        else:
            gaspar_params["latlon"] = latlon
            gaspar_params["rayon"] = rayon_m

        rga_params: dict = {"latlon": latlon}

        radon_params: dict = {}
        if code_insee:
            radon_params["code_insee"] = code_insee
        else:
            radon_params["latlon"] = latlon
            radon_params["rayon"] = 1000

        icpe_params: dict = {"page_size": 50}
        if code_insee:
            icpe_params["code_insee"] = code_insee
        else:
            icpe_params["latlon"] = latlon
            icpe_params["rayon"] = rayon_m

        results = await asyncio.gather(
            client.get("/gaspar/risques", params=gaspar_params),
            client.get("/rga", params=rga_params),
            client.get("/radon", params=radon_params),
            client.get("/installations_classees", params=icpe_params),
            return_exceptions=True,
        )

    # Parse each result -- exceptions become None
    parsed: list[dict | None] = []
    endpoint_names = ["gaspar/risques", "rga", "radon", "installations_classees"]
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(
                "georisques_request_failed",
                extra={
                    "endpoint": endpoint_names[i],
                    "error_type": type(result).__name__,
                },
            )
            parsed.append(None)
        else:
            parsed.append(_safe_json(result))

    # Fail-closed: if ALL upstreams failed, raise rather than returning empty data.
    # A single non-None parsed result means at least one upstream responded (D-09).
    if all(p is None for p in parsed):
        raise UpstreamUnavailableError("Georisques API", "all upstreams unavailable")

    payload = {}
    sources = {}
    for key, block in zip(
        ["risques_naturels", "argiles", "radon", "icpe"], parsed, strict=True
    ):
        payload[key] = None
        state = unavailable()
        if block is not None:
            if key == "argiles":
                if any(
                    name in block
                    for name in ["exposition", "code", "libelle", "niveau"]
                ):
                    payload[key] = block
                    state = ProxySource(
                        statut="disponible",
                        nombre_retournes=1,
                        complet=True,
                        pages_lues=1,
                    )
            elif isinstance(block.get("data"), list):
                try:
                    rows = bounded_rows(
                        block["data"],
                        limit=50,
                        total=block.get("results"),
                        next_page=block.get("next"),
                    )
                    payload[key] = list(rows)
                    state = rows.coverage
                except UpstreamUnavailableError:
                    pass
        sources[key] = state.model_dump()
    if all(state["statut"] == "indisponible" for state in sources.values()):
        raise UpstreamUnavailableError("Georisques API", "invalid sub-source responses")
    return {**payload, "sources": sources}


# Landmark ③ of the address page (API CR-01, D-15): one call, bounded well
# under the SSR timeout of the page, so that a slow Géorisques only turns its
# own landmark into an outage.
RGA_TIMEOUT = httpx.Timeout(4.0, connect=2.0)
RGA_DEADLINE_S = 5.0


async def query_rga(lat: float, lon: float) -> dict | None:
    """The RGA (clay shrink-swell) block at one point, and nothing else.

    ``None`` when the RGA answers 200 with no body: it publishes nothing at
    this point (observed 2026-11-05 on the Île de Nantes), it is not down.
    Raises :class:`UpstreamUnavailableError` on a network failure, a timeout,
    a non-200 status or a body that is not a JSON object.
    """
    try:
        async with asyncio.timeout(RGA_DEADLINE_S):
            async with httpx.AsyncClient(
                verify=CONTEXTE_TLS,
                base_url=GEORISQUES_BASE,
                timeout=RGA_TIMEOUT,
                headers=GEORISQUES_HEADERS,
            ) as client:
                response = await client.get(
                    "/rga", params={"latlon": _format_latlon(lat, lon)}
                )
    except (TimeoutError, httpx.HTTPError) as exc:
        raise UpstreamUnavailableError("Georisques RGA", "request failed") from exc
    if response.status_code == 200 and not response.content.strip():
        return None
    data = _safe_json(response)
    if data is None:
        raise UpstreamUnavailableError("Georisques RGA", "invalid response")
    return data


async def query_polluted_sites(
    lat: float,
    lon: float,
    rayon_m: int = 1000,
) -> dict:
    """Query Georisques SSP endpoint for polluted sites (BASOL/SIS/CASIAS).

    Returns dict with keys: casias, instructions, conclusions_sis,
    conclusions_sup. Fail-closed (ADR-0004): raises UpstreamUnavailableError
    if the Georisques SSP upstream is unreachable or returns an invalid body.
    """
    latlon = _format_latlon(lat, lon)

    try:
        async with httpx.AsyncClient(
            verify=CONTEXTE_TLS,
            base_url=GEORISQUES_BASE,
            timeout=TIMEOUT,
            headers=GEORISQUES_HEADERS,
        ) as client:
            response = await client.get(
                "/ssp",
                params={"latlon": latlon, "rayon": rayon_m, "page_size": 50},
            )
    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError) as exc:
        # ADR-0004 D1: fail-closed. A down upstream must surface as 503, never as
        # a reassuring "no polluted site" (HTTP 200 with null fields).
        raise UpstreamUnavailableError("Georisques SSP", "request failed") from exc

    data = _safe_json(response)
    if data is None:
        raise UpstreamUnavailableError("Georisques SSP", "invalid response")

    payload = {}
    sources = {}
    for key in ["casias", "instructions", "conclusions_sis", "conclusions_sup"]:
        block = data.get(key)
        rows = None
        try:
            if isinstance(block, dict) and isinstance(block.get("data"), list):
                rows = bounded_rows(
                    block["data"],
                    limit=50,
                    total=block.get("results"),
                    next_page=block.get("next"),
                )
            elif isinstance(block, list):
                rows = bounded_rows(block, limit=50)
        except UpstreamUnavailableError:
            pass
        payload[key] = None if rows is None else list(rows)
        sources[key] = (unavailable() if rows is None else rows.coverage).model_dump()
    if all(state["statut"] == "indisponible" for state in sources.values()):
        raise UpstreamUnavailableError("Georisques SSP", "invalid sub-source responses")
    return {**payload, "sources": sources}
