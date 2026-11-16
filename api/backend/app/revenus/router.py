"""FastAPI router for Filosofi revenus endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.revenus.queries import revenus_par_codes
from app.revenus.schemas import ResultatRevenus
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Commune"])


@router.get(
    "/revenus",
    response_model=ReponseApi[list[ResultatRevenus]],
    summary="Revenus déclarés et pauvreté (Filosofi)",
    description=(
        "Revenus déclarés annuels par unité de consommation : médiane, déciles, "
        "quartiles et indice de Gini (Filosofi DEC). Le taux de pauvreté "
        "repose séparément sur le revenu disponible après redistribution "
        "(Filosofi DISP_PAUVRES), au seuil de 60 %. "
        "Dernière année disponible par code commune ou arrondissement ; "
        "le millésime figure dans le champ annee de chaque ligne et peut varier "
        "entre les codes. Les valeurs nulles du dernier millésime sont conservées. "
        "Source : INSEE Filosofi."
    ),
)
async def get_revenus(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("revenus", departement=code_insee_departement)),
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
) -> ReponseApi[list[ResultatRevenus]]:
    """Query household income indicators per commune."""
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("revenus")))

    loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)
    rows = await revenus_par_codes(db, loc.codes)

    resultats = [ResultatRevenus(**row) for row in rows]
    return ReponseApi(
        donnees=resultats,
        meta=get_meta_source("revenus"),
    )
