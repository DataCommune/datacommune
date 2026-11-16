"""FastAPI router for broadband eligibility (connectivite) endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.connectivite.queries import eligibilite_par_adresse
from app.connectivite.schemas import ResultatEligibilite
from app.database import get_db
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Commune"])


@router.get(
    "/connectivite",
    response_model=ReponseApi[list[ResultatEligibilite]],
    summary="Eligibilite fibre et ADSL",
    description=(
        "Eligibilite a la fibre optique et a l'ADSL par adresse, "
        "selon les donnees Arcep. "
        "Retourne le debit max et les technologies disponibles."
    ),
)
async def get_connectivite(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("connectivite", departement=code_insee_departement)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    code_insee: Annotated[
        str,
        Query(
            min_length=5,
            max_length=5,
            description="Code INSEE de la commune (requis)",
            example="44109",
        ),
    ],
    nom_voie: Annotated[
        str | None,
        Query(
            description="Nom de voie (recherche floue via pg_trgm)",
            example="boulevard de la prairie",
        ),
    ] = None,
    numero: Annotated[
        str | None,
        Query(description="Numero de voie (match exact)", example="12"),
    ] = None,
) -> ReponseApi[list[ResultatEligibilite]]:
    """Query broadband eligibility (fibre/ADSL/THD) by address.

    Returns up to 50 eligibility records for buildings matching the
    given commune code and optional street/number filters.

    Source: Arcep Ma Connexion Internet (base_imb + eligibilite).
    """
    if hosting.none_hosted:
        return ReponseApi(
            donnees=[], meta=hosting.meta(get_meta_source("connectivite"))
        )

    rows = await eligibilite_par_adresse(db, code_insee, nom_voie, numero)
    resultats = [ResultatEligibilite(**row) for row in rows]
    return ReponseApi(donnees=resultats, meta=get_meta_source("connectivite"))
