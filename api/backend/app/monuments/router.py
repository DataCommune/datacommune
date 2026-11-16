"""FastAPI router for Monuments Historiques endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import build_page, decode_pagination
from app.common.schemas import PaginationMeta, ReponsePaginee
from app.common.sources import get_meta_source
from app.database import get_db
from app.monuments.queries import monuments_par_point
from app.monuments.schemas import ResultatMonument
from app.scope.dependencies import RouteHosting, hosted_datasets

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/monuments",
    response_model=ReponsePaginee[ResultatMonument],
    summary="Monuments historiques",
    description=(
        "Monuments classes et inscrits au titre des monuments historiques. "
        "Recherche spatiale par rayon autour d'un point GPS."
    ),
)
async def get_monuments(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("monuments"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ],
    rayon_m: Annotated[
        float,
        Query(
            ge=100,
            le=10000,
            description="Rayon de recherche en metres",
            example=1000,
        ),
    ] = 1000.0,
    limite: Annotated[
        int,
        Query(ge=1, le=100, description="Nombre de resultats par page", example=20),
    ] = 20,
    curseur: Annotated[
        str | None,
        Query(description="Curseur de pagination (reponse precedente)"),
    ] = None,
) -> ReponsePaginee[ResultatMonument]:
    """Query Monuments Historiques near a GPS point.

    Returns protected heritage sites with denomination, protection status,
    and location. Results are paginated via cursor-based pagination and
    ordered by distance from query point.
    """
    offset = decode_pagination(curseur)

    if hosting.none_hosted:
        return ReponsePaginee(
            donnees=[],
            pagination=PaginationMeta(curseur_suivant=None, taille=0, limite=limite),
            meta=hosting.meta(get_meta_source("monuments")),
        )

    rows = await monuments_par_point(
        db,
        lat,
        lon,
        rayon_m=rayon_m,
        limit=limite + 1,
        offset=offset,
    )

    resultats, pagination = build_page(rows, ResultatMonument, limite, offset)

    meta = get_meta_source("monuments")
    return ReponsePaginee(donnees=resultats, pagination=pagination, meta=meta)
