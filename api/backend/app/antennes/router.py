"""FastAPI router for ANFR antennes endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.antennes.queries import (
    antennes_par_code_insee,
    antennes_par_point,
)
from app.antennes.schemas import ResultatAntenne
from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/antennes",
    response_model=ReponseApi[list[ResultatAntenne]],
    summary="Antennes relais (ANFR)",
    description=(
        "Antennes relais 2G/3G/4G/5G par operateur. "
        "Recherche par coordonnees GPS (rayon) ou "
        "par code INSEE. Source : ANFR."
    ),
)
async def get_antennes(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("antennes", departement=code_insee_departement)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float | None,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=47.2184),
    ] = None,
    lon: Annotated[
        float | None,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=-1.5536),
    ] = None,
    code_insee: Annotated[
        str | None,
        Query(
            min_length=5,
            max_length=5,
            description="Code INSEE de la commune",
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
    limite: Annotated[
        int,
        Query(ge=1, le=200, description="Nombre max de resultats", example=50),
    ] = 50,
) -> ReponseApi[list[ResultatAntenne]]:
    """Query mobile network antennas."""
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("antennes")))

    loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)

    if loc.mode == "code_insee":
        rows = await antennes_par_code_insee(db, loc.codes, limit=limite)
    else:
        rows = await antennes_par_point(
            db, loc.lat, loc.lon, rayon_m=rayon_m, limit=limite
        )

    resultats = [ResultatAntenne(**row) for row in rows]
    return ReponseApi(
        donnees=resultats,
        meta=get_meta_source("antennes"),
    )
