"""FastAPI router for BD TOPO batiments endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.batiments.queries import batiments_par_point
from app.batiments.schemas import ResultatBatiment
from app.common.pagination import build_page, decode_pagination
from app.common.schemas import PaginationMeta, ReponsePaginee
from app.common.sources import get_meta_source
from app.database import get_db
from app.scope.dependencies import RouteHosting, hosted_datasets

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/batiments",
    response_model=ReponsePaginee[ResultatBatiment],
    summary="Batiments BD TOPO",
    description=(
        "Caracteristiques des batiments IGN : hauteur, usage, annee de construction, "
        "nombre d'etages. Recherche spatiale par rayon."
    ),
)
async def get_batiments(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("batiments"))],
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
        Query(ge=50, le=2000, description="Rayon de recherche en metres", example=200),
    ] = 200.0,
    limite: Annotated[
        int,
        Query(ge=1, le=100, description="Nombre de resultats par page", example=20),
    ] = 20,
    curseur: Annotated[
        str | None,
        Query(description="Curseur de pagination (reponse precedente)"),
    ] = None,
) -> ReponsePaginee[ResultatBatiment]:
    """Query BD TOPO buildings near a GPS point.

    Returns building height, number of floors, usage, and altitude data.
    Results are paginated via cursor-based pagination and ordered by
    distance from query point.
    """
    offset = decode_pagination(curseur)

    if hosting.none_hosted:
        return ReponsePaginee(
            donnees=[],
            pagination=PaginationMeta(curseur_suivant=None, taille=0, limite=limite),
            meta=hosting.meta(get_meta_source("batiments")),
        )

    rows = await batiments_par_point(
        db,
        lat,
        lon,
        rayon_m=rayon_m,
        limit=limite + 1,
        offset=offset,
    )

    resultats, pagination = build_page(rows, ResultatBatiment, limite, offset)

    meta = get_meta_source("batiments")
    return ReponsePaginee(donnees=resultats, pagination=pagination, meta=meta)
