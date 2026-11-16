"""FastAPI router for entreprises proxy endpoint (Sirene near_point)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.proxy import proxy_error_boundary
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.scope.dependencies import RouteHosting, hosted_datasets
from app.sirene.client import entreprises_a_proximite
from app.sirene.schemas import ResultatEntreprise

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/entreprises",
    response_model=ReponseApi[list[ResultatEntreprise]],
    summary="Entreprises a proximite",
    description=(
        "Etablissements et entreprises autour d'un point GPS via le repertoire "
        "Sirene (INSEE). Proxy API Sirene."
    ),
)
async def get_entreprises(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("sirene"))],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=47.2184),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=-1.5536),
    ],
    rayon_m: Annotated[
        float,
        Query(
            ge=100,
            le=50000,
            description="Rayon de recherche en metres (max 50 km)",
            example=5000,
        ),
    ] = 5000.0,
    activite_principale: Annotated[
        str | None,
        Query(
            description=(
                "Code NAF/APE pour filtrer par activite "
                "(ex: 47.11 = commerce de detail)"
            ),
            example="47.11",
        ),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=25, description="Nombre max de resultats", example=10),
    ] = 10,
) -> ReponseApi[list[ResultatEntreprise]]:
    """Query entreprises near a GPS point.

    Proxies API Recherche d'Entreprises /near_point in real-time.
    Returns 503 + Retry-After if the upstream API is unreachable or rate limited.

    Source: recherche-entreprises.api.gouv.fr (Etalab, sans auth).
    """
    if hosting.none_hosted:
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("sirene")))

    rayon_km = rayon_m / 1000
    async with proxy_error_boundary("Sirene"):
        data = await entreprises_a_proximite(
            lat, lon, rayon_km, activite_principale, page=1, per_page=limite
        )
        # Parse results from API response structure (a malformed payload -> 502).
        results = data["results"]
        resultats = [ResultatEntreprise(**r) for r in results]
    return ReponseApi(donnees=resultats, meta=get_meta_source("sirene"))
