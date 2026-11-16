"""Async httpx proxy for ERRIAL API (Etat des Risques Reglementaires).

Proxies errial.georisques.gouv.fr — no auth, no documented rate limit.
Two-step flow: resolve parcel via cadastre endpoint, then fetch full
risk assessment via /api/avis.
"""

import json
import logging

import httpx
from cachetools import TTLCache
from cachetools_async import cached

from app.common.exceptions import UpstreamUnavailableError
from app.common.http import CONTEXTE_TLS
from app.common.proxy_coverage import ProxySource, unavailable
from app.config import settings

logger = logging.getLogger(__name__)

ERRIAL_BASE = "https://errial.georisques.gouv.fr/api"

TIMEOUT = httpx.Timeout(
    settings.proxy_read_timeout_s,
    connect=settings.proxy_connect_timeout_s,
)

_resolve_cache: TTLCache = TTLCache(maxsize=2048, ttl=settings.proxy_cache_ttl_s)
_avis_cache: TTLCache = TTLCache(maxsize=2048, ttl=settings.proxy_cache_ttl_s)


@cached(cache=_resolve_cache)
async def resolve_parcelle_by_coords(lat: float, lon: float) -> tuple[str, str] | None:
    """Resolve GPS coordinates to (code_insee, code_parcelle) via ERRIAL cadastre.

    Returns tuple (code_insee, formatted_parcelle) or None on failure.
    The formatted_parcelle is "{prefixe}-{section}-{numero}@{commune}".
    """
    lat, lon = round(lat, 5), round(lon, 5)
    try:
        async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
            resp = await client.get(f"{ERRIAL_BASE}/cadastre/{lon}/{lat}")
            if resp.status_code != 200:
                raise UpstreamUnavailableError(
                    "ERRIAL cadastre", f"HTTP {resp.status_code}"
                )

            features_raw = resp.json()
            if not isinstance(features_raw, list):
                raise UpstreamUnavailableError("ERRIAL cadastre", "invalid features")
            if not features_raw:
                return None

            # Response is a list of GeoJSON Feature strings
            if isinstance(features_raw[0], str):
                feature = json.loads(features_raw[0])
            else:
                feature = features_raw[0]

            props = feature.get("properties", {})
            code = props.get("code")  # e.g. "000-LB-369"
            commune = props.get("commune")  # e.g. "06088"

            if not code or not commune:
                raise UpstreamUnavailableError("ERRIAL cadastre", "invalid parcel")

            return (commune, f"{code}@{commune}")

    except (httpx.TimeoutException, httpx.ConnectError, ValueError) as exc:
        logger.warning(
            "errial_cadastre_failed", extra={"error_type": type(exc).__name__}
        )
        raise UpstreamUnavailableError("ERRIAL cadastre", "request failed") from exc


@cached(cache=_avis_cache)
async def query_avis(code_insee: str, code_parcelle_formatted: str) -> dict:
    """Fetch full ERRIAL risk assessment for a parcel.

    code_parcelle_formatted should be "{prefixe}-{section}-{numero}@{commune}".
    Returns raw avis dict. Raises UpstreamUnavailableError on failure.
    """
    try:
        async with httpx.AsyncClient(verify=CONTEXTE_TLS, timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{ERRIAL_BASE}/avis",
                params={
                    "codeINSEE": code_insee,
                    "codeParcelle": code_parcelle_formatted,
                },
            )
            if resp.status_code != 200:
                raise UpstreamUnavailableError(
                    "ERRIAL avis", f"HTTP {resp.status_code}"
                )
            data = resp.json()
            if (
                not isinstance(data, dict)
                or data.get("hasError")
                or not isinstance(data.get("summary"), dict)
            ):
                raise UpstreamUnavailableError("ERRIAL avis", "invalid assessment")
            sources = extract_avis_summary(data)["sources"]
            if all(state["statut"] == "indisponible" for state in sources.values()):
                raise UpstreamUnavailableError("ERRIAL avis", "assessment unavailable")
            return data

    except (httpx.TimeoutException, httpx.ConnectError, ValueError) as exc:
        logger.warning("errial_avis_failed", extra={"error_type": type(exc).__name__})
        raise UpstreamUnavailableError("ERRIAL avis", "request failed") from exc


def extract_avis_summary(raw: dict) -> dict:
    """Extract the most useful fields from the verbose ERRIAL avis response.

    Flattens the deeply nested structure into a clean, API-friendly dict.
    """
    summary = raw.get("summary", {})
    commune = summary.get("commune", {})

    # PPR (Plans de Prevention des Risques)
    pprs_raw = raw.get("planPreventionRisquesDTOs")
    pprs = (
        [
            {
                "id_gaspar": p.get("idGaspar"),
                "libelle": p.get("libelleGaspar"),
                "date_approbation": p.get("dateApprobation"),
                "date_prescription": p.get("datePrescription"),
            }
            for p in pprs_raw
        ]
        if isinstance(pprs_raw, list)
        else None
    )

    # AZI (Atlas des Zones Inondables)
    azis_raw = raw.get("azis")
    azis = (
        [
            {
                "code": a.get("code_national_azi"),
                "libelle": a.get("libelle_azi"),
            }
            for a in azis_raw
        ]
        if isinstance(azis_raw, list)
        else None
    )

    # Pollution counts per proximity level
    def _count(key: str) -> int | None:
        val = raw.get(key)
        return len(val) if isinstance(val, list) else None

    result = {
        "adresse": summary.get("adresse"),
        "code_parcelle": summary.get("codeParcelle"),
        "commune": {
            "code_insee": commune.get("codeINSEE"),
            "nom": commune.get("nomCommune"),
            "code_postal": commune.get("codePostal"),
            "code_departement": commune.get("codeDepartement"),
        },
        "sismicite": commune.get("codeZoneSismicite"),
        "radon": commune.get("classePotentielRadon"),
        "argile": raw.get("niveauArgile"),
        "ppr": pprs,
        "azi": azis,
        "pollution": {
            "basias_sur_parcelle": _count("siteIndustrielBasiasSurParcelleDTOs"),
            "basias_proximite": _count("siteIndustrielBasiasProximiteParcelleDTOs"),
            "basol_sur_parcelle": _count("siteIndustrielBasolSurParcelleDTOs"),
            "basol_proximite": _count("siteIndustrielBasolProximiteParcelleDTOs"),
            "sis_sur_parcelle": _count("secteurInformationSolSurParcelleDTOs"),
            "sis_proximite": _count("secteurInformationSolProximiteParcelleDTOs"),
            "icpe_sur_parcelle": _count("installationClasseeSurParcelleDTOs"),
            "icpe_proximite": _count("installationClasseeProximiteParcelleDTOs"),
        },
        "nucleaire": raw.get("hasCentraleNucleaire")
        if type(raw.get("hasCentraleNucleaire")) is bool
        else None,
        "recul_trait_cote": raw.get("hasReculTraitCote")
        if type(raw.get("hasReculTraitCote")) is bool
        else None,
        "obligation_debroussaillement": raw.get("hasOLD")
        if type(raw.get("hasOLD")) is bool
        else None,
        "zone_urbaine": raw.get("hasZoneUrbaine")
        if type(raw.get("hasZoneUrbaine")) is bool
        else None,
        "plan_exposition_bruit": raw.get("zonePlanExpositionBruit"),
        "canalisations": _count("geogCanalisations"),
    }

    sources = {}
    values = {
        key: value
        for key, value in result.items()
        if key not in {"adresse", "code_parcelle", "commune", "pollution"}
    }
    values.update(
        {f"pollution.{key}": value for key, value in result["pollution"].items()}
    )
    for key, value in values.items():
        if value is None:
            state = unavailable()
        else:
            state = ProxySource(
                statut="vide" if value == [] else "disponible",
                complet=True,
                pages_lues=1,
            )
        sources[key] = state.model_dump()
    return {**result, "sources": sources}
