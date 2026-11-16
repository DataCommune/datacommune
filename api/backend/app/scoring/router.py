"""FastAPI router for renovation scoring endpoints."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.unqualified_surfaces import SurfaceIndisponible, refuse, unavailable
from app.database import get_db

router = APIRouter(prefix="/v1", tags=["Commune"])

_VALID_ETIQUETTES = {"F", "G"}


@router.get(
    "/scoring/renov",
    response_model=SurfaceIndisponible,
    status_code=503,
    deprecated=True,
    summary="Scoring renovation (JSON)",
    description=(
        "Surface en quarantaine : aucun rapprochement ou prix de logement "
        "diffusé. HTTP 503 explicite, sans calcul ni requête scoring."
    ),
)
async def get_scoring_renov(
    db: Annotated[AsyncSession, Depends(get_db)],
    code_departement: Annotated[
        str,
        Query(
            min_length=2,
            max_length=3,
            description="Code département ; service en quarantaine",
            example="44",
        ),
    ],
    etiquette_dpe: Annotated[
        str | None,
        Query(
            description="Etiquette energetique : F (passoire) ou G (tres mauvais)",
            example="F",
        ),
    ] = None,
    commune: Annotated[
        str | None,
        Query(description="Nom de commune (filtre texte)", example="Nantes"),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=500, description="Nombre de resultats par page", example=100),
    ] = 100,
    curseur: Annotated[
        str | None,
        Query(description="Curseur de pagination (reponse precedente)"),
    ] = None,
) -> SurfaceIndisponible:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    # Validate etiquette if provided
    if etiquette_dpe is not None:
        etiquette_upper = etiquette_dpe.upper()
        if etiquette_upper not in _VALID_ETIQUETTES:
            raise HTTPException(
                status_code=422,
                detail="etiquette_dpe doit etre F ou G",
            )
        etiquette_dpe = etiquette_upper

    return unavailable("scoring_renov")


# -- CSV export helpers --------------------------------------------------------


def _rows_to_csv(rows: list[dict]) -> str:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    refuse("scoring_renov")


@router.get(
    "/scoring/renov-target",
    response_model=SurfaceIndisponible,
    status_code=503,
    deprecated=True,
    summary="Export scoring renovation (CSV)",
    description=(
        "Export indisponible pendant la quarantaine du scoring. "
        "HTTP 503 JSON, aucun fichier CSV ni montant diffusé."
    ),
)
async def export_scoring_renov_target(
    db: Annotated[AsyncSession, Depends(get_db)],
    code_departement: Annotated[
        str,
        Query(
            min_length=2,
            max_length=3,
            description="Code département ; service en quarantaine",
            example="44",
        ),
    ],
    etiquette_dpe: Annotated[
        str | None,
        Query(
            description="Etiquette DPE : F, G, ou F,G (multi-valeur)",
            example="F,G",
        ),
    ] = None,
    code_commune: Annotated[
        str | None,
        Query(
            min_length=5,
            max_length=5,
            description="Code INSEE commune",
            example="44109",
        ),
    ] = None,
    date_achat_min: Annotated[
        date | None,
        Query(
            description="Date achat minimum (YYYY-MM-DD)",
            example="2020-01-01",
        ),
    ] = None,
    date_achat_max: Annotated[
        date | None,
        Query(
            description="Date achat maximum (YYYY-MM-DD)",
            example="2024-12-31",
        ),
    ] = None,
    surface_min: Annotated[
        float | None,
        Query(ge=0, description="Surface habitable minimum en m2", example=50),
    ] = None,
    limite: Annotated[
        int,
        Query(
            ge=1,
            le=5000,
            description="Nombre max de lignes (export CSV)",
            example=5000,
        ),
    ] = 5000,
) -> SurfaceIndisponible:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    # Parse and validate etiquette_dpe (comma-separated multi-value)
    etiquette_list: list[str] | None = None
    if etiquette_dpe is not None:
        etiquette_list = [e.strip().upper() for e in etiquette_dpe.split(",")]
        for e in etiquette_list:
            if e not in _VALID_ETIQUETTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"etiquette_dpe invalide: '{e}'. Valeurs acceptees: F, G",
                )

    return unavailable("scoring_renov")
