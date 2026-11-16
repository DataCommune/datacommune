"""FastAPI router for BDNB proxy endpoint (Base de Donnees Nationale des Batiments)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.bdnb.client import (
    geocode_bdnb,
    query_batiment_by_adresse,
    query_batiment_by_parcelle,
)
from app.bdnb.schemas import BatimentBDNB, ResultatBDNB
from app.common.proxy import proxy_error_boundary
from app.common.proxy_coverage import ProxyRows, bounded_rows
from app.common.schemas import ReponseApi
from app.common.sources import get_meta_source
from app.scope.dependencies import RouteHosting, hosted_datasets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Parcelle"])


@router.get(
    "/bdnb",
    response_model=ReponseApi[ResultatBDNB],
    summary="Donnees batiment BDNB",
    description=(
        "Informations detaillees sur les batiments (DPE, materiaux, annee "
        "construction, parcelles, proprietaires) via la Base de Donnees "
        "Nationale des Batiments (CSTB). "
        "Recherche par adresse OU par reference cadastrale. "
        "Proxy BDNB API — tier open gratuit (10 resultats max)."
    ),
)
async def get_bdnb(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("bdnb"))],
    adresse: Annotated[
        str | None,
        Query(
            description="Adresse a rechercher (geocodee via BAN puis croisee BDNB)",
            example="1 rue de la Paix 75002",
        ),
    ] = None,
    parcelle_id: Annotated[
        str | None,
        Query(
            description="Reference cadastrale (14 caracteres)",
            example="75102000AB0001",
        ),
    ] = None,
) -> ReponseApi[ResultatBDNB]:
    """Query BDNB building data by address or cadastral parcel ID.

    Exactly one of adresse or parcelle_id must be provided.
    When using adresse, the BDNB geocoder resolves the BAN key first,
    then fetches matching buildings.
    """
    if not adresse and not parcelle_id:
        raise HTTPException(
            status_code=422,
            detail="Fournir 'adresse' ou 'parcelle_id'",
        )
    if adresse and parcelle_id:
        raise HTTPException(
            status_code=422,
            detail="Fournir 'adresse' OU 'parcelle_id', pas les deux",
        )

    if hosting.none_hosted:
        return ReponseApi(
            donnees=ResultatBDNB(batiments=[]),
            meta=hosting.meta(get_meta_source("bdnb")),
        )

    batiments: list[dict] = []

    async with proxy_error_boundary("BDNB"):
        if parcelle_id:
            batiments = await query_batiment_by_parcelle(parcelle_id)

        elif adresse:
            # Step 1: geocode to get cle_interop_adr
            geo = await geocode_bdnb(adresse)
            if not geo or not geo.get("features"):
                raise HTTPException(
                    status_code=404,
                    detail="Adresse non trouvee par le geocodeur BDNB",
                )
            # Use the first result's cle_interop_adr
            first = geo["features"][0]
            cle = first.get("properties", {}).get("cle_interop_adr")
            if not cle:
                raise HTTPException(
                    status_code=404,
                    detail="Adresse geocodee mais sans cle BAN exploitable",
                )
            batiments = await query_batiment_by_adresse(cle)

        if not isinstance(batiments, ProxyRows):
            batiments = bounded_rows(batiments, limit=10)
        resultat = ResultatBDNB(
            batiments=[BatimentBDNB(**b) for b in batiments],
            total=batiments.coverage.total,
            nombre_retournes=len(batiments),
            couverture=batiments.coverage,
        )
    return ReponseApi(
        statut="partiel" if batiments.coverage.complet is not True else "ok",
        donnees=resultat,
        meta=get_meta_source("bdnb"),
    )
