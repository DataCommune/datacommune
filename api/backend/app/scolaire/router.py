"""FastAPI router for Carte Scolaire endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.database import get_db
from app.scolaire.queries import college_de_secteur
from app.scolaire.schemas import ResultatScolaire
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Commune"])


@router.get(
    "/scolaire",
    response_model=ReponseApi[list[ResultatScolaire]],
    summary="Carte scolaire",
    description=(
        "College de rattachement d'une adresse selon la carte scolaire officielle. "
        "Recherche spatiale par point GPS."
    ),
)
async def get_scolaire(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("scolaire", departement=code_insee_departement)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    code_insee: Annotated[
        str,
        Query(
            min_length=5,
            max_length=5,
            description="Code INSEE de la commune (5 chiffres)",
            example="44109",
        ),
    ],
    nom_voie: Annotated[
        str | None,
        Query(
            description="Nom de la voie (pour matching segment pair/impair)",
            example="VICTOR HUGO",
        ),
    ] = None,
    numero: Annotated[
        int | None,
        Query(ge=0, description="Numero de voie", example=12),
    ] = None,
) -> ReponseApi[list[ResultatScolaire]]:
    """Query the sector college for an address.

    Provide the commune code (code_insee, required) and optionally
    the street name (nom_voie) and street number (numero) for
    precise segment matching.

    When nom_voie and numero are provided, the matching logic checks
    the street segment range and parity (pair/impair/tous).

    When only code_insee is provided, falls back to unique-sector
    lookup (communes with a single sector college).

    Source: Carte Scolaire des Colleges Publics (data.gouv.fr).
    """
    if hosting.none_hosted:
        return ReponseApi(donnees=[], meta=hosting.meta(get_meta_source("scolaire")))

    rows = await college_de_secteur(db, code_insee, nom_voie=nom_voie, numero=numero)
    resultats = [ResultatScolaire(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("scolaire"))
