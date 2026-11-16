"""FastAPI router for risques naturels and pollutions proxy endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.proxy import proxy_error_boundary
from app.common.proxy_coverage import is_partial
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.georisques.client import query_all_risks, query_polluted_sites
from app.georisques.schemas import ResultatPollutions, ResultatRisques
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/risques",
    response_model=ReponseApi[ResultatRisques],
    summary="Risques naturels et technologiques",
    description=(
        "Risques naturels (inondation, seisme, mouvement de terrain...) et "
        "technologiques (Seveso, nucleaire...) autour d'un point. "
        "Proxy Georisques API."
    ),
)
async def get_risques(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("georisques", departement=code_insee_departement)),
    ],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=47.2184),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=-1.5536),
    ],
    code_insee: Annotated[
        str | None,
        Query(
            description="Code INSEE commune (accelere la requete GASPAR si fourni)",
            example="44109",
        ),
    ] = None,
    rayon_m: Annotated[
        int,
        Query(
            ge=100,
            le=10000,
            description="Rayon de recherche en metres",
            example=1000,
        ),
    ] = 1000,
) -> ReponseApi[ResultatRisques]:
    """Query natural and industrial risks near a GPS point.

    Proxies 4 Georisques API v1 endpoints in parallel:
    GASPAR (risques naturels), RGA (argiles), radon, ICPE.
    """
    if hosting.none_hosted:
        return ReponseApi(
            donnees=ResultatRisques(), meta=hosting.meta(get_meta_source("georisques"))
        )

    async with proxy_error_boundary("Georisques"):
        data = await query_all_risks(lat, lon, code_insee, rayon_m)
        # Map argiles: single object (not list)
        resultat = ResultatRisques(
            risques_naturels=data.get("risques_naturels"),
            argiles=data.get("argiles"),
            radon=data.get("radon"),
            icpe=data.get("icpe"),
            sources=data.get("sources", {}),
        )
    return ReponseApi(
        statut="partiel" if is_partial(resultat.sources) else "ok",
        donnees=resultat,
        meta=get_meta_source("georisques"),
    )


@router.get(
    "/pollutions",
    response_model=ReponseApi[ResultatPollutions],
    summary="Sites et sols pollues",
    description=(
        "Sites pollues (BASIAS, BASOL, SIS) dans un rayon autour d'un point GPS. "
        "Proxy Georisques API."
    ),
)
async def get_pollutions(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("georisques"))],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ],
    rayon_m: Annotated[
        int,
        Query(
            ge=100,
            le=10000,
            description="Rayon de recherche en metres",
            example=1000,
        ),
    ] = 1000,
) -> ReponseApi[ResultatPollutions]:
    """Query polluted sites (SSP/BASOL/SIS/CASIAS) near a GPS point.

    Proxies the Georisques API v1 SSP endpoint in real-time.
    """
    if hosting.none_hosted:
        return ReponseApi(
            donnees=ResultatPollutions(),
            meta=hosting.meta(get_meta_source("georisques")),
        )

    async with proxy_error_boundary("Georisques"):
        data = await query_polluted_sites(lat, lon, rayon_m)
        resultat = ResultatPollutions(**data)
    return ReponseApi(
        statut="partiel" if is_partial(resultat.sources) else "ok",
        donnees=resultat,
        meta=get_meta_source("georisques"),
    )
