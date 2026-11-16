"""FastAPI router for geocoding and reverse geocoding endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.ban.queries import geocoder, reverse_geocoder
from app.ban.schemas import ResultatGeocodage, ResultatReverseGeocodage
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.scope.dependencies import RouteHosting, hosted_datasets

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/geocode",
    response_model=ReponseApi[list[ResultatGeocodage]],
    summary="Geocoder une adresse",
    description=(
        "Convertit une adresse textuelle en coordonnees GPS. "
        "Recherche fuzzy sur 26M adresses BAN (pg_trgm + unaccent). "
        "Filtrable par code postal."
    ),
)
async def geocode(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("ban"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str,
        Query(
            min_length=3,
            description="Texte d'adresse a geocoder",
            example="12 rue de la Paix Paris",
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=20, description="Nombre de resultats (1-20)", example=5),
    ] = 5,
    code_postal: Annotated[
        str | None,
        Query(description="Filtre par code postal (5 chiffres)", example="75002"),
    ] = None,
) -> ReponseApi[list[ResultatGeocodage]]:
    """Geocode a text address and return matching results sorted by score.

    Uses pg_trgm trigram similarity on the search_text generated column.
    Returns an empty list (not 404) when no match is found.
    """
    if hosting.none_hosted:
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("ban")))

    rows = await geocoder(db, q, limit, code_postal)
    resultats = [ResultatGeocodage(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("ban"))


@router.get(
    "/reverse",
    response_model=ReponseApi[list[ResultatReverseGeocodage]],
    summary="Reverse geocoding",
    description=(
        "Trouve l'adresse la plus proche d'un point GPS (lat/lon). "
        "Recherche spatiale par rayon en metres."
    ),
)
async def reverse(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("ban"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=10, description="Nombre de resultats (1-10)", example=1),
    ] = 1,
    rayon_m: Annotated[
        float,
        Query(ge=1, le=5000, description="Rayon de recherche en metres", example=200),
    ] = 200.0,
) -> ReponseApi[list[ResultatReverseGeocodage]]:
    """Reverse geocode GPS coordinates to find the nearest address.

    Uses PostGIS KNN operator (<->) with ST_DWithin radius filter.
    Returns an empty list when no address exists within the search radius.
    """
    if hosting.none_hosted:
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("ban")))

    rows = await reverse_geocoder(db, lat, lon, limit, rayon_m)
    resultats = [ResultatReverseGeocodage(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("ban"))
