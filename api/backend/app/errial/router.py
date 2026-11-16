"""FastAPI router for ERRIAL proxy endpoint (Etat des Risques Reglementaires)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.common.proxy import proxy_error_boundary
from app.common.proxy_coverage import is_partial
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.errial.client import (
    extract_avis_summary,
    query_avis,
    resolve_parcelle_by_coords,
)
from app.errial.schemas import ResultatErrial
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    hosted_datasets,
)

router = APIRouter(prefix="/v1", tags=["Proximite"])


def _errial_departement(request: Request) -> str | None:
    """Coordinates win over ``code_insee`` + ``code_parcelle`` in the handler."""
    if "lat" in request.query_params and "lon" in request.query_params:
        return None
    return code_insee_departement(request)


@router.get(
    "/errial",
    response_model=ReponseApi[ResultatErrial],
    summary="Etat des risques reglementaires (ERRIAL)",
    description=(
        "Etat des risques reglementaires pour une parcelle — obligation "
        "legale ERP/IAL pour les transactions immobilieres. "
        "Inclut sismicite, radon, argiles, PPR, pollution (BASIAS/BASOL/SIS/ICPE), "
        "nucleaire, bruit, trait de cote, canalisations. "
        "Recherche par coordonnees GPS OU par code INSEE + parcelle. "
        "Proxy ERRIAL Georisques."
    ),
)
async def get_errial(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("errial", departement=_errial_departement)),
    ],
    lat: Annotated[
        float | None,
        Query(
            ge=-90,
            le=90,
            description="Latitude WGS-84 (auto-resolution parcelle)",
            example=48.8566,
        ),
    ] = None,
    lon: Annotated[
        float | None,
        Query(
            ge=-180,
            le=180,
            description="Longitude WGS-84 (auto-resolution parcelle)",
            example=2.3522,
        ),
    ] = None,
    code_insee: Annotated[
        str | None,
        Query(
            description="Code INSEE commune (requis avec code_parcelle)",
            example="75101",
        ),
    ] = None,
    code_parcelle: Annotated[
        str | None,
        Query(
            description=(
                "Code parcelle : prefixe-section-numero (requis avec code_insee)"
            ),
            example="000-AB-0069",
        ),
    ] = None,
) -> ReponseApi[ResultatErrial]:
    """Fetch parcel-level regulatory risk assessment.

    Two modes:
    - By coordinates: provide lat + lon (auto-resolves parcel)
    - By parcel: provide code_insee + code_parcelle
    """
    # Validate input combinations
    has_coords = lat is not None and lon is not None
    has_parcelle = code_insee is not None and code_parcelle is not None

    if not has_coords and not has_parcelle:
        raise HTTPException(
            status_code=422,
            detail="Fournir (lat + lon) ou (code_insee + code_parcelle)",
        )

    if hosting.none_hosted:
        return ReponseApi(
            donnees=ResultatErrial(), meta=hosting.meta(get_meta_source("errial"))
        )

    async with proxy_error_boundary("ERRIAL"):
        # Resolve parcel from coordinates if needed
        if has_coords:
            resolved = await resolve_parcelle_by_coords(lat, lon)  # type: ignore[arg-type]
            if not resolved:
                raise HTTPException(
                    status_code=404,
                    detail="Aucune parcelle trouvee a ces coordonnees",
                )
            code_insee, parcelle_formatted = resolved
        else:
            parcelle_formatted = f"{code_parcelle}@{code_insee}"

        # Fetch full risk assessment
        raw = await query_avis(code_insee, parcelle_formatted)  # type: ignore[arg-type]

        cleaned = extract_avis_summary(raw)
        resultat = ResultatErrial(**cleaned)
    return ReponseApi(
        statut="partiel" if is_partial(resultat.sources) else "ok",
        donnees=resultat,
        meta=get_meta_source("errial"),
    )
