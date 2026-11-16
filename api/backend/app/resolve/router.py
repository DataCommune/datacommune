"""FastAPI router for the polymorphic resolve endpoint.

A single GET /v1/resolve?q=... that auto-detects input type (address,
GPS coordinates, or cadastral reference) and routes to the appropriate
resolver. Returns a unified ResultatResolution with guaranteed lat/lon.
"""

import logging
import re
import time
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.ban.queries import geocoder, reverse_geocoder
from app.cadastre.queries import (
    parcelle_par_point,
    parcelle_par_reference,
    parcelles_proches,
)
from app.common.exceptions import ErreurApiError
from app.common.schemas import (
    DatasetHosting,
    DatasetProvenance,
    EditorialState,
    MetaReponse,
    OutOfCoverageReason,
    ReponseApi,
    ResolutionOutcome,
)
from app.common.source_catalog import (
    RESOLVER_SOURCE_MAP,
    _normalize_key,
    expand_source_keys,
)
from app.common.sources import get_meta_source
from app.database import async_session, get_db
from app.datasets import freshness
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.datasets.queries import get_provenance_facts
from app.dvf.attribution import TYPES_BIEN_ADMIS, provenance_type_bien
from app.dvf.schemas import DvfAttribution
from app.etl.base import DEPARTEMENTS
from app.resolve import telemetry
from app.resolve.address_selection import (
    ADDRESS_CANDIDATE_LIMIT,
    normalize_address_text,
    select_address_candidate,
)
from app.resolve.detector import detect_input_type
from app.resolve.multi import (
    DATASETS_DISPONIBLES,
    DEPENDS_ON_BAN_ID,
    query_multiple_datasets,
)
from app.resolve.schemas import (
    RESOLUTION_RAISONS,
    DatasetErreur,
    DatasetResolutionState,
    ResultatMultiResolution,
    ResultatResolution,
)
from app.resolve.snap import SNAP_RADIUS_M, select_snapped_candidates
from app.scope.coverage import hosting_for, hosting_for_key
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope, territoire_couverture

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Parcelle"])

# Postal-code hint inside a free-text address query (coverage proof only —
# the value itself is never logged).
_RE_POSTAL_HINT = re.compile(r"\b(\d{5})\b")


# ---------------------------------------------------------------------------
# Canonical identity helpers (Phase 45, D-01/D-02/D-04)
# ---------------------------------------------------------------------------


def _departement_from_code(code: str) -> str:
    """Two-digit department prefix (three for DOM 97x) of a 5-char code."""
    return code[:3] if code.startswith("97") else code[:2]


def _dept_hint(type_detecte: str, parsed: dict) -> str | None:
    """Derive a department hint from the parsed input, without any I/O."""
    if type_detecte == "cadastre":
        return _departement_from_code(parsed["ref"])
    if type_detecte == "adresse":
        m = _RE_POSTAL_HINT.search(parsed["q"])
        if m:
            return _departement_from_code(m.group(1))
    return None


# Postcodes 20xxx cover both Corsican departements (INSEE codes 2A / 2B).
_CORSICA_POSTCODE_PREFIX = "20"
_CORSICA_DEPARTEMENTS = frozenset({"2A", "2B"})


def _provably_out_of_coverage(dept: str | None, scope: InstanceScope) -> bool:
    """True only with positive proof: the persisted scope excludes *dept*.

    The scope in the database is the only source (D-23); an empty scope hosts
    nothing. A Corsican postcode (20xxx) names 2A or 2B: it proves nothing
    only while the scope hosts one of them. Without a department hint, or with
    one naming no French department (synthetic 99xxx), there is no proof, so
    the caller falls back to ``not_reported`` — never ``out_of_coverage`` on a
    guess.
    """
    if dept == _CORSICA_POSTCODE_PREFIX:
        return not (_CORSICA_DEPARTEMENTS & set(scope.departements))
    return bool(dept in DEPARTEMENTS and dept not in scope.departements)


def _absence_outcome(
    issue: str, motif: OutOfCoverageReason | None = None
) -> ResolutionOutcome:
    """Typed absence outcome reusing the Phase 43 taxonomy messages."""
    return ResolutionOutcome(
        issue=issue, raison=RESOLUTION_RAISONS.get(issue), motif=motif
    )


def _outcome_from_results(resultats: list[ResultatResolution]) -> ResolutionOutcome:
    """Machine-readable issue for the response: exact/ambiguous/typed absence."""
    if not resultats:
        return _absence_outcome("not_reported")
    base = resultats[0]
    if base.resolution == "ambiguous":
        return ResolutionOutcome(
            issue="ambiguous",
            raison=RESOLUTION_RAISONS["ambiguous"],
            parcelles_candidates=base.parcelles_candidates,
        )
    return ResolutionOutcome(issue="exact")


def _apply_parcel_candidates(
    result: ResultatResolution,
    candidates: list[dict],
) -> None:
    """Apply the deterministic parcel-candidates cardinality rule (D-04).

    One candidate = exact canonical parcel; several = explicit ambiguity with
    the stable-ordered candidate list — never a silent first-row pick. The
    single-candidate branch also backfills ``code_insee`` from the parcel's
    commune when BAN could not provide it.
    """
    ids = [row["id_parcelle"] for row in candidates]
    if len(ids) == 1:
        result.id_parcelle = ids[0]
        if result.code_insee is None:
            result.code_insee = candidates[0].get("commune")
    elif len(ids) > 1:
        result.resolution = "ambiguous"
        result.parcelles_candidates = ids


async def _snap_parcel_candidates(
    db: AsyncSession,
    lat: float,
    lon: float,
    correlation_id: str,
) -> list[dict]:
    """Nearest-parcel fallback used only when the strict lookup found nothing.

    A BAN entrance point can sit on the roadway, strictly outside the parcel
    it belongs to (2 rue des Essais, Nantes: 0.0 m to the abutting parcel, 9.6 m
    to the next one), which left ``id_parcelle`` null on a score-1.0 address.
    The bounded nearby lookup + :func:`select_snapped_candidates` restore the
    identity without ever picking silently between close neighbours: a clear
    winner is exact, a tie remains ambiguous.
    """
    nearby = await parcelles_proches(db, lat, lon, rayon_m=SNAP_RADIUS_M)
    selected = select_snapped_candidates(nearby)
    if selected:
        telemetry.emit_resolve_event(
            logger,
            "resolve_parcelle_snapped",
            correlation_id=correlation_id,
            outcome="exact" if len(selected) == 1 else "ambiguous",
        )
    return selected


def _format_adresse(row: dict) -> str:
    """Build a human-readable address string from geocoder fields."""
    numero = row.get("numero") or ""
    rep = row.get("rep") or ""
    nom_voie = row.get("nom_voie") or ""
    code_postal = row.get("code_postal") or ""
    nom_commune = row.get("nom_commune") or ""

    parts = []
    if numero:
        parts.append(f"{numero}{rep}")
    if nom_voie:
        parts.append(nom_voie)

    street = " ".join(parts)
    city = f"{code_postal} {nom_commune}".strip()
    return f"{street}, {city}" if street else city


async def _resolve_adresse(
    db: AsyncSession,
    q: str,
    correlation_id: str = "",
    *,
    cadastre: bool = True,
) -> list[ResultatResolution]:
    """Resolve an established address before querying any parcel or dataset.

    ``cadastre=False``: the cadastre source is not hosted, its parcels are
    never read (D-23); the identity carries no parcel.
    """
    rows = await geocoder(
        db,
        normalize_address_text(q),
        limit=ADDRESS_CANDIDATE_LIMIT + 1,
        strict_identity=True,
    )
    row = select_address_candidate(q, rows)
    if row is None:
        return []

    result = ResultatResolution(
        type_detecte="adresse",
        latitude=row["latitude"],
        longitude=row["longitude"],
        adresse=_format_adresse(row),
        commune=row.get("nom_commune"),
        code_postal=row.get("code_postal"),
        code_insee=row.get("code_commune"),
        score=row.get("score"),
        # Exact DPE attachment key for /v1/bien/logements (RESEARCH-L1): the
        # geocoder already carries it, nothing new is queried here.
        ban_id=row.get("ban_id"),
    )
    if not cadastre:
        return [result]
    candidates = await parcelle_par_point(db, result.latitude, result.longitude)
    if not candidates:
        candidates = await _snap_parcel_candidates(
            db, result.latitude, result.longitude, correlation_id
        )
    _apply_parcel_candidates(result, candidates)
    return [result]


async def _resolve_gps(
    db: AsyncSession,
    lat: float,
    lon: float,
    correlation_id: str = "",
    *,
    ban: bool = True,
    cadastre: bool = True,
) -> list[ResultatResolution]:
    """Resolve GPS coordinates: reverse geocode + parcel candidates.

    Returns a typed absence (empty list) when neither an address nor a
    parcel exists at the point — never a fabricated identity. A source the
    instance does not host (``ban``, ``cadastre``) is never read (D-23).
    """
    reverse_rows = await reverse_geocoder(db, lat, lon, limit=1) if ban else []
    candidates = await parcelle_par_point(db, lat, lon) if cadastre else []
    if cadastre and not candidates:
        candidates = await _snap_parcel_candidates(db, lat, lon, correlation_id)
    if not reverse_rows and not candidates:
        return []

    result = ResultatResolution(
        type_detecte="gps",
        latitude=lat,
        longitude=lon,
    )
    if reverse_rows:
        row = reverse_rows[0]
        result.adresse = _format_adresse(row)
        result.commune = row.get("nom_commune")
        result.code_postal = row.get("code_postal")
        result.code_insee = row.get("code_commune")
        # Same key as the address mode: the reverse geocoder already read it.
        result.ban_id = row.get("ban_id")

    _apply_parcel_candidates(result, candidates)
    return [result]


async def _resolve_cadastre(
    db: AsyncSession,
    ref: str,
    *,
    ban: bool = True,
) -> list[ResultatResolution]:
    """Resolve a cadastral reference to its centroid + optional address.

    The explicit reference IS the canonical identity: the centroid is
    display-only and is never fed back into a point lookup — a concave
    parcel's centroid may geometrically fall inside a neighbour (D-02).
    """
    rows = await parcelle_par_reference(db, ref)
    if not rows:
        return []

    row = rows[0]
    lat = row["centroide_lat"]
    lon = row["centroide_lon"]

    result = ResultatResolution(
        type_detecte="cadastre",
        latitude=lat,
        longitude=lon,
        id_parcelle=row["id_parcelle"],
        code_insee=row.get("commune"),
    )

    # Enrich with nearest address via reverse geocoding (display only),
    # only while the BAN source is hosted.
    reverse_rows = await reverse_geocoder(db, lat, lon, limit=1) if ban else []
    if reverse_rows:
        addr_row = reverse_rows[0]
        result.adresse = _format_adresse(addr_row)
        result.commune = addr_row.get("nom_commune")
        result.code_postal = addr_row.get("code_postal")

    return [result]


def _parse_forced_type(
    type_force: str,
    q: str,
) -> tuple[str, dict]:
    """Parse input *q* according to the user-forced type."""
    stripped = q.strip()
    if type_force == "cadastre":
        return ("cadastre", {"ref": stripped.upper()})
    if type_force == "gps":
        # Expect lat,lon -- split on comma, semicolon, or whitespace
        parts = re.split(r"[,;\s]+", stripped)
        if len(parts) != 2:
            raise ErreurApiError(
                422,
                "Format GPS invalide",
                "Format attendu : 'lat,lon'.",
            )
        try:
            lat = float(parts[0])
            lon = float(parts[1])
        except ValueError as exc:
            raise ErreurApiError(
                422,
                "Coordonnees GPS invalides",
                "Les deux coordonnees doivent etre numeriques.",
            ) from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ErreurApiError(
                422,
                "Coordonnees GPS hors limites",
                "La latitude doit etre comprise entre -90 et 90 et la longitude "
                "entre -180 et 180.",
            )
        return ("gps", {"lat": lat, "lon": lon})
    return ("adresse", {"q": stripped})


def _parse_type_bien(type_bien: str | None) -> str | None:
    """Normalize the DECLARED type of the consulted dwelling, or reject it.

    D-41/D-40. Absent or empty means UNKNOWN (``None``): the behaviour of every
    existing caller is preserved bit for bit. Any other value outside the closed
    vocabulary is REJECTED with a 422 rather than degraded to the unknown -- a
    typo that reads like a legitimate choice is worse than an error, because it
    would be believed.

    The vocabulary is IMPORTED from :mod:`app.dvf.attribution`, never
    re-declared here: two lists that drift apart by one release is exactly the
    defect class this phase closes.
    """
    if type_bien is None:
        return None
    normalise = type_bien.strip().lower()
    if not normalise:
        return None
    if normalise not in TYPES_BIEN_ADMIS:
        raise ErreurApiError(
            422,
            "Type de bien invalide",
            "Valeurs acceptées : maison, appartement.",
        )
    return normalise


def _assemble_provenance_list(
    source_keys: list[str],
    facts_by_key: dict[str, ProvenanceFacts],
    now: datetime,
) -> list[DatasetProvenance]:
    """Assemble an ordered provenance list from expanded canonical source keys.

    Order is preserved from ``source_keys`` (already deduplicated by
    :func:`expand_source_keys`). Sources without dynamic facts fall back to
    empty :class:`ProvenanceFacts` (honest ``inconnu`` state).
    """
    return [
        assemble_provenance(key, facts_by_key.get(key, ProvenanceFacts()), now)
        for key in source_keys
    ]


def _departement_from_cp(code_postal: str | None) -> str | None:
    """Department prefix of a postal code: two digits, three for 97x (overseas).

    Same rule as ``departement_from_insee`` for overseas codes (97100 -> 971).
    Corsican postcodes stay ``20`` (2A or 2B, see ``_postcode_departements``).
    """
    if not code_postal or len(code_postal) < 2 or not code_postal[:2].isdigit():
        return None
    if code_postal.startswith("97") and len(code_postal) >= 3:
        return code_postal[:3]
    return code_postal[:2]


def _postcode_departements(dept: str) -> frozenset[str]:
    """Departements a postcode prefix may name: 20 is both 2A and 2B."""
    if dept == _CORSICA_POSTCODE_PREFIX:
        return _CORSICA_DEPARTEMENTS
    return frozenset({dept})


def _proven_out_of_coverage(
    sources: list[str],
    dept: str,
    prov_by_source: dict[str, DatasetProvenance],
) -> bool:
    """True only when effective department coverage proves the point is outside.

    Requires at least one source with a concrete ``departements`` coverage
    (the proof). If any such source includes a departement the prefix may
    name (``20``: 2A or 2B) the point may be covered and this returns False
    (no upgrade). Without coverage proof it returns False, keeping the state
    at ``not_reported``.
    """
    candidates = _postcode_departements(dept)
    proven = False
    for source in sources:
        prov = prov_by_source.get(source)
        cov = prov.couverture if prov else None
        if cov and cov.type == "departements" and cov.departements:
            proven = True
            if candidates & set(cov.departements):
                return False
    return proven


def _refine_dataset_states(
    states: dict[str, DatasetResolutionState],
    prov_by_source: dict[str, DatasetProvenance],
    code_postal: str | None,
    *,
    scope: InstanceScope,
) -> None:
    """Upgrade ``not_reported`` states with real editorial/coverage proof.

    In place. ``source_delayed`` only when a backing source is proven late
    (editorial ``retarde``); ``out_of_coverage`` only when effective department
    coverage excludes the resolved postal code. Its reason: the territory when
    the persisted scope does not host the department, the source otherwise.
    All other states are untouched.
    """
    dept = _departement_from_cp(code_postal)
    for name, state in states.items():
        if state.absence_reason != "not_reported":
            continue
        try:
            sources = expand_source_keys([name])
        except KeyError:
            continue
        delayed = any(
            (prov := prov_by_source.get(s)) is not None
            and prov.etat == EditorialState.RETARDE
            for s in sources
        )
        if delayed:
            state.absence_reason = "source_delayed"
            state.raison = RESOLUTION_RAISONS["source_delayed"]
        elif (
            dept
            # A prefix naming no real departement (98, a malformed code) is
            # never a proof of « hors couverture » (02-07 rule).
            and _postcode_departements(dept) <= set(DEPARTEMENTS)
            and _proven_out_of_coverage(sources, dept, prov_by_source)
        ):
            state.absence_reason = "out_of_coverage"
            state.raison = RESOLUTION_RAISONS["out_of_coverage"]
            state.motif = (
                OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE
                if not _postcode_departements(dept) & set(scope.departements)
                else OutOfCoverageReason.SOURCE_SANS_COUVERTURE
            )


def _identity_source(type_detecte: str, scope: InstanceScope) -> str | None:
    """The unhosted source the input mode needs, or None when it can resolve."""
    if type_detecte == "adresse":
        return None if scope.hosts("ban") else "ban"
    if type_detecte == "cadastre":
        return None if scope.hosts("cadastre") else "cadastre"
    if scope.hosts("ban") or scope.hosts("cadastre"):
        return None
    return "ban"


def _point_departement(base: ResultatResolution) -> str | None:
    """Territory of the resolved identity: its commune, else its postcode.

    The commune goes through ``territoire_couverture``: an overseas
    collectivity is its own territory, out of coverage with reason
    ``territoire_hors_perimetre`` (D-123). None when the code names no
    French territory: no proof either way.
    """
    if base.code_insee:
        return territoire_couverture(base.code_insee)
    dept = _departement_from_cp(base.code_postal)
    return dept if dept in DEPARTEMENTS else None


def _declared_out_of_coverage(
    dataset_list: list[str], scope: InstanceScope, dept: str | None
) -> dict[str, OutOfCoverageReason]:
    """Requested keys whose every source is out of coverage, with the reason.

    Decided from the persisted scope before any query or upstream call
    (D-24). Unknown keys are left to the multi-dataset validation.
    """
    declared: dict[str, OutOfCoverageReason] = {}
    for raw in dataset_list:
        name = _normalize_key(raw)
        if name not in RESOLVER_SOURCE_MAP:
            continue
        hosting = hosting_for_key(name, scope, dept)
        if hosting.motif is not None:
            declared[name] = hosting.motif
        elif name in DEPENDS_ON_BAN_ID and not scope.hosts("ban"):
            # Answered only through the BAN key of the address (IN-15): the
            # unhosted BAN is never read, so the key is out with its reason.
            declared[name] = OutOfCoverageReason.DATASET_NON_HEBERGE
    return declared


_IDENTITY_SOURCES = ("ban", "cadastre")


def _identity_hosting(scope: InstanceScope) -> dict[str, DatasetHosting] | None:
    """The identity sources the instance does not host, for meta.hebergement.

    A resolution without them never consulted them (IN-12): the response says
    so instead of leaving a bare absence. None while both are hosted.
    """
    out = {
        key: hosting_for(key, scope, None)
        for key in _IDENTITY_SOURCES
        if not scope.hosts(key)
    }
    return out or None


_PARCEL_MISMATCH_DATASETS = ("cadastre", "dvf")


def _enforce_parcel_invariant(
    base: ResultatResolution,
    multi: dict,
    *,
    type_bien: str | None = None,
) -> None:
    """Fail-closed cross-check: base == datasets.cadastre == dvf[*] (D-02).

    With canonical-id queries a divergence should be impossible; if a lower
    layer still answers with another parcel, the divergent block is scrubbed
    and replaced by an explicit typed error — wrong data is never served as
    this bien's. Logs carry only the dataset name (no identifier).

    ``type_bien`` is the DECLARED type already parsed by the caller (D-41): the
    replacement is still a decision, and it publishes the same provenance as the
    one it replaces. Keyword-only and defaulted, so every existing caller keeps
    its behaviour bit for bit.
    """
    donnees: dict = multi["donnees"]
    erreurs: list[DatasetErreur] = multi["erreurs"] or []
    states: dict[str, DatasetResolutionState] = multi.get("dataset_states", {})
    expected = base.id_parcelle

    for name in _PARCEL_MISMATCH_DATASETS:
        payload = donnees.get(name)
        if not payload:
            continue
        rows = payload if isinstance(payload, list) else [payload]
        ids = {r.get("id_parcelle") for r in rows if isinstance(r, dict)}
        if not ids or ids == {expected}:
            continue
        donnees.pop(name)
        erreurs.append(
            DatasetErreur(
                dataset=name,
                message=RESOLUTION_RAISONS["parcel_mismatch"],
            )
        )
        states[name] = DatasetResolutionState(
            dataset=name,
            presence="error",
            absence_reason="temporary_error",
            raison=RESOLUTION_RAISONS["temporary_error"],
        )
        if name == "dvf" and multi.get("dvf_attribution") is not None:
            # The decision was computed FROM the scrubbed block: it cannot
            # outlive it (T-57-45). Since Phase 57 the fiche reads its DVF KPI
            # on this object, so leaving it in place would serve as this
            # bien's the very sale the invariant just rejected.
            multi["dvf_attribution"] = DvfAttribution(
                status="error",
                reason="temporary_error",
                **provenance_type_bien(type_bien),
            )
        logger.error("resolve_parcel_invariant_violation", extra={"dataset": name})

    multi["erreurs"] = erreurs or None


async def _resolve_request(
    db: AsyncSession,
    scope: InstanceScope,
    q: str,
    type_force: str | None,
    datasets: str | None,
    correlation_id: str,
    type_bien: str | None = None,
) -> ReponseApi[list[ResultatResolution] | list[ResultatMultiResolution]]:
    """Run a resolve request while logging only allowlisted metadata.

    Telemetry contract: see :mod:`app.resolve.telemetry` — the raw input
    ``q`` never reaches a log record; only the opaque ``correlation_id`` is
    propagated to the multi-dataset layer. ``type_bien`` is DELIBERATELY absent
    from every event emitted below (T-57-75): it is an input of the caller, not
    a fact about the resolved identity.
    """
    started = time.perf_counter()
    if type_force is not None:
        type_detecte, parsed = _parse_forced_type(type_force, q)
    else:
        type_detecte, parsed = detect_input_type(q)
    type_bien_declare = _parse_type_bien(type_bien)

    validated_datasets = telemetry.sanitize_datasets(datasets, DATASETS_DISPONIBLES)
    telemetry.emit_resolve_event(
        logger,
        "resolve_request",
        correlation_id=correlation_id,
        input_type=type_detecte,
        datasets=validated_datasets,
        dataset_count=len(validated_datasets),
    )

    def _completed(outcome: ResolutionOutcome) -> None:
        telemetry.emit_resolve_event(
            logger,
            "resolve_completed",
            correlation_id=correlation_id,
            outcome=outcome.issue,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # Coverage gate (D-04): a provably out-of-scope input yields a typed
    # absence — never the best in-scope neighbour from a fuzzy lookup.
    if _provably_out_of_coverage(_dept_hint(type_detecte, parsed), scope):
        outcome = _absence_outcome(
            "out_of_coverage", OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE
        )
        meta = get_meta_source("ban")
        meta["resolution"] = outcome.model_dump()
        _completed(outcome)
        return ReponseApi(donnees=[], meta=meta)

    # Identity sources (D-23): an address needs the BAN, a cadastral
    # reference the cadastre, a point either; an unhosted one is never read.
    identity_source = _identity_source(type_detecte, scope)
    if identity_source is not None:
        outcome = _absence_outcome(
            "out_of_coverage", OutOfCoverageReason.DATASET_NON_HEBERGE
        )
        meta = get_meta_source(identity_source)
        meta["resolution"] = outcome.model_dump()
        meta["hebergement"] = _identity_hosting(scope)
        _completed(outcome)
        return ReponseApi(donnees=[], meta=meta)

    # Step 1: resolve input to the canonical base identity
    ban, cadastre = scope.hosts("ban"), scope.hosts("cadastre")
    if type_detecte == "adresse":
        resultats = await _resolve_adresse(
            db, parsed["q"], correlation_id, cadastre=cadastre
        )
    elif type_detecte == "gps":
        resultats = await _resolve_gps(
            db,
            parsed["lat"],
            parsed["lon"],
            correlation_id,
            ban=ban,
            cadastre=cadastre,
        )
    else:
        resultats = await _resolve_cadastre(db, parsed["ref"], ban=ban)

    outcome = _outcome_from_results(resultats)

    # Step 2: if datasets requested, enrich with multi-dataset query
    if datasets is not None and resultats:
        dataset_list = [d.strip().lower() for d in datasets.split(",") if d.strip()]
        base = resultats[0]
        # A reverse-geocoded GPS point and a cadastral centroid are display
        # context, not an address selection. Only address mode can establish
        # the exact BAN target required by the inventory.
        canonical_dpe_ban_id = base.ban_id if base.type_detecte == "adresse" else None

        multi = await query_multiple_datasets(
            async_session,
            dataset_list,
            base.latitude,
            base.longitude,
            canonical_parcelle=base.id_parcelle,
            # The shared DPE inventory consumes only this strictly selected
            # address key and resolves no second identity of its own.
            canonical_ban_id=canonical_dpe_ban_id,
            # D-41: the DECLARED dwelling type travels to the DVF decision and
            # is interpreted nowhere else on the way.
            type_bien=type_bien_declare,
            correlation_id=correlation_id,
            out_of_coverage=_declared_out_of_coverage(
                dataset_list, scope, _point_departement(base)
            ),
            scope=scope,
        )
        _enforce_parcel_invariant(base, multi, type_bien=type_bien_declare)

        # Expand [ban, *datasets] to canonical sources (ordered, deduped),
        # ignoring unknown aliases (already surfaced in `erreurs`).
        requested = [
            k
            for k in ["ban", *dataset_list]
            if _normalize_key(k) in RESOLVER_SOURCE_MAP
        ]
        source_keys = expand_source_keys(requested)

        now = freshness._now()
        # Provenance facts are additive enrichment: a metadata read failure
        # must degrade to honest `inconnu` states, never fail the resolution.
        try:
            facts_by_key = await get_provenance_facts(db, source_keys)
        except Exception:  # noqa: BLE001
            logger.warning("resolve_provenance_facts_unavailable")
            facts_by_key = {}
        provenances = _assemble_provenance_list(source_keys, facts_by_key, now)
        prov_by_source = {p.dataset: p for p in provenances}

        dataset_states: dict[str, DatasetResolutionState] = multi.get(
            "dataset_states", {}
        )
        _refine_dataset_states(
            dataset_states, prov_by_source, base.code_postal, scope=scope
        )

        enriched = ResultatMultiResolution(
            type_detecte=base.type_detecte,
            latitude=base.latitude,
            longitude=base.longitude,
            adresse=base.adresse,
            commune=base.commune,
            code_postal=base.code_postal,
            id_parcelle=base.id_parcelle,
            code_insee=base.code_insee,
            ban_id=base.ban_id,
            resolution=base.resolution,
            parcelles_candidates=base.parcelles_candidates,
            score=base.score,
            datasets=multi["donnees"],
            erreurs=multi["erreurs"],
            avertissements=multi.get("avertissements", []),
            dataset_states=dataset_states or None,
            # The explicit reconstruction is a trust boundary: keep the shared
            # inventory and its non-attributive V2 decision side by side.
            dpe_inventory=multi.get("dpe_inventory"),
            dpe_attribution=multi.get("dpe_attribution"),
            # D-16/D-37: same doctrine for DVF — one decision, computed once,
            # published beside the parcel history it qualifies.
            dvf_attribution=multi.get("dvf_attribution"),
        )
        _completed(outcome)
        return ReponseApi(
            donnees=[enriched],
            meta=MetaReponse(
                source=provenances,
                resolution=outcome,
                hebergement=_identity_hosting(scope),
            ).model_dump(),
        )

    meta = get_meta_source("ban")
    meta["resolution"] = outcome.model_dump()
    if hebergement := _identity_hosting(scope):
        meta["hebergement"] = hebergement
    _completed(outcome)
    return ReponseApi(donnees=resultats, meta=meta)


@router.get(
    "/resolve",
    response_model=ReponseApi[list[ResultatResolution] | list[ResultatMultiResolution]],
    summary="Resolution polymorphe multi-dataset",
    description=(
        "Point d'entree unifie : accepte une adresse, des coordonnees GPS ou "
        "une reference cadastrale. Detecte automatiquement le type d'entree et "
        "interroge les datasets demandes en parallele."
    ),
)
async def resolve(
    db: Annotated[AsyncSession, Depends(get_db)],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    response: Response,
    q: Annotated[
        str,
        Query(
            min_length=2,
            description=(
                "Entree polymorphe : adresse ('12 rue de la Paix Paris'), "
                "coordonnees GPS ('48.8566,2.3522'), ou "
                "reference cadastrale ('75101000AB0001'). "
                "Le type est auto-detecte sauf si force avec le parametre type"
            ),
            example="12 rue de la Paix Paris",
        ),
    ],
    type: Annotated[
        str | None,
        Query(
            description="Force le type de detection : adresse, gps, ou cadastre",
            example="adresse",
        ),
    ] = None,
    datasets: Annotated[
        str | None,
        Query(
            description=(
                "Datasets a interroger en parallele, separes par virgule. "
                "Valeurs possibles : dvf, dpe, risques, urbanisme, cadastre, "
                "loyers, batiments, monuments, transports, etc."
            ),
            example="dvf,dpe,risques",
        ),
    ] = None,
    type_bien: Annotated[
        str | None,
        Query(
            description=(
                "Type du bien consulté, DÉCLARÉ par l'appelant : maison ou "
                "appartement. Sert uniquement à qualifier la compatibilité des "
                "mutations DVF de la parcelle. Une déclaration n'est pas une "
                "preuve d'identité du logement : elle ne produit jamais une "
                "attribution exacte (D-41)."
            ),
            example="appartement",
        ),
    ] = None,
) -> ReponseApi[list[ResultatResolution] | list[ResultatMultiResolution]]:
    """Resolve any input without exposing it through application logs."""
    correlation_id = telemetry.new_correlation_id()
    dpe_requested = "dpe" in telemetry.sanitize_datasets(datasets, DATASETS_DISPONIBLES)
    if dpe_requested:
        response.headers["Cache-Control"] = "no-store"
    try:
        return await _resolve_request(
            db, scope, q, type, datasets, correlation_id, type_bien
        )
    except ErreurApiError as exc:
        if dpe_requested:
            exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
        raise
    except Exception as exc:  # noqa: BLE001
        # Bounded code only (D-12): an exception text may embed an upstream
        # URL, an address or coordinates — never log str(exc) or exc_info.
        telemetry.emit_resolve_event(
            logger,
            "resolve_failed",
            level=logging.ERROR,
            correlation_id=correlation_id,
            error_code=telemetry.error_code_for(exc),
        )
        raise ErreurApiError(
            500,
            "Erreur de resolution",
            "La resolution n'a pas pu aboutir.",
            headers={"Cache-Control": "no-store"} if dpe_requested else None,
        ) from None
