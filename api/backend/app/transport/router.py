"""FastAPI router for Transport proximity endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas import ReponseApi
from app.common.sources import get_meta_sources
from app.database import get_db
from app.scope.dependencies import RouteHosting, hosted_datasets
from app.transport.queries import transports_a_proximite
from app.transport.schemas import ResultatArret

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/transports",
    response_model=ReponseApi[list[ResultatArret]],
    summary="Transports en commun",
    description=(
        "Arrets de transport en commun (bus, metro, tram) et gares SNCF "
        "a proximite d'un point GPS. Sources : transport.data.gouv.fr et SNCF."
    ),
)
async def get_transports(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("transports", "gares"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8738),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.2950),
    ],
    rayon_m: Annotated[
        float,
        Query(
            ge=100,
            le=5000,
            description="Rayon de recherche en metres",
            example=1000,
        ),
    ] = 1000.0,
    limite: Annotated[
        int,
        Query(
            ge=1,
            le=200,
            description="Nombre maximum de resultats (bus, tram, metro, gares SNCF)",
            example=50,
        ),
    ] = 50,
) -> ReponseApi[list[ResultatArret]]:
    """Query transport stops and train stations near a GPS point.

    Combines bus/tram/metro stops (arrets_transport) and SNCF train
    stations (gares) within the specified radius.  Results are ordered
    by distance from the query point.

    Sources:
    - transport.data.gouv.fr (GTFS consolidated stops)
    - ressources.data.sncf.com (liste-des-gares)
    """
    meta = hosting.meta(get_meta_sources(["transports", "gares"]))
    if hosting.none_hosted:
        return ReponseApi(donnees=[], meta=meta)
    # Only the branches of the hosted sources are read (partial aggregate).
    sources = [key for key in ("transports", "gares") if hosting.hosts(key)]
    rows = await transports_a_proximite(
        db, lat, lon, rayon_m=rayon_m, limit=limite, sources=sources
    )
    resultats = [ResultatArret(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=meta)
