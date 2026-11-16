"""FastAPI router for SITADEL permis endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.sources import get_meta_source
from app.database import get_db
from app.permis.queries import permis_par_codes
from app.permis.schemas import ReponsePermis
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Commune"])


@router.get(
    "/permis",
    response_model=ReponsePermis,
    status_code=503,
    summary="Permis de construire — source en quarantaine",
    description=(
        "Source locale observee dans le departement 93, couverture non qualifiee. "
        "HTTP 503 : donnees indisponibles, y compris le stock ancien. "
        "Une liste vide ne signifie pas absence de permis. "
        "date_depot reste compatible mais n'est pas fournie par cette source."
    ),
)
async def get_permis(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("permis", departement=code_insee_departement)),
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
    limite: Annotated[
        int,
        Query(ge=1, le=200, description="Nombre max de resultats", example=50),
    ] = 50,
) -> ReponsePermis:
    """Preserve parameter validation without querying an unqualified stock."""
    if code_insee is None and (lat is None or lon is None):
        raise HTTPException(
            status_code=422,
            detail="Au moins un parametre requis: (lat + lon) ou code_insee",
        )
    if hosting.none_hosted:
        return ReponsePermis(donnees=[], meta=hosting.meta(get_meta_source("permis")))

    rows = await permis_par_codes(db, [code_insee] if code_insee else [], limit=limite)
    return ReponsePermis(donnees=rows, meta=get_meta_source("permis"))
