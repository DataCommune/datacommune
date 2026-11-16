"""Address search, address page, declared DPE and sales routes (04-02 to 04-05).

Mounted on the protected router with the existing authentication, without
any route-wide publication guard: the BAN and the cadastre are not journaled
sources (R02-05/1, N-10), and every journaled source of the address page is
guarded in its own block (``app.adresse.blocs``, N-01).
"""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.comparaison import CLES_FICHE, comparer_adresses
from app.adresse.documents import (
    DocumentsIndisponiblesError,
    GenerationChangeeError,
    PageHorsLimitesError,
    lire_documents_adresse,
)
from app.adresse.recherche import servir_recherche
from app.adresse.schemas import (
    CodeProblemeAdresse,
    ComparaisonAdresses,
    DocumentsDpeAdresse,
    FicheAdresse,
    ProblemeAdresse,
    RechercheAdresses,
    TypeComparable,
    VentesAdresse,
    VentesParcelle,
    VentesRessemblantes,
)
from app.adresse.service import (
    AdresseInconnueError,
    AdresseIndisponibleError,
    VoieSansNumeroError,
    forme_de_voie,
    lire_fiche_adresse,
)
from app.adresse.ventes import (
    CriteresComparables,
    RegleVentesAdresse,
    VefaVentesAdresse,
    VentesIndisponiblesError,
    lire_ventes_adresse,
    lire_ventes_parcelle,
    lire_ventes_ressemblantes,
)
from app.ban.queries import GeocodageIndisponibleError
from app.common.exceptions import ErreurApiError
from app.common.schemas import MetaReponse, ProblemDetail, ReponseApi
from app.common.sources import get_meta_source, get_meta_sources
from app.database import get_db
from app.dpe.document_public import NUMERO_DPE_RE
from app.dpe.references import paris_today
from app.scope.dependencies import (
    RouteHosting,
    commune_departement,
    get_instance_scope,
    hosted_datasets,
    real_departement,
)
from app.scope.model import InstanceScope

adresse_router = APIRouter(prefix="/v1", tags=["Adresse"])

# Served BAN key, block E of 04-MESURE-DPE.md: INSEE code (2A/2B included),
# street segment of four to six characters, then a five-digit number and an
# optional suffix of one to ten characters. The street form (no number) is
# accepted so that it gets its own 404. A suffix may carry a non-alphanumeric
# character (observed); whitespace, path separators and control characters
# are refused, and the INSEE prefix rules out a coordinate pair. Plain syntax,
# valid for Python re and for the Rust engine of pydantic-core.
BAN_ID_PATTERN = (
    r"^[0-9][0-9AB][0-9]{3}_[0-9A-Za-z]{4,6}"
    r"(_[0-9]{5}(_[^\s/\\\x00-\x1F\x7F]{1,10})?)?$"
)
BAN_ID_MAX_LENGTH = 40
# Opaque generation token of a DPE snapshot (``jeton_generation``).
GENERATION_PATTERN = r"^[0-9a-f]{16}$"

_PROBLEME_SCHEMA = ProblemeAdresse.model_json_schema()


def _probleme_doc(description: str) -> dict:
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": _PROBLEME_SCHEMA}},
    }


def _probleme_ou_validation_doc(description: str) -> dict:
    """A typed problem, or the validation problem of FastAPI (no ``code``)."""
    schemas = [_PROBLEME_SCHEMA, ProblemDetail.model_json_schema()]
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": {"anyOf": schemas}}},
    }


# A problem or a page of documents is never stored by a cache: a stored 503
# would outlive the outage, a stored page would point to a dead generation
# (API WR-06, as ``/v1/dpe/{numero}`` and the V3 documentary contract).
NO_STORE = {"Cache-Control": "no-store"}


def _gabarit(request: Request) -> str:
    """Template of the matched route: the key never comes back (API IN-01)."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "/v1/adresse"


def probleme(
    request: Request, status: int, code: CodeProblemeAdresse, title: str
) -> JSONResponse:
    """Constant RFC 9457 problem: never reflects the input (T-04-02-03)."""
    body = ProblemeAdresse(
        title=title, status=status, code=code, instance=_gabarit(request)
    ).model_dump(exclude_none=True)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=NO_STORE,
    )


def departement_du_filtre(request: Request) -> str | None:
    """Territory named by the ``departement`` filter of a search, if real."""
    return real_departement(request.query_params.get("departement"))


def departement_de_la_cle(request: Request) -> str | None:
    """Departement of the INSEE prefix of the ``ban_id`` path parameter (N-07)."""
    ban_id = str(request.path_params.get("ban_id", ""))
    return commune_departement(ban_id.split("_", 1)[0])


@adresse_router.get(
    "/adresses/recherche",
    response_model=ReponseApi[RechercheAdresses],
    responses={
        422: _probleme_ou_validation_doc(
            "Departement inconnu (`departement_invalide`) ; parametre hors bornes "
            "(q, departement, limite) : probleme de validation, sans `code`."
        ),
        503: _probleme_doc("Recherche d'adresses indisponible (panne SQL)."),
    },
    summary="Rechercher une adresse dans la BAN de l'instance",
    description=(
        "Adresses de la Base Adresse Nationale hebergee par l'instance, sans "
        "coordonnee : chaque resultat porte son type et la cle BAN servie. "
        "L'ordre des types (adresses ou communes d'abord) est servi par l'API. "
        "Pour une saisie numerotee effectivement recherchee, "
        "correspondance_numero compare seulement le numero et son suffixe dans "
        "les resultats retournes ; elle n'etablit ni l'adresse complete ni "
        "l'absence d'une adresse dans la BAN. etat=trop_large : la recherche "
        "approchee sur toute la base a depasse son budget, sans resultat et "
        "sans panne (200) ; une panne reste un 503."
    ),
)
async def rechercher(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("ban", departement=departement_du_filtre)),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str,
        Query(min_length=2, max_length=200, description="Saisie libre"),
    ],
    departement: Annotated[
        str | None,
        Query(
            max_length=3,
            description="Code departement (01..95, 2A, 2B, 971..976) ; 422 sinon",
        ),
    ] = None,
    limite: Annotated[
        int, Query(ge=1, le=8, description="Nombre maximal de resultats")
    ] = 8,
) -> ReponseApi[RechercheAdresses] | JSONResponse:
    """Typed results of the instance BAN, never a remote service.

    An availability failure of the database (strategies, trigram fallback,
    city detection, referential) is a typed 503, never an empty list
    (SEM-01); any other error is the normal 500 (C55 fix 4, C58).
    """
    departement_filtre = None
    if departement is not None:
        departement_filtre = real_departement(departement)
        if departement_filtre is None:
            raise ErreurApiError(
                422,
                "Departement inconnu",
                headers=NO_STORE,
                type_code="departement_invalide",
            )
    try:
        donnees = await servir_recherche(
            db,
            q,
            departement=departement_filtre,
            limite=limite,
            hosting=hosting,
            scope=scope,
        )
    except GeocodageIndisponibleError:
        return probleme(
            request,
            503,
            "geocodage_indisponible",
            "Recherche d'adresses momentanement indisponible",
        )
    # Never stored by a cache, as the other routes of this module (API IN-03).
    corps = ReponseApi(donnees=donnees, meta=get_meta_source("ban"))
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)


# 2 to 4 distinct keys per comparison (D-49); a key is at most 40 characters.
MIN_COMPARES = 2
MAX_COMPARES = 4
_IDS_MAX_LENGTH = MAX_COMPARES * (BAN_ID_MAX_LENGTH + 1)
_RE_BAN_ID = re.compile(BAN_ID_PATTERN)


def ids_compares(
    ids: Annotated[
        str | None,
        Query(
            description=(
                "2 a 4 cles BAN separees par des virgules ; la premiere adresse "
                "est la reference des ecarts. Un doublon est ecarte."
            ),
            examples=["85191_0420_00012,85191_0601_00001"],
        ),
    ] = None,
) -> list[str]:
    """The requested keys, validated before any read (T-04-07-01).

    Declared first by the route, so that nothing (scope, BAN) is read for a
    malformed request. Every check lives here, none in ``Query``. The problem
    is constant: it never quotes a key, and is never stored by a cache.
    """
    probleme = ErreurApiError(
        422,
        "Identifiants d'adresses invalides",
        "De 2 a 4 cles BAN distinctes, separees par des virgules, sont attendues.",
        headers=NO_STORE,
        type_code="ids_invalides",
    )
    if not ids or len(ids) > _IDS_MAX_LENGTH:
        raise probleme
    parts = list(dict.fromkeys(part.strip() for part in ids.split(",")))
    if not MIN_COMPARES <= len(parts) <= MAX_COMPARES:
        raise probleme
    if any(
        len(part) > BAN_ID_MAX_LENGTH or not _RE_BAN_ID.fullmatch(part)
        for part in parts
    ):
        raise probleme
    return parts


# One designation per compared address at most (04-11): « <ban_id>:<numero> ».
_DESIGNES_MAX_LENGTH = MAX_COMPARES * (BAN_ID_MAX_LENGTH + 1 + 13 + 1)


def designations_demandees(
    ids: Annotated[list[str], Depends(ids_compares)],
    designes: Annotated[
        str | None,
        Query(
            description=(
                "DPE designes par le visiteur, au plus un par adresse comparee : "
                "paires <cle BAN>:<numero ADEME> separees par des virgules. La "
                "designation vit dans le lien seulement ; une paire n'est gardee "
                "que si le numero est declare a cette adresse."
            ),
            examples=["85191_0420_00012:2485E0000003C"],
        ),
    ] = None,
) -> list[tuple[str, str]]:
    """The designated pairs, validated before any read (T-04-11-01).

    Each pair names a compared key and a number of the ADEME form; one pair
    per address at most (an exact duplicate is dropped). The problem is
    constant: it never quotes a key or a number, and is never stored.
    """
    if not designes:
        return []
    probleme = ErreurApiError(
        422,
        "Designations invalides",
        "Au plus une paire <cle BAN>:<numero DPE> par adresse comparee est attendue.",
        headers=NO_STORE,
        type_code="designations_invalides",
    )
    if len(designes) > _DESIGNES_MAX_LENGTH:
        raise probleme
    paires: list[tuple[str, str]] = []
    for part in dict.fromkeys(part.strip() for part in designes.split(",")):
        ban_id, separateur, numero = part.rpartition(":")
        if not separateur or ban_id not in ids or not NUMERO_DPE_RE.fullmatch(numero):
            raise probleme
        if any(ban_id == autre for autre, _ in paires):
            raise probleme
        paires.append((ban_id, numero))
    return paires


@adresse_router.get(
    "/adresses/comparaison",
    response_model=ReponseApi[ComparaisonAdresses],
    responses={
        422: _probleme_doc(
            "Moins de 2 ou plus de 4 cles, cle mal formee (`ids_invalides`), "
            "ou designation invalide : paire mal formee, cle non comparee, "
            "deux paires pour une adresse (`designations_invalides`)."
        ),
        503: _probleme_doc("Identite des adresses indisponible (panne SQL)."),
    },
    summary="Comparaison de 2 a 4 adresses",
    description=(
        "Rend, pour 2 a 4 adresses de la BAN de l'instance, une colonne par "
        "adresse et quatre groupes de lignes : documents a l'adresse, ventes "
        "de la parcelle du point, lieu, puis la commune de l'adresse en "
        "reference, toujours en dernier. Chaque valeur est celle de la page "
        "de l'adresse ; pour les colonnes 2 a 4, l'API sert l'ecart a la "
        "premiere colonne, le motif pour lequel il n'est pas calcule, ou pour "
        "une valeur non numerique le verdict identique / different. Une cle "
        "inconnue est rendue dans ids_ecartes. Un DPE designe (parametre "
        "designes) n'est garde que s'il est declare a cette adresse : ses "
        "lignes suivent celles des documents, avec les ecarts et motifs de la "
        "comparaison directe des documents ; sinon la paire est rendue dans "
        "designations_ecartees. Aucun document n'est rattache a un logement."
    ),
)
async def comparer(
    request: Request,
    ids: Annotated[list[str], Depends(ids_compares)],
    designes: Annotated[list[tuple[str, str]], Depends(designations_demandees)],
    hosting: Annotated[RouteHosting, Depends(hosted_datasets(*CLES_FICHE))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReponseApi[ComparaisonAdresses] | JSONResponse:
    """Compare addresses; the rule is ``app.commune.comparison`` (03-09).

    ``hosting`` declares the keys the columns serve; each column computes its
    own hosting for the departement of its key (N-07), never the instance
    hosting of the request.
    """
    del hosting
    try:
        comparaison = await comparer_adresses(db, ids, scope, paris_today(), designes)
    except AdresseIndisponibleError:
        return probleme(
            request,
            503,
            "comparaison_indisponible",
            "Comparaison d'adresses momentanement indisponible",
        )
    corps = ReponseApi(
        donnees=comparaison, meta=get_meta_sources(["ban", "cadastre", "dpe", "dvf"])
    )
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)


@adresse_router.get(
    "/adresse/{ban_id}",
    response_model=ReponseApi[FicheAdresse],
    responses={
        404: _probleme_doc("Adresse inconnue ou voie sans numero."),
        503: _probleme_doc("Lecture de l'adresse indisponible (panne SQL)."),
    },
    summary="Identite d'une adresse BAN et reperes du lieu",
    description=(
        "Identite d'une adresse par sa cle BAN : libelle, commune, departement, "
        "couverture de l'instance et parcelle qui contient le point BAN, puis "
        "les reperes du lieu, chacun avec son etat, sa source et son "
        "rattachement. Une source en panne ne rend que son repere en erreur. "
        "Aucune coordonnee."
    ),
)
async def lire_adresse(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets(*CLES_FICHE, departement=departement_de_la_cle)),
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
) -> ReponseApi[FicheAdresse] | JSONResponse:
    """Identity of one BAN key; the BAN point stays internal.

    Hosting is computed for the departement of the key (N-07): out of
    coverage is a 200 with ``couverture.etat=hors_couverture``, never a 404.
    """
    try:
        fiche = await lire_fiche_adresse(db, ban_id, hosting, scope, paris_today())
    except VoieSansNumeroError:
        return probleme(request, 404, "voie_sans_numero", "Voie sans numero")
    except AdresseInconnueError:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    except AdresseIndisponibleError:
        return probleme(
            request,
            503,
            "adresse_indisponible",
            "Adresse momentanement indisponible",
        )
    # A page may carry blocks in outage: never stored by a cache (API IN-03).
    corps = ReponseApi(donnees=fiche, meta=get_meta_sources(["ban", "cadastre"]))
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)


@adresse_router.get(
    "/adresse/{ban_id}/dpe",
    response_model=ReponseApi[DocumentsDpeAdresse],
    responses={
        404: _probleme_doc("Adresse inconnue, voie sans numero ou page hors limites."),
        409: _probleme_doc("Generation des documents changee : revenir a la page 1."),
        503: _probleme_doc("Documents DPE indisponibles (publication non validee)."),
    },
    summary="Documents DPE declares a une adresse",
    description=(
        "Liste chronologique complete des documents DPE dont l'adresse saisie "
        "par le diagnostiqueur correspond a la cle BAN, 10 par page, avec le "
        "total, la repartition des classes et le plus recent (unique, egalite "
        "ou indetermine) calcules sur toute la liste. Aucun document n'est "
        "rattache a un logement. Le jeton `g` de la reponse est repris par les "
        "liens de page ; un jeton perime est refuse (409). A part, hors du "
        "total, de la repartition et du plus recent : les documents dont la "
        "cle ADEME est absente de la BAN et dont l'adresse saisie designe "
        "exactement cette adresse (`documents_adresse_brute`, les 10 plus "
        "recents, identiques sur chaque page, et `total_adresse_brute`) ; "
        "`etat_adresse_brute` vaut `not_available` quand la generation servie "
        "n'a pas ce rapprochement (aucun zero)."
    ),
)
async def lire_documents_dpe(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(
            hosted_datasets(
                "ban",
                "dpe",
                "cadastre",
                "copropriete",
                "batiments",
                departement=departement_de_la_cle,
            )
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
    page: Annotated[
        int, Query(ge=1, le=100_000, description="Page (10 documents par page)")
    ] = 1,
    g: Annotated[
        str | None,
        Query(
            max_length=16,
            pattern=GENERATION_PATTERN,
            description="Jeton de generation repris des liens de page",
        ),
    ] = None,
) -> ReponseApi[DocumentsDpeAdresse] | JSONResponse:
    """Declared documents of one BAN key under one guarded DPE snapshot.

    The problems are constant: neither the key, the page nor the token is
    reflected (T-04-03-01).
    """
    if forme_de_voie(ban_id):
        return probleme(request, 404, "voie_sans_numero", "Voie sans numero")
    if departement_de_la_cle(request) is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    try:
        documents = await lire_documents_adresse(
            db, ban_id, page, g, hosting, paris_today()
        )
    except GenerationChangeeError:
        return probleme(request, 409, "generation_changee", "Documents DPE mis a jour")
    except PageHorsLimitesError:
        return probleme(request, 404, "page_hors_limites", "Page hors limites")
    except DocumentsIndisponiblesError:
        return probleme(
            request,
            503,
            "documents_dpe_indisponibles",
            "Documents DPE momentanement indisponibles",
        )
    if documents is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    corps = ReponseApi(donnees=documents, meta=get_meta_sources(["ban", "dpe"]))
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)


_CLE_BAN = Path(
    max_length=BAN_ID_MAX_LENGTH,
    pattern=BAN_ID_PATTERN,
    description="Cle BAN servie (ex. 85191_0420_00012)",
)
_GENERATION_DVF = Query(
    max_length=16,
    pattern=GENERATION_PATTERN,
    description="Jeton de generation DVF repris des liens de page",
)


@adresse_router.get(
    "/adresse/{ban_id}/ventes",
    response_model=ReponseApi[VentesParcelle],
    responses={
        404: _probleme_doc("Adresse inconnue, voie sans numero ou page hors limites."),
        409: _probleme_doc("Generation DVF changee : revenir a la page 1."),
        503: _probleme_doc("Ventes indisponibles (publication DVF non validee)."),
    },
    summary="Ventes DVF de la parcelle qui contient le point de l'adresse",
    description=(
        "Mutations DVF distinctes de la parcelle cadastrale qui contient le "
        "point BAN de l'adresse, 10 par page, de la plus recente a la plus "
        "ancienne. Aucune vente n'est rattachee a un logement. Le prix d'une "
        "vente isolee recente (24 mois avant la derniere date DVF importee) "
        "n'est jamais servi. Point hors parcelle et parcelles multiples sont "
        "des etats types, jamais une liste vide. Le jeton `vg` de la reponse "
        "est repris par les liens de page ; un jeton perime est refuse (409)."
    ),
)
async def lire_ventes(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(
            hosted_datasets("dvf", "cadastre", "ban", departement=departement_de_la_cle)
        ),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    ban_id: Annotated[str, _CLE_BAN],
    page: Annotated[
        int, Query(ge=1, le=100_000, description="Page (10 ventes par page)")
    ] = 1,
    vg: Annotated[str | None, _GENERATION_DVF] = None,
) -> ReponseApi[VentesParcelle] | JSONResponse:
    """Sales of the parcel of the point; constant problems (T-04-03-01)."""
    if forme_de_voie(ban_id):
        return probleme(request, 404, "voie_sans_numero", "Voie sans numero")
    if departement_de_la_cle(request) is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    try:
        resultat = await lire_ventes_parcelle(db, ban_id, page, vg, hosting)
    except GenerationChangeeError:
        return probleme(request, 409, "generation_changee", "Ventes DVF mises a jour")
    except PageHorsLimitesError:
        return probleme(request, 404, "page_hors_limites", "Page hors limites")
    except VentesIndisponiblesError:
        return probleme(
            request, 503, "ventes_indisponibles", "Ventes momentanement indisponibles"
        )
    if resultat is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    corps = ReponseApi(donnees=resultat, meta=get_meta_sources(["dvf", "cadastre"]))
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)


@adresse_router.get(
    "/adresse/{ban_id}/ventes-declarees",
    response_model=ReponseApi[VentesAdresse],
    responses={
        404: _probleme_doc("Adresse inconnue, voie sans numero ou page hors limites."),
        409: _probleme_doc("Generation DVF changee : revenir a la page 1."),
        503: _probleme_doc("Ventes indisponibles (publication DVF non validee)."),
    },
    summary="Ventes DVF declarees a l'adresse",
    description=(
        "Ventes DVF dont l'adresse declaree (numero, voie, commune) est celle "
        "de la cle BAN, 10 par page, de la plus recente a la plus ancienne. "
        "Relation distincte des ventes de la parcelle, jamais fusionnee avec "
        "elles. Les donnees DVF importees ne gardent pas le suffixe : une "
        "adresse bis ou ter ne montre pas les ventes declarees a son numero "
        "(etat type), et une vente declaree au 2 bis peut figurer sur "
        "l'adresse 2. Un total de 0 dit qu'aucune vente n'est rattachee a "
        "l'adresse (adresse de la premiere ligne de chaque vente seulement), "
        "pas qu'aucune vente n'a eu lieu. Aucune vente n'est "
        "rattachee a un logement ; le prix d'une vente isolee recente n'est "
        "jamais servi. Le jeton `vg` est celui des ventes de la parcelle ; un "
        "jeton perime est refuse (409). Depuis le plan 04.1-14, l'import DVF "
        "garde le suffixe (adresse_suffixe) ; la regle par defaut "
        "(`regle=numero`, C37) ne le lit pas. `regle=numero_et_suffixe` (C44) "
        "rattache par numero et suffixe : l'adresse 2 garde les ventes "
        "declarees sans suffixe, l'adresse 2 bis celles declarees 2 B (table "
        "BAN rep vers DVF mesuree) ; un suffixe sans correspondance unique est "
        "un etat type (`suffixe_sans_correspondance`), jamais rattache au "
        "numero nu, et une generation DVF importee sans le suffixe est une "
        "absence typee (`suffixe_non_importe`). VEFA (plan 04.1-15) : par "
        "defaut (`vefa=exclues`), les ventes seulement ; `vefa=incluses` liste "
        "aussi les ventes en l'etat futur d'achevement declarees a l'adresse, "
        "chaque carte marquee `hors_stats_vefa`, `nombre_vefa` disant combien "
        "du total elles sont ; elles n'entrent dans aucune statistique (le "
        "nombre de ventes du comparateur ne les compte jamais)."
    ),
)
async def lire_ventes_declarees(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("dvf", "ban", departement=departement_de_la_cle)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    ban_id: Annotated[str, _CLE_BAN],
    page: Annotated[
        int, Query(ge=1, le=100_000, description="Page (10 ventes par page)")
    ] = 1,
    vg: Annotated[str | None, _GENERATION_DVF] = None,
    regle: Annotated[
        RegleVentesAdresse,
        Query(
            description=(
                "Regle de rattachement : numero (defaut, C37) ou "
                "numero_et_suffixe (C44, plan 04.1-14)"
            )
        ),
    ] = "numero",
    vefa: Annotated[
        VefaVentesAdresse,
        Query(
            description=(
                "VEFA : exclues (defaut, ventes seulement) ou incluses (listees, "
                "marquees hors_stats_vefa, hors statistiques ; plan 04.1-15)"
            )
        ),
    ] = "exclues",
) -> ReponseApi[VentesAdresse] | JSONResponse:
    """Sales declared at the address (C37, C44); constant problems (T-04-03-01)."""
    if forme_de_voie(ban_id):
        return probleme(request, 404, "voie_sans_numero", "Voie sans numero")
    if departement_de_la_cle(request) is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    try:
        resultat = await lire_ventes_adresse(db, ban_id, page, vg, hosting, regle, vefa)
    except GenerationChangeeError:
        return probleme(request, 409, "generation_changee", "Ventes DVF mises a jour")
    except PageHorsLimitesError:
        return probleme(request, 404, "page_hors_limites", "Page hors limites")
    except VentesIndisponiblesError:
        return probleme(
            request, 503, "ventes_indisponibles", "Ventes momentanement indisponibles"
        )
    if resultat is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    meta = MetaReponse.model_validate(get_meta_sources(["dvf", "ban"]))
    corps = ReponseApi(donnees=resultat, meta=meta)
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)


@adresse_router.get(
    "/adresse/{ban_id}/ventes-ressemblantes",
    response_model=ReponseApi[VentesRessemblantes],
    responses={
        404: _probleme_doc("Adresse inconnue, voie sans numero ou page hors limites."),
        409: _probleme_doc("Generation DVF changee : revenir a la page 1."),
        503: _probleme_doc("Ventes indisponibles (publication DVF non validee)."),
    },
    summary="Ventes ressemblantes autour du point de l'adresse ou dans sa commune",
    description=(
        "Ventes DVF d'un seul local du type choisi (maison ou appartement), "
        "dans une tolerance de surface, autour du point BAN de l'adresse "
        "(250 m, 500 m, 1 km) ou dans sa commune, sur les dernieres annees "
        "comptees depuis la derniere date DVF importee. Filtre, tri (distance "
        "a vol d'oiseau, puis date decroissante) et effectif sont calcules par "
        "l'API ; les criteres appliques sont servis. Enumerations fermees : "
        "toute autre valeur est refusee (422). Aucune vente n'est rattachee a "
        "un logement ; le prix d'une vente isolee recente n'est jamais servi."
    ),
)
async def lire_ventes_ressemblantes_route(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("dvf", "ban", departement=departement_de_la_cle)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    ban_id: Annotated[str, _CLE_BAN],
    type_: Annotated[
        TypeComparable, Query(alias="type", description="Type du local vendu")
    ],
    surface: Annotated[
        int | None,
        Query(ge=9, le=1000, description="Surface batie de reference (m2)"),
    ] = None,
    tolerance: Annotated[
        Literal["10", "20", "30"], Query(description="Tolerance de surface (%)")
    ] = "20",
    rayon: Annotated[
        Literal["250", "500", "1000", "commune"],
        Query(description="Rayon autour du point (m) ou toute la commune"),
    ] = "500",
    periode: Annotated[
        Literal["1", "3", "5"], Query(description="Dernieres annees DVF")
    ] = "5",
    page: Annotated[
        int, Query(ge=1, le=100_000, description="Page (10 ventes par page)")
    ] = 1,
    vg: Annotated[str | None, _GENERATION_DVF] = None,
) -> ReponseApi[VentesRessemblantes] | JSONResponse:
    """Similar sales of D-114; constant problems (T-04-03-01)."""
    if forme_de_voie(ban_id):
        return probleme(request, 404, "voie_sans_numero", "Voie sans numero")
    if departement_de_la_cle(request) is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    criteres = CriteresComparables(
        type=type_,
        surface=surface,
        tolerance=int(tolerance),
        rayon=None if rayon == "commune" else int(rayon),
        periode=int(periode),
        page=page,
    )
    try:
        resultat = await lire_ventes_ressemblantes(db, ban_id, criteres, vg, hosting)
    except GenerationChangeeError:
        return probleme(request, 409, "generation_changee", "Ventes DVF mises a jour")
    except PageHorsLimitesError:
        return probleme(request, 404, "page_hors_limites", "Page hors limites")
    except VentesIndisponiblesError:
        return probleme(
            request, 503, "ventes_indisponibles", "Ventes momentanement indisponibles"
        )
    if resultat is None:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    corps = ReponseApi(donnees=resultat, meta=get_meta_sources(["dvf", "ban"]))
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)
