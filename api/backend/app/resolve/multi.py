"""Multi-dataset query logic with graceful degradation.

Queries datasets in PARALLEL via asyncio.gather with per-task
AsyncSession isolation. Each dataset gets its own session from the
factory, so a failure in one dataset does not affect the others.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any, NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.antennes.queries import antennes_par_point
from app.ban.queries import reverse_geocoder
from app.batiments.queries import batiments_par_point
from app.bdnb.client import query_batiment_by_adresse
from app.cadastre.queries import parcelle_par_point, parcelle_par_reference
from app.common.exceptions import ErreurApiError
from app.common.geo import commune_at_point
from app.common.proxy_coverage import is_partial, proxy_sources
from app.common.publication_readiness import (
    PUBLICATION_DATASETS,
    describe_read_failure,
    guard_publication,
)
from app.common.schemas import OutOfCoverageReason
from app.common.source_catalog import RESOLVER_SOURCE_MAP
from app.commune.queries import commune_synthese_par_codes
from app.commune.schemas import apply_commune_coverage, commune_coverage
from app.connectivite.queries import eligibilite_par_adresse
from app.copropriete.queries import copropriete_par_codes
from app.demographie.queries import demographie_par_codes
from app.dpe import references as dpe_references
from app.dpe.attribution import classify_dpe_attribution
from app.dpe.inventory_service import (
    DPE_INVENTORY_DEFAULT_PAGE_SIZE,
    DpeInventoryServiceResult,
    assert_inventory_date_current,
    load_dpe_inventory_result,
)
from app.dpe.queries import dpe_par_identifiants_ban
from app.dpe.references import RefreshRequiredError
from app.dpe.schemas import (
    DpeAttribution,
    DpeBuildingContext,
    DpeInventoryDistribution,
    DpeInventoryPage,
)
from app.dvf.attribution import decide_dvf_attribution, provenance_type_bien
from app.dvf.queries import dvf_par_id_parcelle, dvf_par_parcelle
from app.dvf.schemas import DvfAttribution
from app.equipements.queries import equipements_par_point
from app.errial.client import (
    extract_avis_summary,
    query_avis,
    resolve_parcelle_by_coords,
)
from app.fiscalite.queries import fiscalite_par_codes
from app.georisques.client import query_all_risks, query_polluted_sites
from app.logements.queries import cles_ban_batiment_rnb
from app.loyers.queries import loyers_par_codes
from app.monuments.queries import monuments_par_point
from app.permis.queries import permis_par_codes
from app.resolve import telemetry
from app.resolve.schemas import (
    Avertissement,
    DatasetErreur,
    DatasetResolutionState,
    out_of_coverage_state,
    resolution_state,
)
from app.revenus.queries import revenus_par_codes
from app.rnb.client import query_by_coords as rnb_query_by_coords
from app.scolaire.queries import college_de_secteur
from app.scope.coverage import hosting_by_source
from app.scope.model import InstanceScope, territoire_couverture
from app.sirene.client import entreprises_a_proximite
from app.transport.queries import transports_a_proximite
from app.urbanisme.client import query_urbanisme
from app.vacance.queries import vacance_par_codes
from app.zonage_abc.queries import zonage_par_codes

logger = logging.getLogger(__name__)

# Known datasets -- extend here as new datasets are added
DATASETS_DISPONIBLES: set[str] = {
    "ban",
    "cadastre",
    "dvf",
    "dpe",
    "loyers",
    "risques",
    "pollutions",
    "urbanisme",
    "batiments",
    "monuments",
    "connectivite",
    "entreprises",
    "scolaire",
    "transports",
    # v6.1 — newly wired datasets
    "fiscalite",
    "zonage_abc",
    "copropriete",
    "permis",
    "vacance",
    "revenus",
    "demographie",
    "errial",
    "bdnb",
    "rnb",
    "antennes",
    "equipements",
    "commune",
}

# Dataset classification by I/O type
DB_DATASETS: frozenset[str] = frozenset(
    {
        "ban",
        "cadastre",
        "dvf",
        "dpe",
        "loyers",
        "batiments",
        "monuments",
        "transports",
        "connectivite",
        "scolaire",
        # v6.1
        "fiscalite",
        "zonage_abc",
        "copropriete",
        "permis",
        "vacance",
        "revenus",
        "demographie",
        "antennes",
        "equipements",
        "commune",
    }
)
HTTP_DATASETS: frozenset[str] = frozenset(
    {"risques", "pollutions", "urbanisme", "entreprises", "errial", "bdnb", "rnb"}
)
DEPENDS_ON_INSEE: frozenset[str] = frozenset(
    {
        "connectivite",
        "loyers",
        "scolaire",
        # v6.1
        "fiscalite",
        "zonage_abc",
        "copropriete",
        "vacance",
        "revenus",
        "demographie",
        "commune",
    }
)
# BDNB needs the BAN address key (cle_interop_adr = ban_id)
DEPENDS_ON_BAN_ID: frozenset[str] = frozenset({"bdnb"})

# Sentinel to distinguish caught exceptions from None data
_SENTINEL_ERROR = object()

# Sentinel: caller did NOT resolve a canonical identity (legacy path).
# Distinct from None, which means "identity resolved, no canonical parcel"
# (typed absence — cadastre/dvf must stay absent, never re-derived by point).
_UNRESOLVED: Any = object()

# QE-01 (audit 2026-08-21): bound the fan-out. Without a cap, one request with
# N datasets opens N parallel AsyncSessions (pool 10+20) and fires N outbound
# proxy calls (georisques, Sirene, ...) — pool exhaustion + third-party hammering.
MAX_DATASETS_PAR_REQUETE = 5
MAX_CONCURRENCY_DATASETS = 3
_dataset_semaphore = asyncio.Semaphore(MAX_CONCURRENCY_DATASETS)


def _normalize_dataset_name(name: str) -> str:
    """Normalize frontend dataset keys: hyphens → underscores.

    The frontend uses 'zonage-abc' but the backend module is 'zonage_abc'.
    """
    return name.replace("-", "_")


# ---------------------------------------------------------------------------
# DPE: address inventory and non-attributive V2 decision (Phase 58.1)
# ---------------------------------------------------------------------------


class ResultatDpeResolu(NamedTuple):
    """Completed public inventory from the shared transactional service."""

    inventory: DpeInventoryPage


_DPE_BUILDING_CONTEXT_TASK = "__dpe_building_context__"
_DPE_BUILDING_CONTEXT_WARNING_CODE = "CONTEXTE_BATIMENT_INDISPONIBLE"
_DPE_BUILDING_CONTEXT_WARNING_MESSAGE = (
    "Contexte bâtiment DPE temporairement indisponible."
)


def _unavailable_dpe_result() -> ResultatDpeResolu:
    inventory = DpeInventoryPage(
        state="unavailable",
        as_of_date=dpe_references.paris_today(),
        total=None,
        distribution=DpeInventoryDistribution(),
        page_size=DPE_INVENTORY_DEFAULT_PAGE_SIZE,
        has_more=False,
        read_state="partial",
    )
    return ResultatDpeResolu(inventory=inventory)


def _dpe_decision_from_inventory(
    inventory: DpeInventoryPage,
    building_context: DpeBuildingContext | None,
) -> DpeAttribution:
    """Map the shared inventory state without electing an individual document."""
    common = {
        "building_context": building_context,
        "graph_state": inventory.graph_state,
    }
    if inventory.state == "ready":
        return DpeAttribution(
            status="unassigned",
            reason="dwelling_identity_unproven",
            **common,
        )
    if inventory.state == "absent":
        return DpeAttribution(status="absent", reason="not_reported", **common)
    if inventory.state == "identity_unavailable":
        return DpeAttribution(
            status="absent",
            reason="identity_unavailable",
            graph_state=inventory.graph_state,
        )
    return DpeAttribution(
        status="error",
        reason="temporary_error",
        graph_state=inventory.graph_state,
    )


async def _dpe_building_context(
    session_factory: async_sessionmaker[AsyncSession],
    canonical_parcelle: Any,
    ban_id: str,
    *,
    correlation_id: str | None,
) -> tuple[DpeBuildingContext | None, list[Avertissement]]:
    """Load other-address rows separately and expose only their aggregate."""
    parcelle = None if canonical_parcelle is _UNRESOLVED else canonical_parcelle
    if not parcelle:
        return (None, [])
    try:
        async with session_factory() as db:
            await guard_publication(db, ["dpe"])
            cles = await cles_ban_batiment_rnb(db, parcelle, ban_id)
            autres = [cle for cle in cles if cle and cle != ban_id]
            if not autres:
                return (None, [])
            rows, truncated = await dpe_par_identifiants_ban(db, autres)
        decision = classify_dpe_attribution(
            ban_id,
            (),
            building_rows=rows,
            building_truncated=truncated,
        )
        return (decision.building_context, [])
    except Exception as exc:  # noqa: BLE001
        telemetry.emit_resolve_event(
            logger,
            "dpe_building_context_failed",
            level=logging.WARNING,
            dataset="dpe",
            error_code=telemetry.error_code_for(exc),
            correlation_id=correlation_id,
        )
        return (
            None,
            [
                Avertissement(
                    dataset="dpe",
                    code=_DPE_BUILDING_CONTEXT_WARNING_CODE,
                    message=_DPE_BUILDING_CONTEXT_WARNING_MESSAGE,
                )
            ],
        )


async def _load_dpe_result(
    session_factory: async_sessionmaker[AsyncSession],
    canonical_parcelle: Any,
    canonical_ban_id: Any,
    *,
    correlation_id: str | None,
) -> ResultatDpeResolu:
    """Finish the inventory snapshot before any proxy task can be created."""
    ban_id = None if canonical_ban_id is _UNRESOLVED else canonical_ban_id
    try:
        service_result: DpeInventoryServiceResult = await load_dpe_inventory_result(
            session_factory,
            ban_id=ban_id,
            limit=DPE_INVENTORY_DEFAULT_PAGE_SIZE,
        )
    except Exception as exc:  # noqa: BLE001
        telemetry.emit_resolve_event(
            logger,
            "dataset_query_failed",
            level=logging.ERROR,
            dataset="dpe",
            error_code=telemetry.error_code_for(exc),
            correlation_id=correlation_id,
        )
        # The event's fields travel in ``extra``, which the structured log
        # does not render: the cause goes in the message, message-free (C60).
        logger.warning(
            "resolve: lecture DPE indisponible (%s)", describe_read_failure(exc)
        )
        return _unavailable_dpe_result()

    inventory = service_result.page
    return ResultatDpeResolu(
        inventory=inventory,
    )


async def _query_dpe_building_context(
    session_factory: async_sessionmaker[AsyncSession],
    canonical_parcelle: Any,
    ban_id: str,
    *,
    correlation_id: str | None,
) -> tuple[str, DpeBuildingContext | None, list[Avertissement]]:
    """Run optional RNB widening beside, never inside, the inventory snapshot."""
    async with _dataset_semaphore:
        context, warnings = await _dpe_building_context(
            session_factory,
            canonical_parcelle,
            ban_id,
            correlation_id=correlation_id,
        )
    return (_DPE_BUILDING_CONTEXT_TASK, context, warnings)


def donnees_dataset_dpe(_attribution: DpeAttribution) -> None:
    """Keep the legacy dataset slot empty; documents live in the inventory."""
    return None


def etat_dataset_dpe(attribution: DpeAttribution) -> DatasetResolutionState:
    """Project V2 ``unassigned|absent|error`` onto the coarse taxonomy.

    The status is authoritative because ``datasets.dpe`` is always empty.
    ``identity_unavailable`` stays on the detailed attribution: the historical
    coarse taxonomy has no equivalent and must not invent one.
    """
    if attribution.status == "error":
        return resolution_state("dpe", None, is_error=True)
    if attribution.status == "absent":
        return resolution_state("dpe", None, is_error=False)
    return DatasetResolutionState(dataset="dpe", presence="value")


# ---------------------------------------------------------------------------
# DVF: parcel history AND its attribution decision (Phase 57, D-16/D-37)
# ---------------------------------------------------------------------------


class ResultatDvfResolu(NamedTuple):
    """What the « dvf » dispatch answers: the history AND its qualification.

    The two travel together on purpose. ``mutations`` keeps feeding
    ``datasets.dvf`` verbatim (the parcel history, D-11/D-17) and
    ``attribution`` is the decision computed ONCE about the consulted
    property. The consumer reads the state on the decision, never on the
    length of the list -- that inference is the substitution Phase 57 removes.
    """

    mutations: list[dict] | None
    attribution: DvfAttribution


def _resultat_dvf(
    rows: list[dict] | None, type_bien: str | None = None
) -> ResultatDvfResolu:
    """Qualify the mutations of the resolved parcel (D-10/D-13/D-16/D-37).

    ``type_bien`` is the type of the consulted dwelling as DECLARED by the
    caller (``/v1/resolve?type_bien=``, D-41), already normalized and validated
    by the router. It is relayed VERBATIM and interpreted nowhere on the way:
    the resolved identity of ``/v1/resolve`` is an address and a cadastral
    parcel -- it carries no dwelling type of its own (see
    :class:`app.resolve.schemas.ResultatResolution`), and inferring one from
    the mutations would be circular. ``None`` stays an EXPLICIT unknown, and
    the decision owns the honest outcome (``type_bien_inconnu``) rather than
    attributing a sale at random.
    """
    mutations = list(rows or [])
    attribution = decide_dvf_attribution(mutations, type_bien=type_bien)
    return ResultatDvfResolu(mutations or None, attribution)


async def _dispatch_db(  # noqa: C901
    name: str,
    db: AsyncSession,
    lat: float,
    lon: float,
    code_insee: str | None,
    *,
    nom_voie: str | None = None,
    numero: str | None = None,
    canonical_parcelle: Any = _UNRESOLVED,
    canonical_ban_id: Any = _UNRESOLVED,
    type_bien: str | None = None,
    scope: InstanceScope | None = None,
) -> tuple[Any, list[Avertissement]]:
    """Dispatch a DB dataset query.

    Returns ``(data, avertissements)`` where ``avertissements`` is a
    (possibly empty) list of :class:`Avertissement` objects signaling
    degraded enrichment for this dataset.

    The ``nom_voie`` and ``numero`` keyword arguments are threaded into
    connectivite and scolaire queries when both are present, enabling
    address-granular results.  When missing, a machine-readable
    ``ADRESSE_INCOMPLETE`` warning is appended so consumers know the
    data is commune-level only.

    ``canonical_parcelle`` carries the identity already resolved by the
    router (Phase 45, D-02): a string routes cadastre/dvf through the exact
    reference/id queries; ``None`` keeps them absent (no silent point
    fallback); the ``_UNRESOLVED`` sentinel preserves the audited legacy
    point-based path for callers without a resolved identity.

    ``canonical_ban_id`` is its Phase 51 counterpart and is DELIBERATELY
    distinct from the ``ban_id`` argument above: the latter is reverse-geocoded
    inside this module for the historical BDNB proxy, while DPE only ever
    consumes the key the router resolved (D-04, T-51-05). Mixing the two would
    reintroduce a second identity for the very dataset this phase is fixing.
    """
    avs: list[Avertissement] = []

    if name == "ban":
        rows = await reverse_geocoder(db, lat, lon, limit=1)
        return (rows[0] if rows else None, avs)
    if name == "cadastre":
        if canonical_parcelle is _UNRESOLVED:
            rows = await parcelle_par_point(db, lat, lon)
            return (rows[0] if rows else None, avs)
        if canonical_parcelle is None:
            return (None, avs)
        rows = await parcelle_par_reference(db, canonical_parcelle)
        return (rows[0] if rows else None, avs)
    if name == "dvf":
        # « Sur ce bien »: sales of the EXACT cadastre parcel, never a
        # proximity radius. The former 500m radius surfaced neighbours' sales
        # and mislabeled the most recent one as this bien's last sale
        # (violating the fiche's no-substitution invariant). With a resolved
        # identity, the canonical id feeds DVF directly — a boundary point or
        # concave centroid can never select a second parcel.
        if canonical_parcelle is _UNRESOLVED:
            rows = await dvf_par_parcelle(db, lat, lon, limit=5)
            return (_resultat_dvf(rows, type_bien), avs)
        if canonical_parcelle is None:
            # No canonical parcel: nothing was even looked up. That is an
            # absence with its own reason, never « aucune vente ici ».
            return (
                ResultatDvfResolu(
                    None,
                    DvfAttribution(
                        status="absente",
                        reason="parcelle_indisponible",
                        # D-41: the caller's declaration survives an absence it
                        # did not cause. « le type était déclaré ET aucune
                        # parcelle n'a été résolue » is a different fact from
                        # « le type est inconnu ».
                        **provenance_type_bien(type_bien),
                    ),
                ),
                avs,
            )
        rows = await dvf_par_id_parcelle(db, canonical_parcelle, limit=5)
        return (_resultat_dvf(rows, type_bien), avs)
    if name == "loyers":
        if code_insee is None:
            return (None, avs)
        rows = await loyers_par_codes(db, [code_insee])
        return (rows[0] if rows else None, avs)
    if name == "batiments":
        rows = await batiments_par_point(db, lat, lon, limit=5)
        return (rows if rows else None, avs)
    if name == "monuments":
        rows = await monuments_par_point(db, lat, lon, limit=5)
        return (rows if rows else None, avs)
    if name == "transports":
        if scope is None:
            raise RuntimeError("the transports aggregate needs the instance scope")
        # Read only the branches of the sources hosted for the commune's
        # departement when it is known (IN-04), for the scope otherwise.
        # A collectivity is out of coverage with its reason (D-123).
        territory = territoire_couverture(code_insee)
        hostings = hosting_by_source(
            RESOLVER_SOURCE_MAP["transports"], scope, territory
        )
        sources = [s for s, h in hostings.items() if h.etat == "heberge"]
        if not sources:
            return (None, avs)
        rows = await transports_a_proximite(db, lat, lon, limit=10, sources=sources)
        return (rows if rows else None, avs)
    if name == "connectivite":
        if code_insee is None:
            return (None, avs)
        if nom_voie and numero:
            rows = await eligibilite_par_adresse(
                db, code_insee, nom_voie=nom_voie, numero=numero
            )
        else:
            rows = await eligibilite_par_adresse(db, code_insee)
            avs.append(
                Avertissement(
                    dataset="connectivite",
                    code="ADRESSE_INCOMPLETE",
                    message=(
                        "Resultats tronques: adresse sans voie/numero; "
                        "enrichissement limite a la commune."
                    ),
                )
            )
        return (rows if rows else None, avs)
    if name == "scolaire":
        if code_insee is None:
            return (None, avs)
        if nom_voie and numero:
            # college_de_secteur expects numero as int for range/parity checks
            try:
                numero_int: int | None = int(numero)
            except (ValueError, TypeError):
                numero_int = None
            rows = await college_de_secteur(
                db, code_insee, nom_voie=nom_voie, numero=numero_int
            )
        else:
            rows = await college_de_secteur(db, code_insee)
            avs.append(
                Avertissement(
                    dataset="scolaire",
                    code="ADRESSE_INCOMPLETE",
                    message=(
                        "Resultats tronques: adresse sans voie/numero; "
                        "enrichissement limite a la commune."
                    ),
                )
            )
        return (rows if rows else None, avs)
    # v6.1 — INSEE-dependent datasets
    if name == "fiscalite":
        if code_insee is None:
            return (None, avs)
        rows = await fiscalite_par_codes(db, [code_insee])
        return (rows[0] if rows else None, avs)
    if name == "zonage_abc":
        if code_insee is None:
            return (None, avs)
        rows = await zonage_par_codes(db, [code_insee])
        return (rows[0] if rows else None, avs)
    if name == "copropriete":
        if code_insee is None:
            return (None, avs)
        rows = await copropriete_par_codes(db, [code_insee])
        return (rows if rows else None, avs)
    if name == "permis":
        if code_insee is None:
            return (None, avs)
        rows = await permis_par_codes(db, [code_insee])
        return (rows if rows else None, avs)
    if name == "vacance":
        if code_insee is None:
            return (None, avs)
        rows = await vacance_par_codes(db, [code_insee])
        return (rows[0] if rows else None, avs)
    if name == "revenus":
        if code_insee is None:
            return (None, avs)
        rows = await revenus_par_codes(db, [code_insee])
        return (rows[0] if rows else None, avs)
    if name == "demographie":
        if code_insee is None:
            return (None, avs)
        rows = await demographie_par_codes(db, [code_insee])
        return (rows[0] if rows else None, avs)
    if name == "commune":
        if code_insee is None:
            return (None, avs)
        if scope is None:
            # The masking needs the scope: never serve an unmasked synthesis.
            raise RuntimeError("the commune synthesis needs the instance scope")
        couverture = commune_coverage(scope, code_insee)
        hosted = [s for s, h in couverture.items() if h.etat == "heberge"]
        if "demographie" not in hosted:
            # No structural source, no row to serve: nothing is read.
            return (None, avs)
        rows = await commune_synthese_par_codes(db, [code_insee], hosted)
        if not rows:
            return (None, avs)
        served = apply_commune_coverage(
            rows[0], commune_coverage(scope, rows[0]["code_insee"])
        )
        return (served, avs)
    # v6.1 — lat/lon datasets
    if name == "antennes":
        rows = await antennes_par_point(db, lat, lon)
        return (rows if rows else None, avs)
    if name == "equipements":
        rows = await equipements_par_point(db, lat, lon)
        return (rows if rows else None, avs)
    return (None, avs)


async def _dispatch_http(
    name: str,
    lat: float,
    lon: float,
    ban_id: str | None = None,
) -> Any:
    """Dispatch an HTTP dataset query. No DB session needed."""
    if name == "risques":
        return await query_all_risks(lat, lon)
    if name == "pollutions":
        return await query_polluted_sites(lat, lon)
    if name == "urbanisme":
        return await query_urbanisme(lat, lon)
    if name == "entreprises":
        data = await entreprises_a_proximite(lat, lon)
        return data["results"]
    # v6.1
    if name == "rnb":
        return await rnb_query_by_coords(lat, lon)
    if name == "errial":
        resolved = await resolve_parcelle_by_coords(lat, lon)
        if resolved is None:
            return None
        code_insee_errial, code_parcelle = resolved
        raw = await query_avis(code_insee_errial, code_parcelle)
        return extract_avis_summary(raw)
    if name == "bdnb":
        if ban_id is None:
            return None
        rows = await query_batiment_by_adresse(ban_id)
        return rows
    return None


async def _query_one_dataset(
    name: str,
    session_factory: async_sessionmaker[AsyncSession],
    lat: float,
    lon: float,
    code_insee: str | None,
    ban_id: str | None = None,
    nom_voie: str | None = None,
    numero: str | None = None,
    canonical_parcelle: Any = _UNRESOLVED,
    canonical_ban_id: Any = _UNRESOLVED,
    type_bien: str | None = None,
    correlation_id: str | None = None,
    scope: InstanceScope | None = None,
) -> tuple[str, Any, list[Avertissement]]:
    """Query a single dataset with its own session.

    Returns ``(name, data, avertissements)`` on success, or
    ``(name, _SENTINEL_ERROR, [])`` on failure. Degradation events carry only
    allowlisted fields (dataset, bounded error_code, correlation_id) — never
    the location or the exception text (:mod:`app.resolve.telemetry`).
    """
    if name == "permis":
        from app.permis.availability import QUARANTINE_REASON

        return (
            name,
            _SENTINEL_ERROR,
            [
                Avertissement(
                    dataset="permis",
                    code="SOURCE_EN_QUARANTAINE",
                    message=QUARANTINE_REASON,
                )
            ],
        )
    try:
        # QE-01: the semaphore bounds globally concurrent DB sessions AND
        # third-party HTTP calls, whatever the caller's dataset list size.
        async with _dataset_semaphore:
            if name in DB_DATASETS:
                async with session_factory() as db:
                    if name in PUBLICATION_DATASETS:
                        await guard_publication(db, [name])
                    data, avs = await _dispatch_db(
                        name,
                        db,
                        lat,
                        lon,
                        code_insee,
                        nom_voie=nom_voie,
                        numero=numero,
                        canonical_parcelle=canonical_parcelle,
                        canonical_ban_id=canonical_ban_id,
                        type_bien=type_bien,
                        scope=scope,
                    )
            else:
                data = await _dispatch_http(name, lat, lon, ban_id=ban_id)
                avs = []
                if is_partial(proxy_sources(data)):
                    avs.append(
                        Avertissement(
                            dataset=name,
                            code="SOURCE_INCOMPLETE",
                            message=(
                                "Sous-sources indisponibles ou première page "
                                "non exhaustive ; ne pas conclure à une absence."
                            ),
                        )
                    )
        return (name, data, avs)
    except Exception as exc:  # noqa: BLE001
        telemetry.emit_resolve_event(
            logger,
            "dataset_query_failed",
            level=logging.ERROR,
            dataset=name,
            error_code=telemetry.error_code_for(exc),
            correlation_id=correlation_id,
        )
        return (name, _SENTINEL_ERROR, [])


async def query_multiple_datasets(  # noqa: C901
    session_factory: async_sessionmaker[AsyncSession],
    datasets: list[str],
    lat: float,
    lon: float,
    *,
    canonical_parcelle: Any = _UNRESOLVED,
    canonical_ban_id: Any = _UNRESOLVED,
    type_bien: str | None = None,
    correlation_id: str | None = None,
    out_of_coverage: Mapping[str, OutOfCoverageReason] | None = None,
    scope: InstanceScope | None = None,
) -> dict[str, Any]:
    """Query datasets with DPE snapshot-first and per-dataset error isolation.

    The shared DPE inventory completes its dedicated transaction first. Its
    optional RNB aggregate then runs beside all other datasets via
    ``asyncio.gather``; every DB task owns a dedicated session.

    ``canonical_parcelle`` is the identity already resolved by the router
    (Phase 45): cadastre/dvf consume it directly instead of re-deriving a
    parcel from the point. Legacy callers omit it and keep the audited
    point-based behaviour. ``canonical_ban_id`` is an explicitly selected
    address identity used only by the DPE inventory. ``type_bien`` is the Phase
    57 twin (D-41): the type of the consulted dwelling DECLARED by the caller,
    relayed verbatim to the DVF decision and interpreted nowhere else.
    ``correlation_id`` is the opaque per-request id from the router.
    ``out_of_coverage`` maps the (normalized) keys the router declared out of
    coverage to their reason (D-24): they are validated and counted like any
    other key, then never queried -- no table read, no upstream call.
    ``scope`` is the persisted instance scope: aggregate keys (``commune``)
    project their rows through it, so an out-of-coverage source is served
    null, never 0.

    Returns {"donnees": {name: data, ...}, "erreurs": [DatasetErreur, ...] | None,
    "dpe_inventory": DpeInventoryPage | None,
    "dpe_attribution": DpeAttribution | None,
    "dvf_attribution": DvfAttribution | None, ...}
    """
    # Normalize dataset names (hyphens → underscores for frontend compat)
    datasets = [_normalize_dataset_name(d) for d in datasets]

    # QE-01: deduplicate preserving first-seen order, then cap the fan-out.
    # Duplicates must neither multiply tasks nor count toward the cap.
    datasets = list(dict.fromkeys(datasets))
    if len(datasets) > MAX_DATASETS_PAR_REQUETE:
        raise ErreurApiError(
            422,
            "Trop de datasets demandes",
            f"Maximum {MAX_DATASETS_PAR_REQUETE} datasets par requete "
            f"(recu: {len(datasets)}). Reduisez la liste.",
        )

    # Validate that at least one dataset is known
    valid = [d for d in datasets if d in DATASETS_DISPONIBLES]
    unknown = [d for d in datasets if d not in DATASETS_DISPONIBLES]

    if not valid:
        raise ErreurApiError(
            400,
            "Aucun dataset valide",
            f"Datasets inconnus: {', '.join(unknown)}. "
            f"Disponibles: {', '.join(sorted(DATASETS_DISPONIBLES))}",
        )

    declared_out = {
        name: motif for name, motif in (out_of_coverage or {}).items() if name in valid
    }
    queried = [d for d in valid if d not in declared_out]

    donnees: dict[str, Any] = {}
    erreurs: list[DatasetErreur] = []

    # Add errors for unknown datasets upfront
    disponibles = ", ".join(sorted(DATASETS_DISPONIBLES))
    for name in unknown:
        erreurs.append(  # noqa: PERF401
            DatasetErreur(
                dataset=name,
                message=f"Dataset inconnu. Disponibles: {disponibles}",
            )
        )

    # DPE is intentionally completed before Phase 1 and before proxy tasks are
    # even created. ``load_dpe_inventory_result`` owns a short REPEATABLE READ
    # transaction and has left its ``async with`` block when this await returns.
    # RNB widening, when admissible, then runs in a separate session and can
    # only add an aggregate context.
    dpe_result: ResultatDpeResolu | None = None
    if "dpe" in queried:
        async with _dataset_semaphore:
            dpe_result = await _load_dpe_result(
                session_factory,
                canonical_parcelle,
                canonical_ban_id,
                correlation_id=correlation_id,
            )

    # Phase 1: Pre-resolve code_insee, ban_id, nom_voie, numero if needed
    code_insee: str | None = None
    ban_id: str | None = None
    nom_voie: str | None = None
    numero: str | None = None
    needs_ban = (DEPENDS_ON_INSEE | DEPENDS_ON_BAN_ID) & set(queried)
    if needs_ban and scope is not None and not scope.hosts("ban"):
        # The BAN is a source (D-23): unhosted, never read. The commune comes
        # from the referential; address-granular fields stay unknown.
        async with session_factory() as db:
            code_insee = await commune_at_point(db, lat, lon)
    elif needs_ban:
        async with session_factory() as db:
            ban_rows = await reverse_geocoder(db, lat, lon, limit=1)
            if ban_rows:
                code_insee = ban_rows[0].get("code_commune")
                ban_id = ban_rows[0].get("ban_id")
                # Thread address-granular fields for connectivite/scolaire
                nom_voie = ban_rows[0].get("nom_voie") or None
                numero = ban_rows[0].get("numero") or None

    # Phase 2: Parallel gather -- each task gets its own session
    start = time.perf_counter()
    tasks = [
        _query_one_dataset(
            name,
            session_factory,
            lat,
            lon,
            code_insee,
            ban_id,
            nom_voie=nom_voie,
            numero=numero,
            canonical_parcelle=canonical_parcelle,
            canonical_ban_id=canonical_ban_id,
            type_bien=type_bien,
            correlation_id=correlation_id,
            scope=scope,
        )
        for name in queried
        if name != "dpe"
    ]
    dpe_ban_id = None if canonical_ban_id is _UNRESOLVED else canonical_ban_id
    if (
        dpe_result is not None
        # The building widening reads rnb_liens: only while rnb is hosted.
        and (scope is None or scope.hosts("rnb"))
        and dpe_ban_id
        and dpe_result.inventory.state in {"ready", "absent"}
        and canonical_parcelle is not _UNRESOLVED
        and canonical_parcelle is not None
    ):
        tasks.append(
            _query_dpe_building_context(
                session_factory,
                canonical_parcelle,
                dpe_ban_id,
                correlation_id=correlation_id,
            )
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.perf_counter() - start
    telemetry.emit_resolve_event(
        logger,
        "resolve_datasets_queried",
        dataset_count=len(queried),
        duration_ms=int(elapsed * 1000),
        correlation_id=correlation_id,
    )

    # Phase 3: Collect results with error isolation
    avertissements: list[Avertissement] = []
    dataset_states: dict[str, DatasetResolutionState] = {}
    dpe_inventory: DpeInventoryPage | None = None
    dpe_building_context: DpeBuildingContext | None = None
    dpe_attribution: DpeAttribution | None = None
    dvf_attribution: DvfAttribution | None = None
    for name, motif in declared_out.items():
        dataset_states[name] = out_of_coverage_state(name, motif)
    if dpe_result is not None:
        dpe_inventory = dpe_result.inventory
        if dpe_inventory.state == "unavailable":
            erreurs.append(
                DatasetErreur(
                    dataset="dpe",
                    message="Erreur lors de l'interrogation du dataset dpe",
                )
            )
    for result in results:
        if isinstance(result, Exception):
            # Safety net: gather-level exception (should not happen since
            # _query_one_dataset catches internally). Never surface the raw
            # exception text to the client (no-leak invariant, e5a662d5).
            telemetry.emit_resolve_event(
                logger,
                "resolve_gather_exception",
                level=logging.ERROR,
                error_code=telemetry.error_code_for(result),
                correlation_id=correlation_id,
            )
            erreurs.append(
                DatasetErreur(
                    dataset="unknown",
                    message="Erreur interne lors de l'interrogation d'un dataset",
                )
            )
        else:
            name, data, avs = result  # type: ignore[misc]
            avertissements.extend(avs)
            if name == _DPE_BUILDING_CONTEXT_TASK:
                dpe_building_context = data
                continue
            if data is _SENTINEL_ERROR:
                erreurs.append(
                    DatasetErreur(
                        dataset=name,
                        message=f"Erreur lors de l'interrogation du dataset {name}",
                    )
                )
                if name == "dvf":
                    # Same doctrine (D-16): a technical failure must never be
                    # readable as « aucune vente sur cette parcelle ».
                    dvf_attribution = DvfAttribution(
                        status="error",
                        reason="temporary_error",
                        # D-41: a technical failure does not erase what the
                        # caller declared -- the provenance travels intact.
                        **provenance_type_bien(type_bien),
                    )
                # Typed state additive to the historical `erreurs` entry.
                dataset_states[name] = resolution_state(name, None, is_error=True)
            elif isinstance(data, ResultatDvfResolu):
                # The history stays in `datasets.dvf` and keeps its historical
                # coarse state; the decision travels beside it, never inside.
                dvf_attribution = data.attribution
                donnees[name] = data.mutations
                dataset_states[name] = resolution_state(
                    name, data.mutations, is_error=False
                )
            else:
                donnees[name] = data
                dataset_states[name] = resolution_state(name, data, is_error=False)

    if dpe_inventory is not None:
        try:
            assert_inventory_date_current(dpe_inventory.as_of_date)
        except RefreshRequiredError:
            # Proxies may finish after midnight, outside the inventory snapshot.
            dpe_inventory = _unavailable_dpe_result().inventory
            dpe_building_context = None
            erreurs.append(
                DatasetErreur(
                    dataset="dpe",
                    message="Actualisation de l'inventaire DPE requise",
                )
            )
        dpe_attribution = _dpe_decision_from_inventory(
            dpe_inventory,
            dpe_building_context,
        )
        donnees["dpe"] = donnees_dataset_dpe(dpe_attribution)
        dataset_states["dpe"] = etat_dataset_dpe(dpe_attribution)

    # Gathering completes DPE before the other datasets, but the public maps
    # retain the caller's first-seen order after normalization/deduplication.
    donnees = {name: donnees[name] for name in valid if name in donnees}
    dataset_states = {
        name: dataset_states[name] for name in valid if name in dataset_states
    }

    return {
        "donnees": donnees,
        "erreurs": erreurs if erreurs else None,
        "avertissements": avertissements,
        "dataset_states": dataset_states,
        "dpe_inventory": dpe_inventory,
        "dpe_attribution": dpe_attribution,
        "dvf_attribution": dvf_attribution,
    }
