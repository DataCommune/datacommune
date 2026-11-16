"""FastAPI router for Loyers rental price indicator endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.loyers.queries import loyers_par_codes
from app.loyers.schemas import ResultatLoyer
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Commune"])


@router.get(
    "/loyers",
    response_model=ReponseApi[list[ResultatLoyer]],
    summary="Indicateurs de loyers",
    description=(
        "Loyers d'annonce prédits en €/m²/mois, charges comprises, pour des biens "
        "types non meublés. Bornes de l'intervalle de prédiction à 95 %, maille "
        "et qualité du modèle lorsqu'elles ont été conservées à l'import. "
        "Les métadonnées absentes des anciens imports restent nulles. "
        "Dernière année disponible par code commune ou arrondissement et type "
        "d'habitat : le champ annee de chaque ligne porte son propre millésime. "
        "Les valeurs nulles du dernier millésime sont conservées. "
        "Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin."
    ),
)
async def get_loyers(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("loyers", departement=code_insee_departement)),
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
            description=("Code INSEE commune (5 chiffres). Prefere au GPS si fourni"),
            example="44109",
        ),
    ] = None,
) -> ReponseApi[list[ResultatLoyer]]:
    """Query rental price indicators per commune.

    Provide either GPS coordinates (lat + lon) or a commune code
    (code_insee). If both are provided, code_insee is preferred
    (direct lookup is faster than spatial resolution).

    Returns one row per exact commune/arrondissement code and housing type
    (appartement, maison, 1-2_pieces, 3+_pieces), each with its own latest year.
    A PLM parent query can include multiple arrondissement codes.

    Source: Estimations ANIL (Groupe SeLoger / leboncoin).
    """
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("loyers")))

    loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)
    rows = await loyers_par_codes(db, loc.codes)

    resultats = [ResultatLoyer(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("loyers"))
