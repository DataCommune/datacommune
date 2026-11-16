"""FastAPI router for zone-based endpoints (v2 frontend).

Provides commune autocomplete, departement listing, paginated zone
resolve (DVF/DPE), and zone-level aggregated stats.
"""

import logging
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ErreurApiError
from app.common.publication_readiness import (
    acquire_publication_read_lock,
    require_publication,
)
from app.common.schemas import (
    DatasetHosting,
    MetaReponse,
    OutOfCoverageReason,
    ProblemDetail,
    ReponseApi,
)
from app.common.sources import get_meta_source, get_meta_sources
from app.commune.absences import EtatColonne
from app.commune.queries import (
    commune_synthese_par_codes,
    departement_synthese,
    fenetre_mediane_commune,
)
from app.commune.schemas import (
    COMMUNE_SOURCE_KEYS,
    apply_commune_coverage,
    commune_coverage,
    hosted_commune_sources,
)
from app.database import get_db
from app.scope.coverage import hosting_for, hosting_for_key
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    commune_departement,
    get_instance_scope,
    hosted_datasets,
    real_departement,
)
from app.scope.model import InstanceScope
from app.zone.departements import (
    PAGE_SOURCES,
    block_state,
    dpe_block,
    fiches_day,
    imported_for,
    indexable,
    page_day,
    population_block,
    prix_block,
)
from app.zone.queries import (
    POSTAL_CODE_RE,
    communes_principales,
    communes_voisines,
    departement_communes,
    departement_fiches,
    departement_identite,
    import_facts,
    list_communes,
    list_departements,
    postal_code_departement,
    search_communes,
    zone_existe,
    zone_resolve_dpe,
    zone_resolve_dvf,
    zone_stats_dpe,
    zone_stats_dvf,
)
from app.zone.schemas import (
    CommuneAnnuaireRow,
    CommuneListRow,
    CommunePrincipale,
    CommunesAnnuaireResponse,
    CommunesDepartement,
    CommunesListResponse,
    CommunesVoisines,
    CommuneVoisine,
    DpeDistribution,
    FicheDepartement,
    FichesDepartement,
    PrixAnnuaire,
    PrixCommuneAnnuaire,
    PrixTypeAnnuaire,
    ResultatCommuneSearch,
    ResultatDepartement,
    ResultatDepartementSynthese,
    ZoneResolveResponse,
    ZoneStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Zone"])


def zone_departement(request: Request) -> str | None:
    """Departement named by ``type``/``code``; None when no real one is named.

    A code naming no departement is never a proof of « hors couverture »
    (02-07 rule); an invalid ``type`` keeps its 422 from the handler.
    """
    zone_type = request.query_params.get("type")
    code = request.query_params.get("code")
    if zone_type == "departement":
        return real_departement(code)
    if zone_type == "commune":
        return commune_departement(code)
    return None


def search_departement(request: Request) -> str | None:
    """Territory of a commune search, when it names a real departement.

    The ``departement`` filter when given (an unknown one: None, the handler
    answers 422); otherwise the departement named by a five-digit ``q``
    (postal code, CR-01). A search by name has no territory.
    """
    params = request.query_params
    if "departement" in params:
        return real_departement(params.get("departement"))
    return real_departement(postal_code_departement(params.get("q", "")))


def served_departements(key: str, scope: InstanceScope) -> list[str]:
    """Departements where *key* is hosted and published by its source.

    Empty when the dataset is not hosted: its table is then never read.
    """
    hosting = hosting_for(key, scope, None)
    return hosting.departements if hosting.etat == "heberge" else []


# ---------------------------------------------------------------------------
# Departement -> Region mapping (static, 18 regions, official accented names)
# ---------------------------------------------------------------------------
DEPT_REGION: dict[str, str] = {
    "01": "Auvergne-Rhône-Alpes",
    "03": "Auvergne-Rhône-Alpes",
    "07": "Auvergne-Rhône-Alpes",
    "15": "Auvergne-Rhône-Alpes",
    "26": "Auvergne-Rhône-Alpes",
    "38": "Auvergne-Rhône-Alpes",
    "42": "Auvergne-Rhône-Alpes",
    "43": "Auvergne-Rhône-Alpes",
    "63": "Auvergne-Rhône-Alpes",
    "69": "Auvergne-Rhône-Alpes",
    "73": "Auvergne-Rhône-Alpes",
    "74": "Auvergne-Rhône-Alpes",
    "21": "Bourgogne-Franche-Comté",
    "25": "Bourgogne-Franche-Comté",
    "39": "Bourgogne-Franche-Comté",
    "58": "Bourgogne-Franche-Comté",
    "70": "Bourgogne-Franche-Comté",
    "71": "Bourgogne-Franche-Comté",
    "89": "Bourgogne-Franche-Comté",
    "90": "Bourgogne-Franche-Comté",
    "22": "Bretagne",
    "29": "Bretagne",
    "35": "Bretagne",
    "56": "Bretagne",
    "18": "Centre-Val de Loire",
    "28": "Centre-Val de Loire",
    "36": "Centre-Val de Loire",
    "37": "Centre-Val de Loire",
    "41": "Centre-Val de Loire",
    "45": "Centre-Val de Loire",
    "2A": "Corse",
    "2B": "Corse",
    "08": "Grand Est",
    "10": "Grand Est",
    "51": "Grand Est",
    "52": "Grand Est",
    "54": "Grand Est",
    "55": "Grand Est",
    "57": "Grand Est",
    "67": "Grand Est",
    "68": "Grand Est",
    "88": "Grand Est",
    "59": "Hauts-de-France",
    "60": "Hauts-de-France",
    "62": "Hauts-de-France",
    "80": "Hauts-de-France",
    "02": "Hauts-de-France",
    "75": "Île-de-France",
    "77": "Île-de-France",
    "78": "Île-de-France",
    "91": "Île-de-France",
    "92": "Île-de-France",
    "93": "Île-de-France",
    "94": "Île-de-France",
    "95": "Île-de-France",
    "11": "Occitanie",
    "09": "Occitanie",
    "12": "Occitanie",
    "30": "Occitanie",
    "31": "Occitanie",
    "32": "Occitanie",
    "34": "Occitanie",
    "46": "Occitanie",
    "48": "Occitanie",
    "65": "Occitanie",
    "66": "Occitanie",
    "81": "Occitanie",
    "82": "Occitanie",
    "44": "Pays de la Loire",
    "49": "Pays de la Loire",
    "53": "Pays de la Loire",
    "72": "Pays de la Loire",
    "85": "Pays de la Loire",
    "16": "Nouvelle-Aquitaine",
    "17": "Nouvelle-Aquitaine",
    "19": "Nouvelle-Aquitaine",
    "23": "Nouvelle-Aquitaine",
    "24": "Nouvelle-Aquitaine",
    "33": "Nouvelle-Aquitaine",
    "40": "Nouvelle-Aquitaine",
    "47": "Nouvelle-Aquitaine",
    "64": "Nouvelle-Aquitaine",
    "79": "Nouvelle-Aquitaine",
    "86": "Nouvelle-Aquitaine",
    "87": "Nouvelle-Aquitaine",
    "04": "Provence-Alpes-Côte d'Azur",
    "05": "Provence-Alpes-Côte d'Azur",
    "06": "Provence-Alpes-Côte d'Azur",
    "13": "Provence-Alpes-Côte d'Azur",
    "83": "Provence-Alpes-Côte d'Azur",
    "84": "Provence-Alpes-Côte d'Azur",
    "14": "Normandie",
    "27": "Normandie",
    "50": "Normandie",
    "61": "Normandie",
    "76": "Normandie",
    # DOM-TOM
    "971": "Guadeloupe",
    "972": "Martinique",
    "973": "Guyane",
    "974": "La Réunion",
    "976": "Mayotte",
}


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date_params(*dates: str | None) -> None:
    """Validate that date strings match YYYY-MM-DD format."""
    for d in dates:
        if d is not None and not _DATE_RE.match(d):
            raise ErreurApiError(
                422,
                "Format de date invalide",
                f"Attendu YYYY-MM-DD, recu '{d}'",
            )


@router.get(
    "/communes/search",
    response_model=ReponseApi[list[ResultatCommuneSearch]],
    summary="Recherche de communes par nom ou code postal",
    description=(
        "Recherche de communes par prefixe du nom (insensible aux accents, a "
        "la casse, aux tirets et aux espaces) ou par code postal exact (5 "
        "chiffres, dans les departements ou la BAN est hebergee). Filtre "
        "optionnel par departement. Chaque resultat porte code INSEE, nom, "
        "code postal, departement, population avec son annee et sa source, et "
        "l'hebergement de sa fiche (motif si hors couverture) : une commune "
        "hors couverture reste listee. Tri par population servie "
        "decroissante, puis nom, puis code INSEE. Chaque resultat dit son "
        "type_zone (commune) ; inclure_arrondissements=true ajoute les "
        "arrondissements municipaux de Paris, Lyon et Marseille du perimetre "
        "heberge, avec type_zone = arrondissement et commune_parent."
    ),
)
async def communes_search(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("ban", "demographie", departement=search_departement)),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str,
        Query(
            min_length=2,
            max_length=100,
            description=(
                "Prefixe du nom de commune (min 2 caracteres) ou code postal "
                "(5 chiffres)"
            ),
            example="Nant",
        ),
    ],
    departement: Annotated[
        str | None,
        Query(
            max_length=3,
            description="Code departement (01..95, 2A, 2B, 971..976) ; 422 sinon",
            example="44",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=50, description="Nombre max de resultats"),
    ] = 10,
    inclure_arrondissements: Annotated[
        bool,
        Query(
            description=(
                "Ajoute les arrondissements municipaux de Paris, Lyon et "
                "Marseille du perimetre heberge (type_zone = arrondissement, "
                "commune_parent). Defaut false : la sortie ne change pas. La "
                "commune n'est jamais additionnee a ses arrondissements."
            ),
        ),
    ] = False,
) -> ReponseApi[list[ResultatCommuneSearch]]:
    """Search communes by name prefix or postal code.

    The commune referential (geo_communes) is always served, hosted or not:
    each result carries the hosting ``/v1/commune`` would serve for it (key
    ``commune``). ``demographie`` and ``adresses`` are read only for the
    departements where they are hosted (D-23).

    A postal-code search (five digits) always names the BAN coverage of its
    territory in ``meta.hebergement.ban`` (CR-01): out of coverage with its
    reason when the departement named by the prefix is not hosted, hosted
    with its ``departements`` otherwise, so a screen bounds its zero without
    a hard-coded perimeter (D-34). The meta never replaces a result: a hosted
    commune at a neighbouring prefix is still served. A search by name whose
    sources are all hosted keeps its response without meta.
    """
    departement_filtre = None
    if departement is not None:
        departement_filtre = real_departement(departement)
        if departement_filtre is None:
            raise ErreurApiError(
                422,
                "Departement inconnu",
                f"'{departement}' ne designe aucun departement",
            )
    rows = await search_communes(
        db,
        q,
        limit=limit,
        departement=departement_filtre,
        ban_departements=served_departements("ban", scope),
        demographie_departements=served_departements("demographie", scope),
        inclure_arrondissements=inclure_arrondissements,
        arrondissement_departements=served_departements("ban", scope),
    )
    resultats = [
        ResultatCommuneSearch(
            **row,
            hebergement=hosting_for_key(
                "commune", scope, commune_departement(row["code_insee"])
            ),
        )
        for row in rows
    ]
    meta = hosting.meta(get_meta_sources(["ban", "demographie"]))
    if POSTAL_CODE_RE.fullmatch(q.strip()) and "hebergement" not in meta:
        meta = {**meta, "hebergement": {"ban": hosting.hostings["ban"]}}
    if "hebergement" not in meta:
        return ReponseApi(donnees=resultats)
    return ReponseApi(donnees=resultats, meta=meta)


@router.get(
    "/communes",
    response_model=ReponseApi[CommunesListResponse],
    summary="Liste paginee des communes",
    description=(
        "Liste des communes francaises (referentiel geo_communes) avec "
        "recherche par nom (insensible aux accents), filtre par departement "
        "ou par code INSEE, tri (nom / departement / population) et "
        "pagination. Chaque ligne porte la population avec son annee et sa "
        "source (demographie, INSEE) quand ce jeu est heberge pour le "
        "departement de la commune, sinon la valeur du referentiel "
        "geo_communes, dont l'annee n'est pas connue (null) ; le tri par "
        "population porte sur la valeur servie. Avec code_insee, la ligne "
        "porte aussi le code postal lu dans la BAN quand elle est hebergee "
        "pour son departement. Par defaut le referentiel ne liste que des "
        "communes (type_zone = commune) ; inclure_arrondissements=true ajoute "
        "les arrondissements municipaux du perimetre heberge, rattaches a leur "
        "commune par commune_parent, sans jamais les additionner a elle."
    ),
)
async def get_communes(
    hosting: Annotated[
        RouteHosting,
        Depends(
            hosted_datasets("ban", "demographie", departement=code_insee_departement)
        ),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[
        str | None,
        Query(
            max_length=100,
            description="Recherche par nom de commune (insensible aux accents)",
        ),
    ] = None,
    departement: Annotated[
        str | None,
        Query(max_length=3, description="Filtre par code departement"),
    ] = None,
    code_insee: Annotated[
        str | None,
        Query(
            min_length=5,
            max_length=5,
            description=(
                "Restreint le referentiel a ce code INSEE (0 ou 1 ligne), "
                "commune hebergee ou non"
            ),
            example="44109",
        ),
    ] = None,
    sort_by: Annotated[
        Literal["nom", "departement", "population"],
        Query(description="Colonne de tri"),
    ] = "population",
    order: Annotated[
        Literal["asc", "desc"],
        Query(description="Sens du tri"),
    ] = "desc",
    page: Annotated[int, Query(ge=1, description="Numero de page")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=100, description="Resultats par page")
    ] = 50,
    inclure_arrondissements: Annotated[
        bool,
        Query(
            description=(
                "Ajoute les arrondissements municipaux de Paris, Lyon et "
                "Marseille du perimetre heberge (type_zone = arrondissement, "
                "commune_parent). Defaut false : la sortie ne change pas. La "
                "commune n'est jamais additionnee a ses arrondissements."
            ),
        ),
    ] = False,
) -> ReponseApi[CommunesListResponse]:
    """Paginated list of communes with search, filter and sort.

    The referential is served whatever the scope. ``adresses`` is read only
    in ``code_insee`` mode, and only when the BAN is hosted for the
    departement of that code (H2). The population follows the rule of the
    search and of the commune page (H6): ``demographie`` is read only for the
    departements where it is hosted (D-23). No source meta is served: the
    route stays a referential listing.
    """
    code = code_insee.strip().upper() if code_insee is not None else None
    rows, total_count = await list_communes(
        db,
        q=q,
        departement=departement,
        code_insee=code,
        with_code_postal=code is not None and hosting.hosts("ban"),
        sort_by=sort_by,
        order=order,
        page=page,
        per_page=per_page,
        demographie_departements=served_departements("demographie", scope),
        inclure_arrondissements=inclure_arrondissements,
        arrondissement_departements=served_departements("ban", scope),
    )
    has_next = (page * per_page) < total_count
    response = CommunesListResponse(
        results=[CommuneListRow(**row) for row in rows],
        total_count=total_count,
        page=page,
        per_page=per_page,
        has_next=has_next,
    )
    return ReponseApi(donnees=response)


# Flat synthesis columns read by the directory: median, then count, per type.
_ANNUAIRE_PRICE_COLUMNS: dict[str, tuple[str, str]] = {
    "maison": ("prix_m2_median_maison", "nb_mutations_12m_maison"),
    "appartement": ("prix_m2_median_appartement", "nb_mutations_12m_appartement"),
}
_NO_SYNTHESIS_ROW = "La synthèse communale n'a aucune ligne pour la commune."
# Populations are INTEGER columns: a larger bound is refused (422), never sent.
POPULATION_MAX = 2_147_483_647


def commune_served_departements(scope: InstanceScope) -> list[str]:
    """Departements where the commune synthesis is hosted (key ``commune``)."""
    return [
        departement
        for departement in scope.departements
        if hosting_for_key("commune", scope, departement).etat == "heberge"
    ]


def _absent_prices(etat: EtatColonne) -> PrixCommuneAnnuaire:
    """Both types absent for the same proven reason, without any value."""
    absent = PrixTypeAnnuaire(etat=etat)
    return PrixCommuneAnnuaire(maison=absent, appartement=absent)


def _served_prices(served: dict) -> PrixCommuneAnnuaire:
    """Prices of a synthesis row projected by ``apply_commune_coverage``.

    The state of a null median is the one the projection declared in
    ``etats``; a missing state is an API defect and fails the response
    rather than being invented (D-47).
    """
    states = served.get("etats") or {}
    prices = {}
    for kind, (median_column, count_column) in _ANNUAIRE_PRICE_COLUMNS.items():
        median = served.get(median_column)
        prices[kind] = PrixTypeAnnuaire(
            mediane_m2=median,
            effectif=served.get(count_column),
            etat=None if median is not None else states.get(median_column),
        )
    return PrixCommuneAnnuaire(**prices)


def _annuaire_departement(
    departement: str | None, population_min: int | None, population_max: int | None
) -> str | None:
    """The departement filter of the directory, after its 422 checks."""
    if (
        population_min is not None
        and population_max is not None
        and population_min > population_max
    ):
        raise ErreurApiError(
            422,
            "Tranche de population invalide",
            "population_min doit etre inferieure ou egale a population_max",
        )
    if departement is None:
        return None
    departement_filtre = real_departement(departement)
    if departement_filtre is None:
        raise ErreurApiError(
            422,
            "Departement inconnu",
            f"'{departement}' ne designe aucun departement",
        )
    return departement_filtre


def _out_of_coverage(motif: OutOfCoverageReason | None) -> PrixCommuneAnnuaire:
    assert motif is not None  # DatasetHosting enforces a reason when out
    return _absent_prices(EtatColonne(etat="out_of_coverage", motif=motif.value))


async def _hosted_prices(
    db: AsyncSession, scope: InstanceScope, hosted: list[str]
) -> tuple[dict[str, PrixCommuneAnnuaire], str | None]:
    """Prices of the hosted codes and the DVF window, as the comparison reads them.

    One synthesis read for a multi-departement page (union of the hosted
    sources), projected by ``apply_commune_coverage``; the window is read
    after the barrier only when a count is served. A hosted code without a
    synthesis row is ``not_reported``. A 503 propagates to the caller.
    """
    sources = sorted(
        {
            source
            for code in hosted
            for source in hosted_commune_sources(scope, commune_departement(code))
        }
    )
    prices: dict[str, PrixCommuneAnnuaire] = {}
    has_counts = False
    for row in await commune_synthese_par_codes(db, hosted, sources):
        code = row["code_insee"]
        couverture = commune_coverage(scope, code)
        served = apply_commune_coverage(row, couverture)
        if served is None:
            prices[code] = _out_of_coverage(couverture["demographie"].motif)
            continue
        prices[code] = _served_prices(served)
        has_counts = has_counts or any(
            served.get(count) is not None
            for _, count in _ANNUAIRE_PRICE_COLUMNS.values()
        )
    median_window = None
    if "dvf" in sources and has_counts:
        median_window = await fenetre_mediane_commune(db)
    missing = EtatColonne(etat="not_reported", motif=_NO_SYNTHESIS_ROW)
    for code in hosted:
        prices.setdefault(code, _absent_prices(missing))
    return prices, median_window


async def _annuaire_prices(
    db: AsyncSession, scope: InstanceScope, hebergements: dict[str, DatasetHosting]
) -> tuple[dict[str, PrixCommuneAnnuaire], PrixAnnuaire]:
    """Prices of a directory page and the state of their read.

    A commune out of coverage, or whose synthesis is hosted but not DVF (the
    source of every price, e.g. a departement DVF does not publish), gets its
    reason without any read: a missing synthesis row or an outage cannot turn
    it into ``not_reported`` or ``indisponible``. No code left, no read at
    all. A 503 of the synthesis or of its window keeps the list: the codes
    read get no price and the read is ``indisponible``.
    """
    prices: dict[str, PrixCommuneAnnuaire] = {}
    hosted: list[str] = []
    for code, h in hebergements.items():
        dvf = commune_coverage(scope, code)["dvf"] if h.etat == "heberge" else h
        if dvf.etat == "heberge":
            hosted.append(code)
        else:
            prices[code] = _out_of_coverage(dvf.motif)
    if not hosted:
        return prices, PrixAnnuaire(etat="sans_lecture")
    try:
        served, median_window = await _hosted_prices(db, scope, hosted)
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        return prices, PrixAnnuaire(etat="indisponible", motif=str(exc.detail))
    return {**prices, **served}, PrixAnnuaire(
        etat="servi", fenetre_mediane_dvf=median_window
    )


@router.get(
    "/communes/annuaire",
    response_model=ReponseApi[CommunesAnnuaireResponse],
    summary="Annuaire des communes avec prix medians",
    description=(
        "Annuaire pagine des communes du referentiel : par defaut celles des "
        "departements ou la synthese commune est hebergee (couverture=servies), "
        "ou tout le referentiel (couverture=toutes). Filtres nom (sous-chaine), "
        "departement et tranche de population servie (bornes inclusives, "
        "population inconnue exclue d'une tranche bornee), appliques au total "
        "et a la page ; tri par nom ou population. Chaque ligne porte la "
        "population servie avec son annee et sa source, l'hebergement de sa "
        "fiche et, pour les maisons et les appartements, la mediane au m2, "
        "l'effectif de ventes retenues et l'etat servi d'une mediane absente "
        "(hors couverture, effectif insuffisant, non renseignee). La fenetre "
        "DVF est donnee une fois. Une indisponibilite de la synthese laisse la "
        "liste servie avec prix.etat = indisponible, jamais des zeros. "
        "inclure_arrondissements=true ajoute les arrondissements municipaux de "
        "Paris, Lyon et Marseille du perimetre heberge (type_zone, "
        "commune_parent) ; la commune garde sa ligne, ses prix sont ceux de "
        "l'ensemble de ses arrondissements."
    ),
)
async def get_communes_annuaire(
    hosting: Annotated[
        RouteHosting, Depends(hosted_datasets("commune", "dvf", "demographie"))
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    nom: Annotated[
        str | None,
        Query(
            max_length=100,
            description="Sous-chaine du nom (insensible aux accents et a la casse)",
        ),
    ] = None,
    departement: Annotated[
        str | None,
        Query(
            max_length=3,
            description="Code departement (01..95, 2A, 2B, 971..976) ; 422 sinon",
        ),
    ] = None,
    population_min: Annotated[
        int | None,
        Query(
            ge=0, le=POPULATION_MAX, description="Population servie minimale, incluse"
        ),
    ] = None,
    population_max: Annotated[
        int | None,
        Query(
            ge=0, le=POPULATION_MAX, description="Population servie maximale, incluse"
        ),
    ] = None,
    couverture: Annotated[
        Literal["servies", "toutes"],
        Query(
            description=(
                "servies : departements ou la synthese commune est hebergee ; "
                "toutes : referentiel national"
            )
        ),
    ] = "servies",
    sort_by: Annotated[
        Literal["nom", "population"], Query(description="Colonne de tri")
    ] = "population",
    order: Annotated[Literal["asc", "desc"], Query(description="Sens du tri")] = "desc",
    page: Annotated[int, Query(ge=1, description="Numero de page")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=100, description="Resultats par page")
    ] = 50,
    inclure_arrondissements: Annotated[
        bool,
        Query(
            description=(
                "Ajoute les arrondissements municipaux de Paris, Lyon et "
                "Marseille du perimetre heberge (type_zone = arrondissement, "
                "commune_parent). Defaut false : la sortie ne change pas. La "
                "commune n'est jamais additionnee a ses arrondissements."
            ),
        ),
    ] = False,
) -> ReponseApi[CommunesAnnuaireResponse]:
    """Directory page of communes, then the prices of its hosted codes.

    The list is :func:`list_communes` with the directory filters, applied
    before pagination. The prices are read only for the hosted codes of the
    page, by ``commune_synthese_par_codes`` (publication barrier) and projected
    by ``apply_commune_coverage``, exactly as ``/v1/communes/comparaison``
    does: sources of a multi-departement page, window read after the barrier.
    No hosted code, no read. A 503 of the synthesis (or of its window) keeps
    the list and marks the prices ``indisponible``.
    """
    departement_filtre = _annuaire_departement(
        departement, population_min, population_max
    )
    servies = commune_served_departements(scope)
    rows, total_count = await list_communes(
        db,
        q=nom,
        departement=departement_filtre,
        departements=servies if couverture == "servies" else None,
        population_min=population_min,
        population_max=population_max,
        sort_by=sort_by,
        order=order,
        page=page,
        per_page=per_page,
        demographie_departements=served_departements("demographie", scope),
        inclure_arrondissements=inclure_arrondissements,
        arrondissement_departements=served_departements("ban", scope),
    )
    hebergements = {
        row["code_insee"]: hosting_for_key(
            "commune", scope, commune_departement(row["code_insee"])
        )
        for row in rows
    }
    prices, prix = await _annuaire_prices(db, scope, hebergements)

    response = CommunesAnnuaireResponse(
        results=[
            CommuneAnnuaireRow(
                **row,
                hebergement=hebergements[row["code_insee"]],
                prix=prices.get(row["code_insee"]),
            )
            for row in rows
        ],
        total_count=total_count,
        page=page,
        per_page=per_page,
        has_next=(page * per_page) < total_count,
        couverture=couverture,
        departements_servis=servies,
        prix=prix,
    )
    # Prices come from DVF and populations from demographie, through the
    # synthesis of each hosted commune; ``hebergement`` is added when one is out.
    meta = hosting.meta(get_meta_sources(["dvf", "demographie"]))
    return ReponseApi(donnees=response, meta=MetaReponse.model_validate(meta))


@router.get(
    "/departements",
    response_model=ReponseApi[list[ResultatDepartement]],
    summary="Liste des départements",
    description=(
        "Les 101 départements du référentiel avec code, nom et région (nom "
        "officiel accentué). Chaque ligne dit aussi l'hébergement de DVF (prix) "
        "pour le département, s'il est indexable (DVF hébergé et importé pour "
        "lui) et le jour du dernier import réussi des jeux de sa page (dvf, dpe, "
        "demographie) hébergés et importés pour lui, null sinon."
    ),
)
async def get_departements(
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReponseApi[list[ResultatDepartement]]:
    """List all French departements with region, prices hosting and page date.

    The indexing rule and the dates are the API's (plan 04.7-10, decisions 3
    and 4): the client writes no territory. No source table is read.
    """
    rows = await list_departements(db)
    facts = await import_facts(db, PAGE_SOURCES)
    resultats = [
        ResultatDepartement(
            code=row["code"],
            nom=row["nom"],
            region=DEPT_REGION.get(row["code"]),
            derniere_importation=page_day(scope, facts, row["code"]),
            prix=hosting_for("dvf", scope, row["code"]),
            indexable=indexable(scope, facts, row["code"]),
        )
        for row in rows
    ]
    return ReponseApi(donnees=resultats)


# ---------------------------------------------------------------------------
# Departement page and sitemap (plan 04.7-10, decisions 2 to 4)
# ---------------------------------------------------------------------------

# Two digits (01..95, and 00 or 99 that name nothing: 404), Corsica, or the
# three digits of an overseas departement. Anything else is malformed (422).
_CODE_DEPARTEMENT = re.compile(r"^(?:\d{2}|2[AB]|97\d)$")


def departement_code(
    code: Annotated[
        str,
        Path(
            description=(
                "Code du département : 01 à 95, 2A, 2B, 971 à 976 ; « 2a » est "
                "lu 2A. Mal formé : 422 ; absent du référentiel : 404."
            ),
            examples=["44"],
        ),
    ],
) -> str:
    """The requested departement code, validated before any read (422).

    Declared first by the routes, as ``comparison_codes``: nothing (scope,
    referential, view) is read for a malformed code (T-08).
    """
    normalized = code.strip().upper()
    if not _CODE_DEPARTEMENT.fullmatch(normalized):
        raise ErreurApiError(
            422,
            "Code de département invalide",
            f"'{code[:10]}' n'est pas un code de département.",
        )
    return normalized


def path_departement(request: Request) -> str | None:
    """Territory named by the ``code`` path parameter (None: not a departement)."""
    return real_departement(request.path_params.get("code"))


# Largest departement: under 900 communes. A list beyond this is a defect,
# answered as an error rather than cut (decision 3).
MAX_FICHES = 5_000

# The errors a client of the departement routes tells apart (the sitemap
# answers a 404 by a 404, a 503 by a 503 to retry): the code problems of
# ErreurApiError and FastAPI validation, the barrier's HTTPException detail.
_PROBLEME_DEPARTEMENT = {
    "application/problem+json": {"schema": ProblemDetail.model_json_schema()}
}
DEPARTEMENT_ERREURS: dict[int | str, dict] = {
    404: {
        "description": "Code absent du référentiel des départements.",
        "content": _PROBLEME_DEPARTEMENT,
    },
    422: {
        "description": "Code de département mal formé.",
        "content": _PROBLEME_DEPARTEMENT,
    },
}
DEPARTEMENT_INDISPONIBLE: dict[int | str, dict] = {
    503: {
        "description": (
            "Synthèse indisponible (publication en cours ou vues périmées) : "
            "réessayer plus tard, rien n'est servi à la place."
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                    "required": ["detail"],
                }
            }
        },
    },
}


async def _known_departement(db: AsyncSession, code: str) -> dict:
    """Identity of *code* in geo_departements; 404 when it names none."""
    identity = await departement_identite(db, code)
    if identity is None:
        raise ErreurApiError(
            404,
            "Département inconnu",
            f"'{code}' ne désigne aucun département du référentiel.",
        )
    return identity


@router.get(
    "/departements/{code}",
    response_model=ReponseApi[ResultatDepartementSynthese],
    responses={**DEPARTEMENT_ERREURS, **DEPARTEMENT_INDISPONIBLE},
    summary="Chiffres clés d'un département",
    description=(
        "Identité (référentiel), taille (communes, arrondissements), population "
        "des communes, prix médians au m² des maisons et des appartements sur "
        "12 mois glissants, documents DPE par classe et part F/G, dix communes "
        "les plus peuplées. Chaque bloc suit l'hébergement de sa source pour le "
        "département : hors couverture avec son motif, non renseigné (jeu pas "
        "importé pour le département, aucune vente ou aucun document), effectif "
        "insuffisant (moins de 5 ventes), sans base de calcul (aucune classe "
        "connue) ; jamais un zéro fabriqué. Code mal formé : 422 ; inconnu du "
        "référentiel : 404. Une indisponibilité de la synthèse rend toute la "
        "réponse en HTTP 503."
    ),
)
async def get_departement(
    code: Annotated[str, Depends(departement_code)],
    hosting: Annotated[
        RouteHosting,
        Depends(
            hosted_datasets("dvf", "dpe", "demographie", departement=path_departement)
        ),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReponseApi[ResultatDepartementSynthese]:
    """Key figures of one departement (plan 04.7-10, decision 2).

    The code is checked first (422), then looked up (404). Each block takes
    the state of its source for the departement (out of coverage, or hosted
    but not imported: ``not_reported``) before any value of the view, which is
    read, behind the barrier of the commune chain, only when one of the three
    sources is hosted; a barrier not ready is a 503 of the whole route.
    Identity, communes and principal communes come from the referential.
    """
    identity = await _known_departement(db, code)
    hosted = [key for key in PAGE_SOURCES if hosting.hosts(key)]
    if hosted:
        # The import facts and the view under one read lock: a publication
        # cannot commit between them, so a block never pairs the new figures
        # with the state and date of the previous generation.
        await acquire_publication_read_lock(db)
    facts = await import_facts(db, PAGE_SOURCES)
    states = {
        key: block_state(
            hosting.hostings[key], imported=imported_for(facts.get(key), code)
        )
        for key in PAGE_SOURCES
    }
    row = await departement_synthese(db, code, hosted) if hosted else None
    with_arrondissements = code in served_departements("ban", scope)
    nombre, arrondissements = await departement_communes(
        db, code, arrondissements=with_arrondissements
    )
    principales = await communes_principales(
        db,
        code,
        # Paris: a departement of one commune is read by its arrondissements.
        arrondissements=nombre == 1 and arrondissements > 0,
        # The census only where it is hosted AND imported for the departement,
        # as its population block; otherwise the referential value (rule H6).
        demographie_departements=[code] if states["demographie"] is None else [],
    )
    synthese = ResultatDepartementSynthese(
        code=identity["code"],
        nom=identity["nom"],
        region=DEPT_REGION.get(code),
        derniere_importation=page_day(scope, facts, code),
        indexable=indexable(scope, facts, code),
        couverture=dict(hosting.hostings),
        communes=CommunesDepartement(nombre=nombre, arrondissements=arrondissements),
        population=population_block(row, states["demographie"]),
        prix=prix_block(row, states["dvf"]),
        dpe=dpe_block(row, states["dpe"]),
        communes_principales=[CommunePrincipale(**zone) for zone in principales],
    )
    meta = hosting.meta(get_meta_sources(["dvf", "dpe", "demographie"]))
    return ReponseApi(donnees=synthese, meta=MetaReponse.model_validate(meta))


@router.get(
    "/departements/{code}/fiches",
    response_model=ReponseApi[FichesDepartement],
    responses=DEPARTEMENT_ERREURS,
    summary="Toutes les fiches d'un département",
    description=(
        "Codes de toutes les fiches du département en un appel, triés : les "
        "communes du référentiel et, là où la BAN est hébergée, les "
        "arrondissements municipaux de Paris, Lyon et Marseille. "
        "derniere_importation est le jour du dernier import réussi des sources "
        "de la fiche commune hébergées et importées pour le département, null "
        "sinon. Sans pagination ; code mal formé : 422, inconnu : 404."
    ),
)
async def get_departement_fiches(
    code: Annotated[str, Depends(departement_code)],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReponseApi[FichesDepartement]:
    """Every commune page of a departement, for the sitemap (decision 3).

    The referential is national: the list does not depend on the scope, only
    the arrondissements (listed where the BAN is hosted, as
    ``inclure_arrondissements``) and the date. Never cut: past MAX_FICHES the
    route fails instead.
    """
    await _known_departement(db, code)
    facts = await import_facts(db, COMMUNE_SOURCE_KEYS)
    fiches = await departement_fiches(
        db,
        code,
        arrondissements=code in served_departements("ban", scope),
        limit=MAX_FICHES + 1,
    )
    if len(fiches) > MAX_FICHES:
        raise ErreurApiError(
            500,
            "Liste des fiches trop longue",
            f"Plus de {MAX_FICHES} fiches : la liste n'est jamais tronquée.",
        )
    return ReponseApi(
        donnees=FichesDepartement(
            code=code,
            derniere_importation=fiches_day(scope, facts, code),
            fiches=[FicheDepartement(**fiche) for fiche in fiches],
        )
    )


# Links of a commune page to its neighbours (6 to 10, SEO audit 2026-11-14):
# enough to tie every page to the pages around it, few enough to stay a list.
VOISINES_MAX = 8
# Contours read by the index before the bordering ones are told apart: a
# commune borders rarely more than 15 others.
VOISINES_CANDIDATS = 24


@router.get(
    "/communes/voisines",
    response_model=ReponseApi[CommunesVoisines],
    responses={
        404: {
            "description": (
                "Code absent du référentiel des communes et arrondissements."
            ),
            "content": _PROBLEME_DEPARTEMENT,
        }
    },
    summary="Communes voisines d'une fiche",
    description=(
        f"Au plus {VOISINES_MAX} fiches proches d'une commune ou d'un "
        "arrondissement municipal, lues sur les contours du référentiel : les "
        "limitrophes d'abord, par population décroissante, puis les plus "
        "proches. Un arrondissement liste les autres arrondissements de sa "
        "commune et les communes autour. Référentiel national, sans "
        "périmètre ; code mal formé : 422, inconnu : 404."
    ),
)
async def get_communes_voisines(
    db: Annotated[AsyncSession, Depends(get_db)],
    code_insee: Annotated[
        str,
        Query(
            pattern=r"^(?:\d{5}|2[ABab]\d{3})$",
            description="Code INSEE de la commune ou de l'arrondissement",
            examples=["44109"],
        ),
    ],
) -> ReponseApi[CommunesVoisines]:
    """Neighbours of a commune page, for its internal links."""
    code = code_insee.upper()
    if not await zone_existe(db, code):
        raise ErreurApiError(
            404,
            "Commune inconnue",
            f"'{code}' ne désigne aucune commune ni aucun arrondissement.",
        )
    voisines = await communes_voisines(
        db, code, limit=VOISINES_MAX, candidats=VOISINES_CANDIDATS
    )
    return ReponseApi(
        donnees=CommunesVoisines(
            code_insee=code,
            voisines=[CommuneVoisine(**voisine) for voisine in voisines],
        )
    )


@router.get(
    "/zone/resolve",
    dependencies=[Depends(require_publication)],
    response_model=ReponseApi[ZoneResolveResponse],
    summary="Resultats pagines pour une zone",
    description=(
        "Requete paginee DVF ou DPE sur une commune ou un departement. "
        "Supporte les filtres surface_min, prix_max, type_local, dpe, "
        "date_min, date_max."
    ),
)
async def zone_resolve(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("dvf", "dpe", departement=zone_departement)),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    type: Annotated[
        str,
        Query(
            description="Type de zone : commune ou departement",
            example="commune",
        ),
    ],
    code: Annotated[
        str,
        Query(
            min_length=2,
            max_length=5,
            description="Code INSEE (commune) ou code departement",
            example="44109",
        ),
    ],
    datasets: Annotated[
        str,
        Query(
            description="Dataset a interroger : dvf ou dpe",
            example="dvf",
        ),
    ] = "dvf",
    page: Annotated[int, Query(ge=1, description="Numero de page")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=100, description="Resultats par page")
    ] = 25,
    # DVF filters
    surface_min: Annotated[
        float | None, Query(ge=0, description="Surface min (m2)")
    ] = None,
    surface_max: Annotated[
        float | None, Query(ge=0, description="Surface max (m2)")
    ] = None,
    prix_min: Annotated[
        float | None, Query(ge=0, description="Prix min (euros)")
    ] = None,
    prix_max: Annotated[
        float | None, Query(ge=0, description="Prix max (euros)")
    ] = None,
    type_local: Annotated[
        str | None,
        Query(description="Type de local (Maison, Appartement, etc.)"),
    ] = None,
    # DPE filter
    dpe: Annotated[
        str | None, Query(min_length=1, max_length=1, description="Etiquette DPE (A-G)")
    ] = None,
    # Date filters
    date_min: Annotated[
        str | None, Query(description="Date minimum (YYYY-MM-DD)")
    ] = None,
    date_max: Annotated[
        str | None, Query(description="Date maximum (YYYY-MM-DD)")
    ] = None,
) -> ReponseApi[ZoneResolveResponse]:
    """Paginated zone-level query for DVF or DPE datasets."""
    _validate_date_params(date_min, date_max)

    if type not in ("commune", "departement"):
        raise ErreurApiError(
            422,
            "Type de zone invalide",
            "Valeurs acceptees : commune, departement",
        )

    dataset = datasets.strip().lower()
    if dataset in hosting.hostings and not hosting.hosts(dataset):
        # Paginated envelope kept, nothing read (D-24).
        return ReponseApi(
            donnees=ZoneResolveResponse(
                results=[], total_count=0, page=page, per_page=per_page, has_next=False
            ),
            meta=hosting.meta(get_meta_source(dataset), keys=[dataset]),
        )
    if dataset == "dvf":
        rows, total_count = await zone_resolve_dvf(
            db,
            zone_type=type,
            code=code,
            page=page,
            per_page=per_page,
            surface_min=surface_min,
            surface_max=surface_max,
            prix_min=prix_min,
            prix_max=prix_max,
            type_local=type_local,
            date_min=date_min,
            date_max=date_max,
        )
        meta = get_meta_source("dvf")
    elif dataset == "dpe":
        rows, total_count = await zone_resolve_dpe(
            db,
            zone_type=type,
            code=code,
            page=page,
            per_page=per_page,
            dpe=dpe,
            date_min=date_min,
            date_max=date_max,
        )
        meta = get_meta_source("dpe")
    else:
        raise ErreurApiError(
            422,
            "Dataset invalide",
            "Valeurs acceptees : dvf, dpe",
        )

    has_next = (page * per_page) < total_count
    response = ZoneResolveResponse(
        results=rows,
        total_count=total_count,
        page=page,
        per_page=per_page,
        has_next=has_next,
    )
    return ReponseApi(donnees=response, meta=meta)


@router.get(
    "/zone/stats",
    dependencies=[Depends(require_publication)],
    response_model=ReponseApi[ZoneStats],
    summary="Stats agregees pour une zone",
    description=(
        "Indicateurs agreges pour une commune ou un departement : "
        "prix median au m2, nombre de transactions, surface moyenne, "
        "population, distribution DPE, taux de vacance."
    ),
)
async def zone_stats(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("dvf", "dpe", "commune", departement=zone_departement)),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    type: Annotated[
        str,
        Query(
            description="Type de zone : commune ou departement",
            example="commune",
        ),
    ],
    code: Annotated[
        str,
        Query(
            min_length=2,
            max_length=5,
            description="Code INSEE (commune) ou code departement",
            example="44109",
        ),
    ],
) -> ReponseApi[ZoneStats]:
    """Aggregated stats for a commune or departement."""
    if type not in ("commune", "departement"):
        raise ErreurApiError(
            422,
            "Type de zone invalide",
            "Valeurs acceptees : commune, departement",
        )

    # An out-of-coverage key is never read and its indicators stay null,
    # never 0 (SEM-01).
    # DVF aggregates
    dvf_stats = await zone_stats_dvf(db, type, code) if hosting.hosts("dvf") else {}

    # DPE distribution
    dpe_distribution = None
    if hosting.hosts("dpe"):
        dpe_dist_raw = await zone_stats_dpe(db, type, code)
        dpe_distribution = DpeDistribution(
            **{label: dpe_dist_raw.get(label, 0) for label in "ABCDEFG"}
        )

    # Commune-level data from v_commune_synthese, through the same masked
    # projection as /v1/commune and /v1/resolve.
    population = None
    taux_vacance = None
    if type == "commune" and hosting.hosts("commune"):
        couverture = commune_coverage(scope, code)
        hosted = [s for s, h in couverture.items() if h.etat == "heberge"]
        synthese_rows = (
            await commune_synthese_par_codes(db, [code], hosted)
            if "demographie" in hosted
            else []
        )
        synthese = (
            apply_commune_coverage(
                synthese_rows[0],
                commune_coverage(scope, synthese_rows[0]["code_insee"]),
            )
            if synthese_rows
            else None
        )
        if synthese is not None:
            population = synthese.get("population")
            taux_vacance = synthese.get("taux_vacance")

    prix_median = dvf_stats.get("prix_median_m2")
    stats = ZoneStats(
        prix_median_m2=round(prix_median, 2) if prix_median else None,
        nb_transactions=dvf_stats.get("nb_transactions"),
        surface_moyenne=(
            round(dvf_stats["surface_moyenne"], 1)
            if dvf_stats.get("surface_moyenne")
            else None
        ),
        # D-25: deja arrondi/masque (n.d.) dans zone_stats_dvf -- transmis tel quel.
        prix_median_m2_maison=dvf_stats.get("prix_median_m2_maison"),
        nb_transactions_maison=dvf_stats.get("nb_transactions_maison"),
        prix_median_m2_appartement=dvf_stats.get("prix_median_m2_appartement"),
        nb_transactions_appartement=dvf_stats.get("nb_transactions_appartement"),
        population=population,
        dpe_distribution=dpe_distribution,
        taux_vacance=taux_vacance,
    )
    return ReponseApi(
        donnees=stats,
        meta=hosting.meta(get_meta_sources(["dvf", "dpe"])),
    )
