"""FastAPI router for urbanisme proxy endpoint (API Carto GPU/IGN)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.proxy import proxy_error_boundary
from app.common.proxy_coverage import is_partial
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.scope.dependencies import RouteHosting, hosted_datasets
from app.urbanisme.client import query_urbanisme
from app.urbanisme.schemas import ResultatUrbanisme

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Parcelle"])


@router.get(
    "/urbanisme",
    response_model=ReponseApi[ResultatUrbanisme],
    summary="Zonages PLU",
    description=(
        "Zonages des Plans Locaux d'Urbanisme (PLU/PLUi) pour un point GPS. "
        "Proxy API Carto de l'IGN."
    ),
)
async def get_urbanisme(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("urbanisme"))],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ],
) -> ReponseApi[ResultatUrbanisme]:
    """Query PLU zones and surface prescriptions for a GPS point.

    Proxies the API Carto GPU (IGN) zone-urba and prescription-surf
    endpoints in parallel.  Returns structured PLU zone type (U, AU, A, N)
    and applicable prescriptions.
    """
    if hosting.none_hosted:
        return ReponseApi(
            donnees=ResultatUrbanisme(), meta=hosting.meta(get_meta_source("urbanisme"))
        )

    async with proxy_error_boundary("API Carto GPU"):
        data = await query_urbanisme(lat, lon)
        # Public endpoint flattens GeoJSON properties; Resolve retains GeoJSON.
        payload = dict(data)
        for key in ["zones_plu", "prescriptions"]:
            if payload.get(key) is not None:
                payload[key] = [feature["properties"] for feature in payload[key]]
        resultat = ResultatUrbanisme(**payload)
    return ReponseApi(
        statut="partiel" if is_partial(resultat.sources) else "ok",
        donnees=resultat,
        meta=get_meta_source("urbanisme"),
    )
