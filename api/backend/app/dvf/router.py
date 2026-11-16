"""FastAPI router for DVF property transaction endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.ban.queries import _RE_POSTAL, GeocodageIndisponibleError, geocoder
from app.common.exceptions import ErreurApiError
from app.common.pagination import build_page, decode_pagination
from app.common.schemas import MetaReponse, PaginationMeta, ReponsePaginee
from app.common.sources import get_meta_source
from app.database import get_db
from app.dvf.queries import dvf_par_adresse, dvf_par_point
from app.dvf.schemas import (
    ProblemeDvfAdresse,
    RechercheDvfAdresse,
    ReponseDvfAdresse,
    ResultatDvf,
)
from app.scope.dependencies import (
    RouteHosting,
    commune_departement,
    get_instance_scope,
    hosted_datasets,
    hosting_at_point,
    real_departement,
    route_hosting,
)
from app.scope.model import InstanceScope
from app.zone.queries import postal_code_departement

router = APIRouter(prefix="/v1", tags=["Parcelle"])

# C52 (B1): radius of the proximity search around the retained point, served
# in ``recherche.rayon_m`` (the rapprochement itself is unchanged).
RAYON_ADRESSE_M = 50.0
_NO_STORE = {"Cache-Control": "no-store"}


@router.get(
    "/dvf",
    response_model=ReponsePaginee[ResultatDvf],
    summary="Transactions immobilieres (DVF)",
    description=(
        "Demandes de valeurs foncieres : prix, dates, types de biens et surfaces "
        "des transactions immobilieres. Filtrable par annee, prix, type de local. "
        "Pagination par curseur."
    ),
)
async def get_dvf(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("dvf"))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=47.2184),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=-1.5536),
    ],
    rayon_m: Annotated[
        float,
        Query(ge=50, le=5000, description="Rayon de recherche en metres", example=500),
    ] = 500.0,
    annee_min: Annotated[
        int | None,
        Query(description="Annee minimum (incluse)", example=2020),
    ] = None,
    annee_max: Annotated[
        int | None,
        Query(description="Annee maximum (incluse)", example=2024),
    ] = None,
    prix_min: Annotated[
        float | None,
        Query(description="Prix minimum en euros (inclus)", example=100000),
    ] = None,
    prix_max: Annotated[
        float | None,
        Query(description="Prix maximum en euros (inclus)", example=500000),
    ] = None,
    type_local: Annotated[
        str | None,
        Query(
            description=(
                "Type de local : Appartement, Maison, Dependance, Local industriel"
            ),
            example="Appartement",
        ),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=100, description="Nombre de resultats par page", example=20),
    ] = 20,
    curseur: Annotated[
        str | None,
        Query(description="Curseur de pagination (reponse precedente)"),
    ] = None,
) -> ReponsePaginee[ResultatDvf]:
    """Query DVF property transactions near a GPS point.

    Supports spatial filtering (radius), temporal filtering (year range),
    price filtering, and property type filtering. Results are paginated
    via cursor-based pagination and ordered by distance from query point.
    """
    offset = decode_pagination(curseur)

    # C52 (B2): hosted somewhere is not hosted here; the departement of the
    # commune covering the point qualifies the coverage before any read.
    if not hosting.none_hosted:
        hosting = await hosting_at_point(db, scope, hosting, lat, lon)
    if hosting.none_hosted:
        return ReponsePaginee(
            donnees=[],
            pagination=PaginationMeta(curseur_suivant=None, taille=0, limite=limite),
            meta=hosting.meta(get_meta_source("dvf")),
        )

    rows = await dvf_par_point(
        db,
        lat,
        lon,
        rayon_m=rayon_m,
        annee_min=annee_min,
        annee_max=annee_max,
        prix_min=prix_min,
        prix_max=prix_max,
        type_local=type_local,
        limit=limite + 1,
        offset=offset,
    )

    resultats, pagination = build_page(rows, ResultatDvf, limite, offset)

    meta = get_meta_source("dvf")
    return ReponsePaginee(donnees=resultats, pagination=pagination, meta=meta)


@router.get(
    "/dvf/adresse",
    response_model=ReponseDvfAdresse,
    summary="Ventes DVF à proximité d'une adresse géocodée",
    description=(
        "Recherche de proximité, pas un historique : l'adresse saisie est "
        "géocodée sur la Base Adresse Nationale de l'instance et son premier "
        "résultat est le point retenu. La réponse liste les ventes DVF dont la "
        f"position est à moins de {RAYON_ADRESSE_M:g} m de ce point, des plus "
        "récentes aux plus anciennes. Une vente d'un bâtiment voisin situé dans "
        "ce rayon peut figurer ; une vente de l'adresse positionnée plus loin "
        "peut manquer. "
        "Natures listées : « Vente » et « Vente en l'état futur d'achèvement » "
        "(VEFA, signalée par `hors_stats_vefa`) ; « Vente terrain à bâtir », "
        "échange, adjudication et expropriation sont exclues. "
        "Aucune vente n'est rattachée à un bâtiment ni à un logement. "
        "`recherche` rend l'état, l'adresse BAN retenue, son point et le "
        "rayon ; sans adresse retenue (`adresse_non_trouvee`), la liste vide "
        "n'est pas un zéro vente. Hors couverture, `meta.hebergement` donne le "
        "motif. Pour les ventes rattachées à une adresse BAN par l'adresse "
        "déclarée, utiliser `/v1/adresse/{ban_id}/ventes-declarees`. "
        "Pagination par curseur."
    ),
    responses={
        503: {
            "description": "Géocodage momentanément indisponible (panne SQL).",
            "content": {
                "application/problem+json": {
                    "schema": ProblemeDvfAdresse.model_json_schema()
                }
            },
        }
    },
)
async def get_dvf_adresse(
    # The address is geocoded on the BAN (adresses): a source too (D-23).
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("dvf", "ban"))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str,
        Query(
            min_length=5,
            description="Adresse a rechercher (geocodee automatiquement)",
            example="12 Rue de la Paix 75002 Paris",
        ),
    ],
    annee_min: Annotated[
        int | None,
        Query(description="Annee minimum (incluse)", example=2020),
    ] = None,
    annee_max: Annotated[
        int | None,
        Query(description="Annee maximum (incluse)", example=2024),
    ] = None,
    prix_min: Annotated[
        float | None,
        Query(description="Prix minimum en euros (inclus)", example=100000),
    ] = None,
    prix_max: Annotated[
        float | None,
        Query(description="Prix maximum en euros (inclus)", example=500000),
    ] = None,
    type_local: Annotated[
        str | None,
        Query(
            description=(
                "Type de local : Appartement, Maison, Dependance, Local industriel"
            ),
            example="Appartement",
        ),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=100, description="Nombre de resultats par page", example=50),
    ] = 50,
    curseur: Annotated[
        str | None,
        Query(description="Curseur de pagination (reponse precedente)"),
    ] = None,
) -> ReponseDvfAdresse:
    """DVF sales within ``RAYON_ADRESSE_M`` of the retained geocoded point (C52).

    A proximity search, never the history of an address nor of a building:
    the first geocoder result is the retained point, the sales are those
    positioned within the radius, newest first. No retained address is
    ``adresse_non_trouvee`` (no sale read, not a zero sale); a geocoder
    outage is a 503 that never quotes the input (SEM-01).
    """
    vide = PaginationMeta(curseur_suivant=None, taille=0, limite=limite)
    # Without dvf nothing to serve; without ban no address to locate the
    # sales: both out of coverage with their reason, no table read.
    if not (hosting.hosts("dvf") and hosting.hosts("ban")):
        return ReponseDvfAdresse(
            donnees=[],
            pagination=vide,
            meta=MetaReponse.model_validate(hosting.meta(get_meta_source("dvf"))),
        )

    # The problem is raised after the try, detached from the outage (C59 B1):
    # it is thrown into get_db, where a failing close would chain it to a log.
    geo_rows: list[dict] | None
    try:
        geo_rows = await geocoder(db, q, limit=1, erreurs="lever")
    except GeocodageIndisponibleError:
        geo_rows = None
    if geo_rows is None:
        raise ErreurApiError(
            503,
            "Geocodage momentanement indisponible",
            headers=_NO_STORE,
            type_code="geocodage_indisponible",
        ) from None
    if not geo_rows:
        # C52 (B2): no retained address; a postal code naming a real
        # departement out of coverage says so. The BAN read above is never
        # skipped because of a postal prefix.
        postal = _RE_POSTAL.search(q)
        departement = (
            real_departement(postal_code_departement(postal.group(1)))
            if postal
            else None
        )
        if departement is not None:
            hosting = route_hosting(hosting.hostings, scope, departement)
        return ReponseDvfAdresse(
            donnees=[],
            pagination=vide,
            meta=MetaReponse.model_validate(hosting.meta(get_meta_source("dvf"))),
            recherche=RechercheDvfAdresse(
                etat="adresse_non_trouvee", rayon_m=RAYON_ADRESSE_M
            ),
        )

    addr = geo_rows[0]
    recherche = RechercheDvfAdresse(
        etat="point_retenu",
        rayon_m=RAYON_ADRESSE_M,
        ban_id=addr.get("ban_id"),
        code_commune=addr.get("code_commune"),
        latitude=addr["latitude"],
        longitude=addr["longitude"],
    )
    # C52 (B2): the coverage of the departement of the retained address.
    hosting = route_hosting(
        hosting.hostings, scope, commune_departement(addr.get("code_commune"))
    )
    if not (hosting.hosts("dvf") and hosting.hosts("ban")):
        return ReponseDvfAdresse(
            donnees=[],
            pagination=vide,
            meta=MetaReponse.model_validate(hosting.meta(get_meta_source("dvf"))),
            recherche=recherche,
        )
    offset = decode_pagination(curseur)

    rows = await dvf_par_adresse(
        db,
        lat=addr["latitude"],
        lon=addr["longitude"],
        rayon_m=RAYON_ADRESSE_M,
        annee_min=annee_min,
        annee_max=annee_max,
        prix_min=prix_min,
        prix_max=prix_max,
        type_local=type_local,
        limit=limite + 1,
        offset=offset,
    )

    resultats, pagination = build_page(rows, ResultatDvf, limite, offset)

    return ReponseDvfAdresse(
        donnees=resultats,
        pagination=pagination,
        meta=MetaReponse.model_validate(get_meta_source("dvf")),
        recherche=recherche,
    )
