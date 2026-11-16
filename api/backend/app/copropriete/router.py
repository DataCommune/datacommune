"""FastAPI router for RNIC copropriete endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.copropriete.queries import (
    copropriete_par_codes,
    copropriete_par_numero,
)
from app.copropriete.schemas import ResultatCopropriete
from app.database import get_db
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Commune"])


def _copropriete_departement(request: Request) -> str | None:
    """A registry number lookup carries no territory; else ``code_insee``."""
    if "numero" in request.query_params:
        return None
    return code_insee_departement(request)


@router.get(
    "/copropriete",
    response_model=ReponseApi[list[ResultatCopropriete]],
    summary="Coproprietes (RNIC)",
    description=(
        "Registre national des coproprietes: lots, syndic, "
        "periode de construction. Source : ANAH / RNIC. Le nom du syndic "
        "n'est servi que pour un syndic professionnel : un syndic benevole "
        "ou cooperatif est un coproprietaire, une personne physique."
    ),
)
async def get_copropriete(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("copropriete", departement=_copropriete_departement)),
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
            description="Code INSEE de la commune",
            example="75056",
        ),
    ] = None,
    numero: Annotated[
        str | None,
        Query(
            description="Numero d'immatriculation RNIC (recherche directe)",
            example="AB1234567",
        ),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=200, description="Nombre max de resultats", example=50),
    ] = 50,
) -> ReponseApi[list[ResultatCopropriete]]:
    """Query copropriete registry entries."""
    if hosting.none_hosted:
        if numero is None:
            require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("copropriete")))

    has_numero = numero is not None

    if has_numero:
        rows = await copropriete_par_numero(db, numero)  # type: ignore[arg-type]
    else:
        loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)
        rows = await copropriete_par_codes(db, loc.codes, limit=limite)

    # RGPD: the queries name a professional manager only.
    resultats = [ResultatCopropriete(**row) for row in rows]
    return ReponseApi(
        donnees=resultats,
        meta=get_meta_source("copropriete"),
    )
