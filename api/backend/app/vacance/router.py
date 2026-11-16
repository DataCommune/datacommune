"""FastAPI router for LOVAC vacance endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)
from app.vacance.queries import vacance_par_codes
from app.vacance.schemas import ResultatVacance

router = APIRouter(prefix="/v1", tags=["Commune"])


@router.get(
    "/vacance",
    response_model=ReponseApi[list[ResultatVacance]],
    summary="Logements vacants (LOVAC)",
    description=(
        "Taux de vacance, nombre de logements vacants "
        "par commune, dernière observation disponible pour chaque commune. "
        "Les taux du parc privé sont indisponibles lorsque leurs effectifs "
        "sont inconnus (notamment sous secret statistique). "
        "Source : CEREMA LOVAC."
    ),
)
async def get_vacance(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("vacance", departement=code_insee_departement)),
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
) -> ReponseApi[list[ResultatVacance]]:
    """Query vacant housing indicators per commune."""
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("vacance")))

    loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)
    rows = await vacance_par_codes(db, loc.codes)

    resultats = [ResultatVacance(**row) for row in rows]
    return ReponseApi(
        donnees=resultats,
        meta=get_meta_source("vacance"),
    )
