"""FastAPI router for RNB proxy endpoint (Referentiel National des Batiments)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.common.proxy import proxy_error_boundary
from app.common.proxy_coverage import ProxyRows, bounded_rows
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.rnb.client import (
    query_by_address,
    query_by_coords,
    query_by_parcelle,
    query_by_rnb_id,
)
from app.rnb.schemas import BatimentRNB, ResultatRNB
from app.scope.dependencies import RouteHosting, hosted_datasets

router = APIRouter(prefix="/v1", tags=["Parcelle"])


@router.get(
    "/rnb",
    response_model=ReponseApi[ResultatRNB],
    summary="Referentiel National des Batiments (RNB)",
    description=(
        "Identifiant unique batiment (pivot BDNB/DPE/cadastre). "
        "Recherche par coordonnees, parcelle, adresse ou ID RNB. "
        "Proxy RNB API (beta.gouv.fr) — gratuit, sans authentification."
    ),
)
async def get_rnb(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("rnb"))],
    lat: Annotated[
        float | None,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ] = None,
    lon: Annotated[
        float | None,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ] = None,
    rayon_m: Annotated[
        int | None,
        Query(
            ge=1,
            le=1000,
            description="Rayon en metres (avec lat/lon)",
            example=50,
        ),
    ] = 50,
    parcelle_id: Annotated[
        str | None,
        Query(
            description="Reference cadastrale (14 caracteres)",
            example="75102000AB0069",
        ),
    ] = None,
    adresse: Annotated[
        str | None,
        Query(
            description="Adresse texte libre (geocodee via BAN)",
            example="1 rue de la Paix Paris",
        ),
    ] = None,
    rnb_id: Annotated[
        str | None,
        Query(description="Identifiant unique RNB du batiment"),
    ] = None,
) -> ReponseApi[ResultatRNB]:
    """Query the national building registry.

    Provide exactly one search criterion: lat+lon, parcelle_id, adresse, or rnb_id.
    """
    modes = sum(
        [
            lat is not None and lon is not None,
            parcelle_id is not None,
            adresse is not None,
            rnb_id is not None,
        ]
    )
    if modes == 0:
        raise HTTPException(
            status_code=422,
            detail="Fournir (lat + lon), parcelle_id, adresse, ou rnb_id",
        )
    if modes > 1:
        raise HTTPException(
            status_code=422,
            detail="Un seul critere de recherche a la fois",
        )

    if hosting.none_hosted:
        return ReponseApi(
            donnees=ResultatRNB(batiments=[]), meta=hosting.meta(get_meta_source("rnb"))
        )

    batiments_raw: list[dict] | None = None

    async with proxy_error_boundary("RNB"):
        if rnb_id:
            single = await query_by_rnb_id(rnb_id)
            rows = [single] if single is not None else []
            batiments_raw = bounded_rows(rows, limit=1, total=len(rows))

        elif parcelle_id:
            batiments_raw = await query_by_parcelle(parcelle_id)

        elif adresse:
            batiments_raw = await query_by_address(adresse)

        elif lat is not None and lon is not None:
            batiments_raw = await query_by_coords(lat, lon, rayon_m or 50)

        # None from a client means an unusable upstream response -> 502.
        if batiments_raw is None:
            raise ValueError("empty RNB response")
        if not isinstance(batiments_raw, ProxyRows):
            batiments_raw = bounded_rows(batiments_raw, limit=50)
        resultat = ResultatRNB(
            batiments=[BatimentRNB(**b) for b in batiments_raw],
            total=batiments_raw.coverage.total,
            nombre_retournes=len(batiments_raw),
            couverture=batiments_raw.coverage,
        )
    return ReponseApi(
        statut="partiel" if batiments_raw.coverage.complet is not True else "ok",
        donnees=resultat,
        meta=get_meta_source("rnb"),
    )
