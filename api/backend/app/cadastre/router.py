"""FastAPI router for cadastre parcel lookup endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.cadastre.queries import parcelle_par_point, parcelle_par_reference
from app.cadastre.schemas import ResultatParcelle
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.scope.dependencies import (
    RouteHosting,
    commune_departement,
    get_instance_scope,
    hosted_datasets,
    hosting_at_point,
)
from app.scope.model import InstanceScope

router = APIRouter(prefix="/v1", tags=["Parcelle"])


def _departement_de_la_reference(request: Request) -> str | None:
    """Departement of the INSEE prefix of the ``ref`` path parameter (C52, B2).

    No database read; a prefix naming no departement leaves the territory
    unknown, never a proof of « hors couverture » (02-07 rule).
    """
    return commune_departement(str(request.path_params.get("ref", ""))[:5])


@router.get(
    "/parcelle",
    response_model=ReponseApi[list[ResultatParcelle]],
    summary="Rechercher des parcelles",
    description=(
        "Recherche de parcelles cadastrales autour d'un point GPS dans un rayon donne. "
        "Retourne la reference, surface et geometrie."
    ),
)
async def get_parcelle_par_point(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("cadastre"))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ],
) -> ReponseApi[list[ResultatParcelle]]:
    """Find the cadastre parcel containing the given GPS point.

    Uses PostGIS ST_Intersects spatial join on the GIST-indexed
    geometrie column. Returns an empty list when no parcel matches.
    """
    # C52 (B2): the departement of the commune covering the point qualifies
    # the coverage before any read.
    if not hosting.none_hosted:
        hosting = await hosting_at_point(db, scope, hosting, lat, lon)
    if hosting.none_hosted:
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("cadastre")))

    rows = await parcelle_par_point(db, lat, lon)
    resultats = [ResultatParcelle(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("cadastre"))


@router.get(
    "/parcelle/{ref}",
    response_model=ReponseApi[list[ResultatParcelle]],
    summary="Parcelle par reference",
    description=(
        "Recupere une parcelle cadastrale par sa reference complete "
        "(14 caracteres, ex: 75101000AB0001)."
    ),
)
async def get_parcelle_par_reference(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("cadastre", departement=_departement_de_la_reference)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    ref: Annotated[
        str,
        Path(
            pattern=r"^[0-9]{5}[0-9]{3}[A-Z0-9]{2}[0-9]{4}$",
            description=(
                "Reference cadastrale 14 caracteres : "
                "5 commune + 3 prefixe + 2 section + 4 numero"
            ),
            example="75101000AB0001",
        ),
    ],
) -> ReponseApi[list[ResultatParcelle]]:
    """Look up a cadastre parcel by its 14-character reference.

    Format: 5 digits commune + 3 digits prefix + 2 alphanumeric section
    + 4 digits number. Returns 422 if format is invalid, empty list if
    unknown reference.
    """
    if hosting.none_hosted:
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("cadastre")))

    rows = await parcelle_par_reference(db, ref)
    resultats = [ResultatParcelle(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("cadastre"))
