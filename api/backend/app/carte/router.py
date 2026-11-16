"""``GET /v1/carte/{ban_id}/{couche}.png``: IGN map of an address, same origin.

Public route, mounted beside the health routes: an ``<img>`` tag carries no API
key. It takes no parameter of the visitor beyond the BAN key and the layer
name; any query string is ignored. The point comes from the BAN, as for the
report; the IGN request is built by :mod:`app.carte.proxy` alone.

``GET /v1/carte/parcelles/{z}/{x}/{y}`` (04.10-02): the parcels of one tile of
the interactive map, read in our base by :mod:`app.carte.parcelles`. Public
too, with a per-visitor bucket of its own: the middleware skips this prefix,
so a route declared here has no limit unless it spends one. The tile indices
are the only input; they are bounded and never reflected.

``GET /v1/carte/{ban_id}/contexte`` (04.10-02): where the map of an address
opens (BAN point, bounds, parcel of the point). Same bucket; never stored.

``GET /v1/carte/fond/{z}/{x}/{y}.png`` (04.10-04): one Plan IGN tile of the base
map, relayed by :mod:`app.carte.tuiles`. Public, with a third per-visitor
bucket. The indices are the only input; the tile is asked to the IGN only when
it lies in the hosted territory, so the route is not an open relay.

``GET /v1/carte/{ban_id}/parcelle/{id_parcelle}/ventes`` (04.10-05): the sales
of one parcel of the map of an address, read by :mod:`app.carte.ventes` with
the price rule of the address page. Public, with a fourth per-visitor bucket,
tighter than the others: the map must not become an export of DVF. It answers
only for a parcel around the address of the path; anything else is one
constant 404.
"""

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.documents import GenerationChangeeError, PageHorsLimitesError
from app.adresse.router import (
    BAN_ID_MAX_LENGTH,
    BAN_ID_PATTERN,
    GENERATION_PATTERN,
    NO_STORE,
    _probleme_doc,
    _probleme_ou_validation_doc,
    departement_de_la_cle,
    probleme,
)
from app.adresse.service import (
    AdresseInconnueError,
    AdresseIndisponibleError,
    VoieSansNumeroError,
    point_de_la_cle,
)
from app.adresse.ventes import VentesIndisponiblesError
from app.carte import proxy, tuiles
from app.carte.parcelles import (
    ParcellesIndisponiblesError,
    lire_contexte,
    lire_tuile,
)
from app.carte.schemas import ContexteCarte, TuileParcelles, VentesParcelleCarte
from app.carte.ventes import lire_ventes_de_la_parcelle
from app.common.exceptions import ErreurApiError
from app.common.schemas import MetaReponse, ProblemDetail, ReponseApi
from app.common.sources import get_meta_sources
from app.config import settings
from app.database import get_db
from app.middleware import _get_client_ip, cle_visiteur
from app.rate_limit import _check_rate
from app.scope.dependencies import RouteHosting, get_instance_scope, hosted_datasets
from app.scope.model import InstanceScope

logger = logging.getLogger(__name__)

carte_router = APIRouter(prefix="/v1", tags=["Adresse"])

IP_BUCKET_PREFIX = "carte:ip:"
# Apart from the bucket of the images: a map view is several tiles.
PARCELLES_BUCKET_PREFIX = "carte:parcelles:ip:"
# Apart from both: the tiles of the base map are images asked by the dozen.
FOND_BUCKET_PREFIX = "carte:fond:ip:"
# Apart from the three: the sales of a parcel, the tightest of them.
VENTES_BUCKET_PREFIX = "carte:ventes:ip:"
AMONT_BUCKET = "carte:amont"
RESOLUTION_BUCKET = "carte:resolution"

_ENTETES_IMAGE = {
    "Cache-Control": "public, max-age=86400",
    "X-Content-Type-Options": "nosniff",
}
_PROBLEME_GENERIQUE = ProblemDetail.model_json_schema()
# Lowest and highest zoom of the interactive map; a tile index is below 2**z.
ZOOM_MIN = 16
ZOOM_MAX = 19
_INDICE_MAX = 2**ZOOM_MAX - 1
# Title of the validation problem of the API (app/common/exceptions.py): an
# index beyond the grid of its zoom is refused as a path parameter is.
_TITRE_VALIDATION = "Erreur de validation des donnees"


def _trop_de_demandes(reset: int) -> ErreurApiError:
    return ErreurApiError(
        429,
        "Trop de demandes",
        "Reessayez dans quelques minutes.",
        headers={"Cache-Control": "no-store", "Retry-After": str(reset)},
    )


async def limite_carte(request: Request) -> None:
    """Per-visitor bucket of the route (60 a minute), spent before any read."""
    client_ip = _get_client_ip(request)
    allowed, _, reset = _check_rate(
        f"{IP_BUCKET_PREFIX}{cle_visiteur(client_ip)}", settings.carte_rate_limit_per_ip
    )
    if not allowed:
        logger.warning("carte_rate_limited", extra={"client_ip": client_ip})
        raise _trop_de_demandes(reset)


async def limite_parcelles(request: Request) -> None:
    """Per-visitor bucket of the JSON routes of the map, spent before any read.

    Their only limit: the navigation ceiling skips ``/v1/carte/`` and these
    routes are outside the quota of the address page (Q7).
    """
    client_ip = _get_client_ip(request)
    allowed, _, reset = _check_rate(
        f"{PARCELLES_BUCKET_PREFIX}{cle_visiteur(client_ip)}",
        settings.carte_parcelles_rate_limit_per_ip,
    )
    if not allowed:
        logger.warning("carte_parcelles_rate_limited", extra={"client_ip": client_ip})
        raise _trop_de_demandes(reset)


async def limite_fond(request: Request) -> None:
    """Per-visitor bucket of the base map tiles, spent before any read.

    Their only limit on the visitor's side, outside the quota of the address
    page (Q7) and apart from the buckets of the static images and of the
    parcels: a view is a dozen tiles and each move asks for more.
    """
    client_ip = _get_client_ip(request)
    allowed, _, reset = _check_rate(
        f"{FOND_BUCKET_PREFIX}{cle_visiteur(client_ip)}",
        settings.carte_fond_rate_limit_per_ip,
    )
    if not allowed:
        logger.warning("carte_fond_rate_limited", extra={"client_ip": client_ip})
        raise _trop_de_demandes(reset)


async def limite_ventes_carte(request: Request) -> None:
    """Per-visitor bucket of the sales of a parcel, spent before any read.

    Their only limit, outside the quota of the address page (Q7) and apart
    from the three other buckets of the map. Counted per visitor, whatever the
    address and the parcel: it is the walk from parcel to parcel it slows down.
    """
    client_ip = _get_client_ip(request)
    allowed, _, reset = _check_rate(
        f"{VENTES_BUCKET_PREFIX}{cle_visiteur(client_ip)}",
        settings.carte_ventes_rate_limit_per_ip,
    )
    if not allowed:
        logger.warning("carte_ventes_rate_limited", extra={"client_ip": client_ip})
        raise _trop_de_demandes(reset)


def _probleme_constant(request: Request, statut: int, titre: str) -> JSONResponse:
    """Constant problem of a map route: nothing of the input is reflected."""
    gabarit = getattr(request.scope.get("route"), "path", None) or "/v1/carte"
    corps = ProblemDetail(title=titre, status=statut, instance=gabarit).model_dump(
        exclude_none=True
    )
    return JSONResponse(
        status_code=statut,
        content=corps,
        media_type="application/problem+json",
        headers=NO_STORE,
    )


def _carte_indisponible(request: Request) -> JSONResponse:
    """Constant 503: no body of image, nothing of the input reflected."""
    return _probleme_constant(request, 503, "Carte momentanement indisponible")


@carte_router.get(
    "/carte/{ban_id}/{couche}.png",
    dependencies=[Depends(limite_carte)],
    response_class=Response,
    responses={
        200: {
            "description": (
                "Image PNG de 448 x 448 px (300 m au sol autour de l'adresse)."
            ),
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        404: _probleme_doc("Adresse inconnue, voie sans numero ou hors couverture."),
        429: {
            "description": "Trop de demandes (par visiteur, ou appels a la source).",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        503: {
            "description": "Lecture de l'adresse ou source de la carte indisponible.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
    },
    summary="Carte d'une adresse : plan ou parcelles (image)",
    description=(
        "Image PNG de 448 x 448 px centree sur l'adresse BAN, 300 m au sol, "
        "tiree du plan IGN (`plan`) ou du parcellaire cadastral (`parcelles`). "
        "Route publique, sans cle : la source est interrogee par le serveur, "
        "jamais par le visiteur, et rien du visiteur ne lui est transmis. Aucun "
        "autre parametre n'est lu. L'image est gardee 21 jours cote serveur. "
        "Source indisponible : 503 sans image."
    ),
)
async def lire_carte(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("ban", departement=departement_de_la_cle)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    ban_id: Annotated[
        str,
        Path(
            max_length=BAN_ID_MAX_LENGTH,
            pattern=BAN_ID_PATTERN,
            description="Cle BAN servie (ex. 85191_0420_00012)",
        ),
    ],
    couche: Annotated[
        Literal["plan", "parcelles"],
        Path(description="Couche : `plan` (Plan IGN) ou `parcelles` (cadastre)"),
    ],
) -> Response:
    """The PNG of one (key, layer), from the cache or from one IGN call.

    The coverage of the BAN is checked first, hit or not: an address that left
    the scope is no longer served from the cache. The scope is read by the
    dependency, but a hit reads no address row. Known 404s are answered from
    the short negative cache before any read.
    """
    if hosting.hostings["ban"].etat != "heberge":
        return _introuvable(request, "adresse_inconnue")
    image = proxy.CACHE.get((ban_id, couche))
    if image is None:
        code = proxy.NEGATIF.get(ban_id)
        if code is not None:
            return _introuvable(request, code)
        try:
            image = await _obtenir(db, hosting, ban_id, couche)
        except VoieSansNumeroError:
            return _introuvable(request, "voie_sans_numero")
        except AdresseInconnueError:
            return _introuvable(request, "adresse_inconnue")
        except AdresseIndisponibleError:
            return probleme(
                request,
                503,
                "adresse_indisponible",
                "Adresse momentanement indisponible",
            )
        except proxy.CarteAmontIndisponibleError:
            return _carte_indisponible(request)
    return Response(content=image, media_type="image/png", headers=_ENTETES_IMAGE)


def _introuvable(
    request: Request, code: Literal["adresse_inconnue", "voie_sans_numero"]
):
    titre = "Voie sans numero" if code == "voie_sans_numero" else "Adresse inconnue"
    return probleme(request, 404, code, titre)


async def _obtenir(
    db: AsyncSession, hosting: RouteHosting, ban_id: str, couche: str
) -> bytes:
    """The image of a missed key; concurrent requests on one key share one call.

    The first request does the work; the others wait for its outcome, image or
    error, instead of each spending a resolution and an IGN call. The entry is
    dropped when the work ends, whatever the outcome, so nothing outlives it.
    """
    cle = (ban_id, couche)
    en_cours = proxy.EN_COURS.get(cle)
    if en_cours is not None:
        # The scope was read in this session: give the connection back before
        # waiting for another request's IGN call.
        await db.rollback()
        return await asyncio.shield(en_cours)
    futur: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    proxy.EN_COURS[cle] = futur
    try:
        image = await _produire(db, hosting, ban_id, couche)
    except BaseException as exc:
        futur.set_exception(
            exc if isinstance(exc, Exception) else proxy.CarteAmontIndisponibleError()
        )
        futur.exception()  # nobody may be waiting: mark the error as retrieved
        raise
    else:
        futur.set_result(image)
        return image
    finally:
        proxy.EN_COURS.pop(cle, None)


async def _produire(
    db: AsyncSession, hosting: RouteHosting, ban_id: str, couche: str
) -> bytes:
    """Resolve the point, release the transaction, call the IGN, keep the image."""
    _depenser(RESOLUTION_BUCKET, settings.carte_rate_limit_resolution)
    try:
        point = await point_de_la_cle(db, ban_id, hosting)
    except VoieSansNumeroError:
        proxy.NEGATIF[ban_id] = "voie_sans_numero"
        raise
    except AdresseInconnueError:
        proxy.NEGATIF[ban_id] = "adresse_inconnue"
        raise
    if point is None:
        proxy.NEGATIF[ban_id] = "adresse_inconnue"
        raise AdresseInconnueError
    # Read-only so far: give the connection back before waiting for the IGN, as
    # the report does (app/rapport/service.py).
    await db.rollback()
    _depenser(AMONT_BUCKET, settings.carte_rate_limit_amont)
    image = await proxy.telecharger_carte(couche, point.lon, point.lat)
    proxy.CACHE[(ban_id, couche)] = image
    return image


def _depenser(seau: str, limite: int) -> None:
    """One token of a global bucket, or the 429 of the route."""
    allowed, _, reset = _check_rate(seau, limite)
    if not allowed:
        logger.warning("carte_rate_limited", extra={"bucket": seau})
        raise _trop_de_demandes(reset)


def _entetes_donnees(*etats: str) -> dict[str, str]:
    """Cache header of a JSON answer of the map, chosen from its states.

    Stable states (a value, out of coverage) make an answer every visitor may
    share for a while. One ``temporary_error`` and it is never stored: a cached
    outage would outlive the outage (T-04.10-36).
    """
    if "temporary_error" in etats:
        return {**NO_STORE, "X-Content-Type-Options": "nosniff"}
    return {
        "Cache-Control": f"public, max-age={settings.carte_parcelles_cache_secondes}",
        "X-Content-Type-Options": "nosniff",
    }


@carte_router.get(
    "/carte/parcelles/{z}/{x}/{y}",
    dependencies=[Depends(limite_parcelles)],
    response_model=ReponseApi[TuileParcelles],
    responses={
        422: {
            "description": "Zoom hors de 16 a 19, ou indice hors de la grille du zoom.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        429: {
            "description": "Trop de demandes (par visiteur) ; en-tete Retry-After.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        503: {
            "description": "Lecture du cadastre indisponible.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
    },
    summary="Parcelles cadastrales d'une tuile de la carte (GeoJSON)",
    description=(
        "Toutes les parcelles de la base qui touchent la tuile `z/x/y` (grille "
        "Web Mercator, zoom 16 a 19), chacune entiere, sans plafond : surface "
        "cadastrale (nulle si absente), point d'etiquette dans la parcelle et "
        "nombre de ventes DVF distinctes sur la periode servie. Une parcelle a "
        "cheval sur deux tuiles est servie par les deux. Route publique, sans "
        "cle, avec sa propre limite par visiteur. Hors couverture, aucune "
        "parcelle, surface absente et panne sont quatre reponses distinctes ; "
        "une panne des ventes laisse les parcelles servies, sans comptes, et "
        "la reponse n'est alors pas mise en cache. Ni prix, ni date, ni "
        "identifiant de vente."
    ),
)
async def lire_parcelles_tuile(
    request: Request,
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("cadastre", "dvf"))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    z: Annotated[
        int, Path(ge=ZOOM_MIN, le=ZOOM_MAX, description="Zoom de la tuile, 16 a 19")
    ],
    x: Annotated[
        int, Path(ge=0, le=_INDICE_MAX, description="Colonne, inferieure a 2^z")
    ],
    y: Annotated[
        int, Path(ge=0, le=_INDICE_MAX, description="Ligne, inferieure a 2^z")
    ],
) -> JSONResponse:
    """The parcels of one tile; the bucket is spent before, then the reader runs."""
    if max(x, y) >= 2**z:
        return _probleme_constant(request, 422, _TITRE_VALIDATION)
    try:
        tuile = await lire_tuile(db, scope, hosting, z, x, y)
    except ParcellesIndisponiblesError:
        return _probleme_constant(request, 503, "Parcelles momentanement indisponibles")
    meta = MetaReponse.model_validate(get_meta_sources(["cadastre", "dvf"]))
    corps = ReponseApi(donnees=tuile, meta=meta)
    return JSONResponse(
        content=corps.model_dump(mode="json"),
        headers=_entetes_donnees(tuile.parcelles.etat, tuile.ventes.etat),
    )


@carte_router.get(
    "/carte/{ban_id}/contexte",
    dependencies=[Depends(limite_parcelles)],
    response_model=ReponseApi[ContexteCarte],
    responses={
        404: _probleme_doc("Adresse inconnue, voie sans numero ou hors couverture."),
        422: {
            "description": "Cle BAN mal formee.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        429: {
            "description": "Trop de demandes (par visiteur) ; en-tete Retry-After.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        503: _probleme_doc("Lecture de l'adresse indisponible."),
    },
    summary="Contexte de la carte d'une adresse : centre, emprise, parcelle",
    description=(
        "Ce sur quoi s'ouvre la carte interactive d'une adresse : le point BAN "
        "(centre), l'emprise dans laquelle la carte se deplace (un carre "
        "autour du point, qui contient la vue la plus large au zoom 16), la "
        "parcelle cadastrale qui contient le point (jamais la plus proche ; "
        "point hors parcelle et parcelles multiples sont des etats types), "
        "l'etat des parcelles et des ventes pour le departement de l'adresse, "
        "la periode des ventes, le millesime du cadastre et le credit du fond "
        "de carte. Route publique, sans cle, sur la limite par visiteur des "
        "tuiles de parcelles. Jamais mise en cache."
    ),
)
async def lire_contexte_carte(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(
            hosted_datasets("ban", "cadastre", "dvf", departement=departement_de_la_cle)
        ),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    ban_id: Annotated[
        str,
        Path(
            max_length=BAN_ID_MAX_LENGTH,
            pattern=BAN_ID_PATTERN,
            description="Cle BAN servie (ex. 85191_0420_00012)",
        ),
    ],
) -> JSONResponse:
    """The context of one address; the same constant 404s as its static map."""
    try:
        contexte = await lire_contexte(db, hosting, ban_id)
    except VoieSansNumeroError:
        return _introuvable(request, "voie_sans_numero")
    except AdresseInconnueError:
        return _introuvable(request, "adresse_inconnue")
    except AdresseIndisponibleError:
        return probleme(
            request, 503, "adresse_indisponible", "Adresse momentanement indisponible"
        )
    if contexte is None:
        return _introuvable(request, "adresse_inconnue")
    meta = MetaReponse.model_validate(get_meta_sources(["ban", "cadastre", "dvf"]))
    corps = ReponseApi(donnees=contexte, meta=meta)
    return JSONResponse(
        content=corps.model_dump(mode="json"),
        headers={**NO_STORE, "X-Content-Type-Options": "nosniff"},
    )


@carte_router.get(
    "/carte/fond/{z}/{x}/{y}.png",
    dependencies=[Depends(limite_fond)],
    response_class=Response,
    responses={
        200: {
            "description": "Tuile PNG de 256 x 256 px du Plan IGN.",
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        404: {
            "description": "Tuile hors du territoire servi.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        422: {
            "description": "Zoom hors de 16 a 19, ou indice hors de la grille du zoom.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        429: {
            "description": (
                "Trop de demandes (par visiteur, ou appels a la source) ; "
                "en-tete Retry-After."
            ),
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        503: {
            "description": "Lecture du territoire ou source du fond indisponible.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
    },
    summary="Fond de la carte : une tuile du Plan IGN (image)",
    description=(
        "Tuile PNG de 256 x 256 px du Plan IGN, grille Web Mercator `z/x/y`, "
        "zoom 16 a 19 : le fond de la carte interactive. Route publique, sans "
        "cle, avec sa propre limite par visiteur. La tuile est demandee a la "
        "source par le serveur, jamais par le visiteur, et rien du visiteur ne "
        "lui est transmis ; la couche est fixe et aucun autre parametre n'est "
        "lu. Seules les tuiles du territoire heberge sont relayees : ailleurs, "
        "404 sans appel a la source. La tuile est gardee 21 jours cote "
        "serveur. Source indisponible : 503 sans image, jamais une tuile vide."
    ),
)
async def lire_fond(
    request: Request,
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("ban"))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    z: Annotated[
        int, Path(ge=ZOOM_MIN, le=ZOOM_MAX, description="Zoom de la tuile, 16 a 19")
    ],
    x: Annotated[
        int, Path(ge=0, le=_INDICE_MAX, description="Colonne, inferieure a 2^z")
    ],
    y: Annotated[
        int, Path(ge=0, le=_INDICE_MAX, description="Ligne, inferieure a 2^z")
    ],
) -> Response:
    """One tile of the base map, from the cache or from one IGN call.

    The scope is read by the dependency; beyond it a kept tile reads nothing.
    A missed one costs one read of the referential, which decides the
    territory before anything is asked, then the connection is given back
    before the wait. Refusal (404), limit (429) and outage (503) never merge,
    and none of them is an image.
    """
    if max(x, y) >= 2**z:
        return _probleme_constant(request, 422, _TITRE_VALIDATION)
    # Coverage first, kept tile or not: nothing is relayed once the BAN left
    # the scope.
    if not hosting.hosts("ban"):
        return _hors_territoire(request)
    image = tuiles.CACHE.get((z, x, y))
    if image is None:
        try:
            if not await tuiles.dans_le_territoire(db, scope, z, x, y):
                return _hors_territoire(request)
        except tuiles.TerritoireIndisponibleError:
            return _carte_indisponible(request)
        # Read-only so far: give the connection back before waiting for the
        # IGN, or for the request that is already asking it for this tile.
        await db.rollback()
        try:
            image = await tuiles.obtenir_tuile(z, x, y)
        except tuiles.BudgetAmontEpuiseError as exc:
            logger.warning("carte_rate_limited", extra={"bucket": tuiles.AMONT_BUCKET})
            raise _trop_de_demandes(exc.reset) from exc
        except proxy.CarteAmontIndisponibleError:
            return _carte_indisponible(request)
    return Response(content=image, media_type="image/png", headers=_ENTETES_IMAGE)


def _hors_territoire(request: Request) -> JSONResponse:
    """Constant 404: the sea and a land we do not host answer the same."""
    return _probleme_constant(request, 404, "Tuile hors du territoire servi")


@carte_router.get(
    "/carte/{ban_id}/parcelle/{id_parcelle}/ventes",
    dependencies=[Depends(limite_ventes_carte)],
    response_model=ReponseApi[VentesParcelleCarte],
    responses={
        404: _probleme_ou_validation_doc(
            "Parcelle introuvable autour de cette adresse (meme reponse pour une "
            "adresse inconnue, une parcelle inconnue et une parcelle hors de "
            "l'emprise, sans `code`) ; page hors limites (`page_hors_limites`)."
        ),
        409: _probleme_doc("Generation DVF changee : revenir a la page 1."),
        422: {
            "description": "Cle BAN, identifiant de parcelle, page ou jeton invalide.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        429: {
            "description": "Trop de demandes (par visiteur) ; en-tete Retry-After.",
            "content": {"application/problem+json": {"schema": _PROBLEME_GENERIQUE}},
        },
        503: _probleme_doc("Ventes indisponibles (publication DVF non validee)."),
    },
    summary="Ventes DVF d'une parcelle de la carte d'une adresse",
    description=(
        "Mutations DVF distinctes d'une parcelle cadastrale choisie sur la "
        "carte interactive d'une adresse, 5 par page, de la plus recente a la "
        "plus ancienne, au format des ventes de la fiche adresse et avec la "
        "meme regle : le prix d'une vente isolee recente (24 mois avant la "
        "derniere date DVF importee) n'est jamais servi. La route ne repond "
        "que pour une parcelle qui touche l'emprise de deplacement de la carte "
        "de l'adresse nommee, calculee par le serveur ; ailleurs, un seul 404, "
        "qui ne dit pas si l'adresse ou la parcelle existent. Aucune vente "
        "n'est identifiee ni rattachee a un logement. `groupes` suit `ventes` "
        "rang pour rang. Zero vente, hors couverture et panne (503) sont trois "
        "reponses distinctes. Route publique, sans cle, avec sa propre limite "
        "par visiteur, plus serree que celle des tuiles. Le jeton `vg` de la "
        "reponse est repris par les liens de page ; un jeton perime est refuse "
        "(409). Jamais mise en cache."
    ),
)
async def lire_ventes_parcelle_carte(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(
            hosted_datasets("ban", "cadastre", "dvf", departement=departement_de_la_cle)
        ),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    ban_id: Annotated[
        str,
        Path(
            max_length=BAN_ID_MAX_LENGTH,
            pattern=BAN_ID_PATTERN,
            description="Cle BAN servie (ex. 85191_0420_00012)",
        ),
    ],
    id_parcelle: Annotated[
        str,
        Path(
            pattern=r"^[0-9A-Z]{14}$",
            description="Identifiant cadastral de la parcelle, 14 caracteres",
        ),
    ],
    page: Annotated[
        int, Query(ge=1, le=100_000, description="Page (5 ventes par page)")
    ] = 1,
    vg: Annotated[
        str | None,
        Query(
            max_length=16,
            pattern=GENERATION_PATTERN,
            description="Jeton de generation DVF repris des liens de page",
        ),
    ] = None,
) -> JSONResponse:
    """The sales of one parcel around one address; constant problems.

    The bucket is spent before, then the reader decides whether the pair of
    the path is served. Nothing else of the request is read: no bounds, no
    page size, no parcel list comes from the visitor.
    """
    try:
        resultat = await lire_ventes_de_la_parcelle(
            db, scope, hosting, ban_id, id_parcelle, page, vg
        )
    except GenerationChangeeError:
        return probleme(request, 409, "generation_changee", "Ventes DVF mises a jour")
    except PageHorsLimitesError:
        return probleme(request, 404, "page_hors_limites", "Page hors limites")
    except VentesIndisponiblesError:
        return probleme(
            request, 503, "ventes_indisponibles", "Ventes momentanement indisponibles"
        )
    if resultat is None:
        return _probleme_constant(
            request, 404, "Parcelle introuvable autour de cette adresse"
        )
    meta = MetaReponse.model_validate(get_meta_sources(["dvf", "cadastre"]))
    corps = ReponseApi(donnees=resultat, meta=meta)
    return JSONResponse(
        content=corps.model_dump(mode="json"),
        headers={**NO_STORE, "X-Content-Type-Options": "nosniff"},
    )
