"""FastAPI router for MAJIC personnes morales (legal entity parcels)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.pagination import build_page, decode_pagination
from app.common.schemas import PaginationMeta, ReponsePaginee
from app.common.sources import get_meta_source
from app.database import get_db
from app.foncier_pm.queries import foncier_pm_par_codes, foncier_pm_par_point
from app.foncier_pm.schemas import ResultatFoncierPm
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Parcelle"])


@router.get(
    "/foncier-pm",
    response_model=ReponsePaginee[ResultatFoncierPm],
    summary="Parcelles des personnes morales (MAJIC)",
    description=(
        "Proprietes foncieres detenues par des personnes morales (entreprises, "
        "collectivites, associations). Source : fichiers MAJIC de la DGFiP. "
        "Filtrable par SIREN ou raison sociale. Pagination par curseur."
    ),
)
async def get_foncier_pm(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("foncier_pm", departement=code_insee_departement)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float | None,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ] = None,
    lon: Annotated[
        float | None,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ] = None,
    code_insee: Annotated[
        str | None,
        Query(
            min_length=5,
            max_length=5,
            description="Code INSEE de la commune (5 chiffres)",
            example="75056",
        ),
    ] = None,
    rayon_m: Annotated[
        float,
        Query(
            ge=50,
            le=5000,
            description="Rayon de recherche en metres (mode GPS)",
            example=500,
        ),
    ] = 500.0,
    siren: Annotated[
        str | None,
        Query(
            max_length=15,
            description="Filtrer par numero SIREN",
            example="552032534",
        ),
    ] = None,
    denomination: Annotated[
        str | None,
        Query(
            description="Recherche par raison sociale (contient)",
            example="TOTAL",
        ),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=100, description="Nombre de resultats par page", example=20),
    ] = 20,
    curseur: Annotated[
        str | None,
        Query(description="Curseur de pagination (reponse precedente)"),
    ] = None,
) -> ReponsePaginee[ResultatFoncierPm]:
    """Query legal entity property ownership from MAJIC PM data.

    Supports commune code or GPS + radius lookup.  GPS mode resolves
    communes via the geo_communes referential (foncier_pm has no
    geometry column; the cadastre is never read for it).  Optional SIREN
    and denomination filters.
    """
    offset = decode_pagination(curseur)
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponsePaginee(
            donnees=[],
            pagination=PaginationMeta(curseur_suivant=None, taille=0, limite=limite),
            meta=hosting.meta(get_meta_source("foncier_pm")),
        )

    loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)

    if loc.mode == "gps":
        rows = await foncier_pm_par_point(
            db,
            lat=loc.lat,  # type: ignore[arg-type]
            lon=loc.lon,  # type: ignore[arg-type]
            rayon_m=rayon_m,
            siren=siren,
            denomination=denomination,
            limit=limite + 1,
            offset=offset,
        )
    else:
        rows = await foncier_pm_par_codes(
            db,
            codes=loc.codes,
            siren=siren,
            denomination=denomination,
            limit=limite + 1,
            offset=offset,
        )

    resultats, pagination = build_page(rows, ResultatFoncierPm, limite, offset)
    meta = get_meta_source("foncier_pm")
    return ReponsePaginee(donnees=resultats, pagination=pagination, meta=meta)
