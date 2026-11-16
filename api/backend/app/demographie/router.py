"""FastAPI router for Recensement demographie endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.demographie.queries import demographie_par_codes
from app.demographie.schemas import ResultatDemographie
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Commune"])


@router.get(
    "/demographie",
    response_model=ReponseApi[list[ResultatDemographie]],
    summary="Demographie (recensement INSEE)",
    description=(
        "Population, menages, logements et densite par commune. "
        "Derniere observation disponible pour chaque code commune ou arrondissement ; "
        "le champ annee de chaque ligne indique son millesime propre. "
        "Les valeurs nulles et les zeros du dernier millesime sont conserves. "
        "Source : INSEE Recensement de la population."
    ),
)
async def get_demographie(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("demographie", departement=code_insee_departement)),
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
) -> ReponseApi[list[ResultatDemographie]]:
    """Query the latest local population/housing observation for each exact code."""
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("demographie")))

    loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)
    rows = await demographie_par_codes(db, loc.codes)

    resultats = [ResultatDemographie(**row) for row in rows]
    return ReponseApi(
        donnees=resultats,
        meta=get_meta_source("demographie"),
    )
