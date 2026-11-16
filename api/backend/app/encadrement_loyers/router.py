"""FastAPI router for Encadrement des loyers endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.unqualified_surfaces import SurfaceIndisponible, unavailable
from app.database import get_db

router = APIRouter(prefix="/v1", tags=["Parcelle"])


@router.get(
    "/encadrement-loyers",
    response_model=SurfaceIndisponible,
    status_code=503,
    deprecated=True,
    summary="Encadrement des loyers",
    description=(
        "Surface en quarantaine, y compris la sélection manuelle. "
        "Un centroïde proche ne prouve pas l'appartenance réglementaire. "
        "HTTP 503, aucun montant applicable diffusé."
    ),
)
async def get_encadrement_loyers(
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float | None,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ] = None,
    lon: Annotated[
        float | None,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ] = None,
    ville: Annotated[
        str | None,
        Query(
            description="Nom de la ville (actuellement Paris uniquement)",
            example="PARIS",
        ),
    ] = None,
    numero_quartier: Annotated[
        int | None,
        Query(
            description="Numero du quartier DRIHL (1 a 80 pour Paris)",
            example=1,
        ),
    ] = None,
) -> SurfaceIndisponible:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    has_gps = lat is not None and lon is not None
    has_quartier = ville is not None and numero_quartier is not None

    if not has_gps and not has_quartier:
        raise HTTPException(
            status_code=422,
            detail=("Parametres requis: (lat + lon) ou (ville + numero_quartier)"),
        )

    return unavailable("encadrement_loyers")
