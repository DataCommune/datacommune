"""FastAPI router for BPE equipements endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import require_location_params, resolve_location
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.equipements.queries import (
    equipements_par_code_insee,
    equipements_par_point,
)
from app.equipements.schemas import ResultatEquipement
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Proximite"])


@router.get(
    "/equipements",
    response_model=ReponseApi[list[ResultatEquipement]],
    summary="Equipements (BPE INSEE)",
    description=(
        "Base permanente des equipements: commerces, sante, "
        "enseignement, sports, transports. Recherche par "
        "coordonnees GPS (rayon) ou code INSEE. "
        "Source : INSEE BPE."
    ),
)
async def get_equipements(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("equipements", departement=code_insee_departement)),
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
    domaine: Annotated[
        str | None,
        Query(
            description=(
                "Filtre par domaine : commerces, sante_social, "
                "enseignement, sports_loisirs_culture, "
                "transports, services_particuliers, tourisme"
            ),
            example="sante_social",
        ),
    ] = None,
    rayon_m: Annotated[
        int,
        Query(
            ge=100,
            le=10000,
            description="Rayon de recherche en metres",
            example=1000,
        ),
    ] = 1000,
    limite: Annotated[
        int,
        Query(ge=1, le=200, description="Nombre max de resultats", example=50),
    ] = 50,
) -> ReponseApi[list[ResultatEquipement]]:
    """Query permanent facilities."""
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("equipements")))

    loc = await resolve_location(db, lat=lat, lon=lon, code_insee=code_insee)

    if loc.mode == "code_insee":
        rows = await equipements_par_code_insee(
            db, loc.codes, domaine=domaine, limit=limite
        )
    else:
        rows = await equipements_par_point(
            db, loc.lat, loc.lon, rayon_m=rayon_m, domaine=domaine, limit=limite
        )

    resultats = [ResultatEquipement(**row) for row in rows]
    return ReponseApi(
        donnees=resultats,
        meta=get_meta_source("equipements"),
    )
