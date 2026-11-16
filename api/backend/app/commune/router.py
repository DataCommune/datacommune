"""FastAPI router for the commune synthese endpoint."""

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ErreurApiError
from app.common.geo import (
    commune_and_its_arrondissement,
    require_location_params,
    resolve_location,
    zone_identity,
)
from app.common.schemas import DatasetProvenance, MetaReponse, ReponseApi
from app.commune.autre_maille import apply_autre_maille, codes_autre_maille
from app.commune.comparison import build_comparison
from app.commune.queries import (
    commune_synthese_par_codes,
    fenetre_mediane_commune,
    synthese_autre_maille,
    tendances_prix_par_codes,
)
from app.commune.schemas import (
    COMMUNE_SOURCE_KEYS,
    CodeHorsCouverture,
    CommuneComparee,
    ResultatCommuneSynthese,
    ResultatComparaisonCommunes,
    apply_commune_coverage,
    commune_coverage,
    hosted_commune_sources,
)
from app.database import get_db
from app.datasets import freshness
from app.datasets.provenance import (
    ProvenanceFacts,
    assemble_provenance,
    periode_de_source,
)
from app.datasets.queries import get_provenance_facts
from app.scope.coverage import hosting_for_key
from app.scope.dependencies import (
    RouteHosting,
    code_insee_departement,
    commune_departement,
    get_instance_scope,
    hosted_datasets,
)
from app.scope.model import InstanceScope, collectivite_outre_mer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Commune"])

_DVF_COUNT_COLUMNS = (
    "nb_mutations_12m",
    "nb_mutations_12m_maison",
    "nb_mutations_12m_appartement",
)


def _with_zone(served: dict[str, Any]) -> dict[str, Any]:
    """The served row completed with its ``type_zone`` and ``commune_parent``."""
    type_zone, commune_parent = zone_identity(served["code_insee"])
    return {**served, "type_zone": type_zone, "commune_parent": commune_parent}


async def _complete_from_other_mesh(
    db: AsyncSession,
    scope: InstanceScope,
    served_rows: list[dict[str, Any]],
    sources: list[str],
) -> None:
    """Inherit or point to the values of the other mesh (R-09), in place.

    Paris, Lyon, Marseille only: the commune of an arrondissement, or the
    arrondissements of a commune, are read once more behind the barrier and
    projected as the requested rows are; any other code reads nothing more.
    """
    codes = codes_autre_maille(served["code_insee"] for served in served_rows)
    if not codes:
        return
    autres = {}
    for row in await synthese_autre_maille(db, codes, sources):
        other = apply_commune_coverage(row, commune_coverage(scope, row["code_insee"]))
        if other is not None:
            autres[row["code_insee"]] = other
    for served in served_rows:
        apply_autre_maille(served, autres)


@router.get(
    "/commune",
    response_model=ReponseApi[list[ResultatCommuneSynthese]],
    summary="Profil complet d'une commune",
    description=(
        "Retourne le profil agrege d'une commune en un seul appel: "
        "demographie, revenus, zonage ABC, vacance, fiscalite locale, loyers, "
        "prix DVF, DPE, equipements, scolaire, monuments, foncier PM, fibre. "
        "Donnees pre-calculees. Une indisponibilite transitoire de la lecture "
        "est signalee par HTTP 503, sans resultat vide fabrique."
    ),
)
async def get_commune_synthese(
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("commune", departement=code_insee_departement)),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float | None,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=47.2184),
    ] = None,
    lon: Annotated[
        float | None,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=-1.5536),
    ] = None,
    code_insee: Annotated[
        str | None,
        Query(
            min_length=5,
            max_length=5,
            description=(
                "Code INSEE de la commune ou de l'arrondissement municipal "
                "(5 chiffres), lu à ce code exact : 75056 rend Paris seule, "
                "75112 le 12e arrondissement"
            ),
            example="44109",
        ),
    ] = None,
) -> ReponseApi[list[ResultatCommuneSynthese]]:
    """Query the commune synthese matview.

    Provide GPS coordinates (lat + lon) or a commune code (code_insee).
    Returns aggregated data from 13 datasets in a single response: a column
    whose source is out of coverage for the commune is null with its reason
    in ``couverture``, never 0 (SEM-01). ``meta.source`` carries the
    structured provenance of the thirteen sources (PROV-03).
    """
    # Hosted through its sources and its structural one (demographie): out
    # of coverage without it, or when all of them are.
    if hosting.none_hosted:
        require_location_params(lat, lon, code_insee)
        return ReponseApi(donnees=[], meta=hosting.meta(_commune_meta({})))
    # Exact code: Paris is 75056 alone, never the 21 rows of its arrondissements
    # in no order (the page reads the first). Its prices are summed on the
    # arrondissements by the view itself (plan 04.7-09, D3 and D4).
    loc = await resolve_location(
        db, lat=lat, lon=lon, code_insee=code_insee, expand=False
    )
    territory = commune_departement(code_insee or loc.codes[0])
    if code_insee is None:
        # GPS: the commune is known only now (geo_communes); same guard as a
        # code_insee request, before any read of the view (WR-01).
        located = RouteHosting(
            {"commune": hosting_for_key("commune", scope, territory)}
        )
        if located.none_hosted:
            return ReponseApi(donnees=[], meta=located.meta(_commune_meta({})))
    sources = hosted_commune_sources(scope, territory)
    rows = await commune_synthese_par_codes(db, loc.codes, sources)
    served_rows = []
    for row in rows:
        couverture = commune_coverage(scope, row["code_insee"])
        served = apply_commune_coverage(row, couverture)
        if served is not None:
            served_rows.append(served)
    await _complete_from_other_mesh(db, scope, served_rows, sources)
    # The yearly price series, for the rows whose dvf source is hosted only:
    # the others keep the field null with their reason in ``couverture``.
    with_dvf = [
        served
        for served in served_rows
        if served["couverture"]["dvf"].etat == "heberge"
    ]
    if with_dvf:
        tendances = await tendances_prix_par_codes(
            db, [served["code_insee"] for served in with_dvf], sources
        )
        for served in with_dvf:
            served["tendance_prix"] = tendances.get(served["code_insee"])
    has_dvf_counts = any(
        any(served.get(column) is not None for column in _DVF_COUNT_COLUMNS)
        for served in served_rows
    )
    median_window = None
    if "dvf" in sources and has_dvf_counts:
        median_window = await fenetre_mediane_commune(db)
    resultats = [
        ResultatCommuneSynthese(
            **_with_zone(served),
            fenetre_mediane_dvf=(
                median_window
                if any(served.get(column) is not None for column in _DVF_COUNT_COLUMNS)
                else None
            ),
        )
        for served in served_rows
    ]
    # Provenance facts are additive: a metadata read failure degrades to the
    # honest ``inconnue`` period, never to a failed synthesis.
    try:
        facts = await get_provenance_facts(db, list(COMMUNE_SOURCE_KEYS))
    except SQLAlchemyError:
        logger.warning("commune_provenance_facts_unavailable")
        facts = {}
    return ReponseApi(donnees=resultats, meta=hosting.meta(_commune_meta(facts)))


# INSEE commune code: five digits, or 2A/2B and three digits (Corsica).
_CODE_INSEE = re.compile(r"^(?:\d{5}|2[AB]\d{3})$")
MIN_COMPARED, MAX_COMPARED = 2, 4
# Four codes, three commas and some spaces: anything longer is refused unread.
MAX_CODES_LENGTH = 64


def comparison_codes(
    codes: Annotated[
        str | None,
        Query(
            description=(
                "2 à 4 codes INSEE distincts, séparés par des virgules ; la "
                "première commune est la référence des écarts."
            ),
            example="44109,85047",
        ),
    ] = None,
) -> list[str]:
    """The requested codes, validated before any read (422 otherwise).

    Declared first by the route so that nothing (scope, synthesis) is read
    for a malformed request (T-03-09-01). Every check lives here, none in
    ``Query``: a FastAPI parameter error would let the other dependencies
    (the scope read) run before the 422.
    """
    if codes is not None and len(codes) > MAX_CODES_LENGTH:
        raise ErreurApiError(
            422,
            "Paramètre codes trop long",
            f"Au plus {MAX_CODES_LENGTH} caractères sont attendus.",
        )
    parts = [part.strip().upper() for part in (codes or "").split(",")]
    if not codes or not MIN_COMPARED <= len(parts) <= MAX_COMPARED:
        raise ErreurApiError(
            422,
            "Nombre de communes invalide",
            f"De {MIN_COMPARED} à {MAX_COMPARED} codes INSEE séparés par des "
            "virgules sont attendus.",
        )
    malformed = [part for part in parts if not _CODE_INSEE.fullmatch(part)]
    if malformed:
        raise ErreurApiError(
            422,
            "Code INSEE invalide",
            f"'{malformed[0][:10]}' n'est pas un code INSEE de commune.",
        )
    if len(set(parts)) != len(parts):
        raise ErreurApiError(
            422, "Code INSEE en double", "Chaque commune ne peut figurer qu'une fois."
        )
    # A whole against its own part is no comparison: Paris contains its 12e.
    # Pure on the codes, so it is refused before any read like the checks above.
    if (pair := commune_and_its_arrondissement(parts)) is not None:
        commune, arrondissement = pair
        raise ErreurApiError(
            422,
            "Commune et arrondissement comparés",
            f"{arrondissement} est un arrondissement de {commune} : un tout ne se "
            "compare pas à sa propre partie. Comparez deux arrondissements, ou "
            "deux communes.",
        )
    return parts


async def _commune_identities(
    db: AsyncSession, codes: list[str]
) -> dict[str, dict[str, str]]:
    """Name and departement of *codes* in the referential, hosted or not (H2).

    Communes come from ``geo_communes``, municipal arrondissements of Paris, Lyon
    and Marseille from ``geo_arrondissements`` (migration 099), whose ``nom`` is
    the one of geo.api.gouv.fr: a name is never composed from a code. A code is
    in one table only (``geo_arrondissements.code`` is not a commune).
    """
    result = await db.execute(
        text(
            "SELECT code, nom, departement_code FROM geo_communes "
            "WHERE code = ANY(:codes) "
            "UNION ALL "
            "SELECT code, nom, departement_code FROM geo_arrondissements "
            "WHERE code = ANY(:codes)"
        ),
        {"codes": codes},
    )
    return {
        row.code: {"nom": row.nom, "departement": row.departement_code}
        for row in result
    }


@router.get(
    "/communes/comparaison",
    response_model=ReponseApi[ResultatComparaisonCommunes],
    summary="Comparaison de 2 à 4 communes",
    description=(
        "Rend, pour 2 à 4 communes, leur synthèse (même projection que "
        "/v1/commune) et, par valeur comparée, une cellule par commune : "
        "valeur ou état, période, et pour les colonnes 2 à 4 l'écart à la "
        "première colonne ou le motif pour lequel il n'est pas calculé "
        "(périodes, unités ou définitions différentes, donnée absente, commune "
        "hors couverture, valeur non numérique). Aucun score ni classement. "
        "Un code absent du référentiel est rendu dans codes_inconnus, sauf "
        "celui d'une collectivité d'outre-mer, rendu hors couverture dans "
        "codes_hors_couverture ; une indisponibilité de la synthèse rend "
        "toute la réponse en HTTP 503."
    ),
)
async def compare_communes(
    codes: Annotated[list[str], Depends(comparison_codes)],
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("commune"))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReponseApi[ResultatComparaisonCommunes]:
    """Compare communes; the comparability rule is app.commune.comparison.

    Identities come from the referential (geo_communes, geo_arrondissements),
    whatever the scope.
    Each commune gets the hosting ``/v1/commune`` would serve for it; the
    syntheses of the hosted ones are read in one statement, then projected by
    ``apply_commune_coverage`` exactly as ``/v1/commune`` does. A 503 of the
    publication barrier makes the whole response a 503.
    """
    identities = await _commune_identities(db, codes)
    known = [code for code in codes if code in identities]
    # D-123: a collectivity is out of coverage with its reason, not unknown.
    collectivites = [
        code
        for code in codes
        if code not in identities and collectivite_outre_mer(code) is not None
    ]
    hebergements = {
        code: hosting_for_key("commune", scope, commune_departement(code))
        for code in known
    }
    hosted = [code for code in known if hebergements[code].etat == "heberge"]
    served_rows: dict[str, dict] = {}
    sources: list[str] = []
    median_window: str | None = None
    if hosted:
        sources = sorted(
            {
                source
                for code in hosted
                for source in hosted_commune_sources(scope, commune_departement(code))
            }
        )
        for row in await commune_synthese_par_codes(db, hosted, sources):
            code = row["code_insee"]
            served = apply_commune_coverage(row, commune_coverage(scope, code))
            if served is not None:
                served_rows[code] = served
        await _complete_from_other_mesh(db, scope, list(served_rows.values()), sources)
        # The 12 months the flat dvf columns are computed on (C32, C34), read as
        # landmark ④ reads them (C29), after the barrier of the synthesis; a
        # failure here follows the synthesis, never an invented period.
        has_dvf_counts = any(
            any(served.get(column) is not None for column in _DVF_COUNT_COLUMNS)
            for served in served_rows.values()
        )
        if "dvf" in sources and has_dvf_counts:
            median_window = await fenetre_mediane_commune(db)
    communes = []
    for code in known:
        served = served_rows.get(code)
        communes.append(
            CommuneComparee(
                code_insee=code,
                nom=identities[code]["nom"],
                departement=identities[code]["departement"],
                hebergement=hebergements[code],
                synthese=(
                    None
                    if served is None
                    else ResultatCommuneSynthese(
                        **_with_zone(served),
                        fenetre_mediane_dvf=(
                            median_window
                            if any(
                                served.get(column) is not None
                                for column in _DVF_COUNT_COLUMNS
                            )
                            else None
                        ),
                    )
                ),
            )
        )
    # Provenance facts only when a synthesis was read: a request of communes
    # all out of coverage reads nothing but the referential, as /v1/commune.
    facts = {}
    if hosted:
        try:
            facts = await get_provenance_facts(db, list(COMMUNE_SOURCE_KEYS))
        except SQLAlchemyError:
            logger.warning("commune_provenance_facts_unavailable")
    meta = _commune_meta(facts)
    comparison = build_comparison(
        communes,
        codes_inconnus=[
            code
            for code in codes
            if code not in identities and code not in collectivites
        ],
        # The period of a value of a source without a vintage column (C28):
        # its true label, else its upstream day, else the day of its last
        # successful import (C14); nothing known, no period.
        source_periods={
            key: periode_de_source(facts.get(key)) for key in COMMUNE_SOURCE_KEYS
        },
        dvf_median_window=median_window,
    )
    comparison.codes_hors_couverture = [
        CodeHorsCouverture(
            code_insee=code,
            hebergement=hosting_for_key("commune", scope, commune_departement(code)),
        )
        for code in collectivites
    ]
    return ReponseApi(donnees=comparison, meta=hosting.meta(meta))


def _commune_meta(facts: dict[str, ProvenanceFacts]) -> dict:
    """Structured provenance of the thirteen sources; period never invented."""
    now = freshness._now()
    provenances: list[DatasetProvenance] = [
        assemble_provenance(key, facts.get(key, ProvenanceFacts()), now)
        for key in COMMUNE_SOURCE_KEYS
    ]
    return MetaReponse(source=provenances).model_dump()
