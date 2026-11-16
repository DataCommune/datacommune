"""Async httpx proxy for BDNB API (Base de Donnees Nationale des Batiments).

Proxies the CSTB BDNB open tier (no auth, 10K req/month, 120 req/min).
Supports lookup by address key (cle_interop_adr) and cadastral parcel ID.

Serves the ``/v1/bdnb*`` routes, ``/v1/resolve?datasets=bdnb`` and the manual
``proxy_run`` profile only: the address report reads the imported export
(``bdnb_batiment_parcelle``, 04.7-19). Every request that leaves for
api.bdnb.io logs one ``bdnb_upstream_call`` line (the production check that
the report no longer calls it).
"""

import logging

import httpx
from cachetools import TTLCache
from cachetools_async import cached

from app.common.exceptions import UpstreamUnavailableError
from app.common.http import CONTEXTE_TLS
from app.common.proxy_coverage import bounded_rows
from app.config import settings

logger = logging.getLogger(__name__)

BDNB_BASE = "https://api.bdnb.io/v1/bdnb"

TIMEOUT = httpx.Timeout(
    settings.proxy_read_timeout_s,
    connect=settings.proxy_connect_timeout_s,
)

# Columns we expose (subset of ~100 available in open tier)
SELECT_COLUMNS = ",".join(
    [
        "batiment_groupe_id",
        "code_commune_insee",
        "code_departement_insee",
        "libelle_adr_principale_ban",
        "cle_interop_adr_principale_ban",
        "annee_construction",
        "hauteur_mean",
        "s_geom_groupe",
        "classe_bilan_dpe",
        "conso_5_usages_ep_m2",
        "emission_ges_5_usages_m2",
        "mat_mur_txt",
        "mat_toit_txt",
        "nb_classe_bilan_dpe_a",
        "nb_classe_bilan_dpe_b",
        "nb_classe_bilan_dpe_c",
        "nb_classe_bilan_dpe_d",
        "nb_classe_bilan_dpe_e",
        "nb_classe_bilan_dpe_f",
        "nb_classe_bilan_dpe_g",
        "l_parcelle_id",
        "l_siren",
        "l_denomination_proprietaire",
        "distance_monument_historique",
        "altitude_sol_mean",
    ]
)


def _appel_amont(fonction: str) -> None:
    """One line per request leaving for api.bdnb.io: the name of the function,
    never a parameter (an address key or a parcel)."""
    logger.info("bdnb_upstream_call", extra={"fonction": fonction})


# Module-level TTL caches — errors are NOT cached (cachetools-async behaviour)
_parcelle_cache: TTLCache = TTLCache(maxsize=2048, ttl=settings.proxy_cache_ttl_s)
_adresse_cache: TTLCache = TTLCache(maxsize=2048, ttl=settings.proxy_cache_ttl_s)
_geocode_cache: TTLCache = TTLCache(maxsize=4096, ttl=settings.proxy_cache_ttl_s)


@cached(cache=_adresse_cache)
async def query_batiment_by_adresse(cle_interop_adr: str) -> list[dict]:
    """Lookup buildings by BAN address key (cle_interop_adr).

    Returns list of building records.
    Raises UpstreamUnavailableError on upstream failure.
    """
    _appel_amont("query_batiment_by_adresse")
    try:
        async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{BDNB_BASE}/donnees/batiment_groupe_complet/adresse",
                params={
                    "cle_interop_adr": f"eq.{cle_interop_adr}",
                    "select": SELECT_COLUMNS,
                    "limit": 11,
                },
            )
            if resp.status_code == 429:
                logger.warning("BDNB API rate limited (429)")
                raise UpstreamUnavailableError("BDNB", "quota depasse (429)")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise UpstreamUnavailableError(
                    "BDNB", f"HTTP {exc.response.status_code}"
                ) from exc
            return bounded_rows(resp.json(), limit=10)
    except (httpx.TimeoutException, httpx.ConnectError, ValueError) as exc:
        logger.warning("bdnb_request_failed", extra={"error_type": type(exc).__name__})
        raise UpstreamUnavailableError("BDNB", "request failed") from exc


@cached(cache=_parcelle_cache)
async def query_batiment_by_parcelle(parcelle_id: str) -> list[dict]:
    """Lookup buildings by cadastral parcel ID (e.g. 75101000AB0001).

    Returns list of building records.
    Raises UpstreamUnavailableError on upstream failure.
    """
    _appel_amont("query_batiment_by_parcelle")
    try:
        async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{BDNB_BASE}/donnees/batiment_groupe_complet/parcelle",
                params={
                    "parcelle_id": f"eq.{parcelle_id}",
                    "select": SELECT_COLUMNS,
                    "limit": 11,
                },
            )
            if resp.status_code == 429:
                logger.warning("BDNB API rate limited (429)")
                raise UpstreamUnavailableError("BDNB", "quota depasse (429)")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise UpstreamUnavailableError(
                    "BDNB", f"HTTP {exc.response.status_code}"
                ) from exc
            return bounded_rows(resp.json(), limit=10)
    except (httpx.TimeoutException, httpx.ConnectError, ValueError) as exc:
        logger.warning("bdnb_request_failed", extra={"error_type": type(exc).__name__})
        raise UpstreamUnavailableError("BDNB", "request failed") from exc


@cached(cache=_geocode_cache)
async def geocode_bdnb(query: str) -> dict:
    """Geocode an address via BDNB (proxies BAN internally).

    Returns GeoJSON FeatureCollection.
    Raises UpstreamUnavailableError on failure.
    Useful to obtain cle_interop_adr for subsequent building lookups.
    """
    _appel_amont("geocode_bdnb")
    try:
        async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{BDNB_BASE}/geocodage",
                params={"q": query},
            )
            if resp.status_code == 429:
                logger.warning("BDNB geocoding rate limited (429)")
                raise UpstreamUnavailableError("BDNB geocodage", "quota depasse (429)")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise UpstreamUnavailableError(
                    "BDNB geocodage",
                    f"HTTP {exc.response.status_code}",
                ) from exc
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("features"), list):
                raise ValueError("Invalid BDNB geocoding features envelope")  # noqa: TRY004
            for feature in data["features"]:
                if not isinstance(feature, dict):
                    raise ValueError("Invalid BDNB geocoding feature")  # noqa: TRY004
                if "properties" in feature and not isinstance(
                    feature["properties"], dict
                ):
                    raise ValueError("Invalid BDNB geocoding properties")
            return data
    except (httpx.TimeoutException, httpx.ConnectError, ValueError) as exc:
        logger.warning(
            "bdnb_geocoding_failed", extra={"error_type": type(exc).__name__}
        )
        raise UpstreamUnavailableError("BDNB geocodage", "request failed") from exc
