"""FastAPI router for Zonage ABC housing tension zone endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
    real_departement,
)
from app.zonage_abc.queries import (
    zonage_par_codes,
    zonage_par_departement,
)
from app.zonage_abc.schemas import ResultatZonageAbc

router = APIRouter(prefix="/v1", tags=["Commune"])


def _zonage_departement(request: Request) -> str | None:
    """``code_departement`` wins over ``code_insee``, as in the handler."""
    if "code_departement" in request.query_params:
        return real_departement(request.query_params["code_departement"])
    return code_insee_departement(request)


@router.get(
    "/zonage-abc",
    response_model=ReponseApi[list[ResultatZonageAbc]],
    summary="Zonage ABC (tension immobiliere)",
    description=(
        "Classification des communes en zones A bis, A, B1, B2, C "
        "selon le niveau de tension du marche immobilier. "
        "Determine l'eligibilite Pinel, PTZ, et autres dispositifs."
    ),
)
async def get_zonage_abc(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("zonage_abc", departement=_zonage_departement)),
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
    code_departement: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=3,
            description=(
                "Code departement (retourne toutes les communes du departement)"
            ),
            example="75",
        ),
    ] = None,
) -> ReponseApi[list[ResultatZonageAbc]]:
    """Query ABC zoning for communes.

    Provide one of: GPS coordinates (lat + lon), commune code
    (code_insee), or department code (code_departement).

    Source: Ministere de la Transition Ecologique.
    """
    if hosting.none_hosted:
        if code_departement is None:
            require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("zonage_abc")))

    has_dept = code_departement is not None

    if has_dept:
        rows = await zonage_par_departement(db, code_departement)  # type: ignore[arg-type]
    else:
        if lat is None and lon is None and code_insee is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Au moins un parametre requis: "
                    "(lat + lon), code_insee, ou code_departement"
                ),
            )
        loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)
        rows = await zonage_par_codes(db, loc.codes)

    resultats = [ResultatZonageAbc(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("zonage_abc"))
