"""Transactional DPE address inventory and independent document consultation."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.dpe import queries, references
from app.dpe.lifecycle import (
    DATE_ISSUES,
    GRAPH_ISSUES,
    Validity,
    source_scope,
    validity_at,
)
from app.dpe.queries import (
    DpeInventoryAggregate,
    DpeInventoryRecord,
    DpeInventoryTarget,
    DpeRelationGenerations,
)
from app.dpe.references import (
    DpeContextReference,
    DpeCursorReference,
    InvalidReferenceError,
    RefreshRequiredError,
    issue_context,
    issue_cursor,
    issue_document,
    verify_context,
    verify_cursor,
    verify_document,
)
from app.dpe.schemas import (
    DpeConsultedDocument,
    DpeInventoryDistribution,
    DpeInventoryItem,
    DpeInventoryPage,
    DpeLifecycle,
)
from app.etl_dpe.reconciliation import _previous_observations, _timestamp

DPE_INVENTORY_DEFAULT_PAGE_SIZE = 20
DPE_INVENTORY_MAX_PAGE_SIZE = 50

_ENERGY_CLASSES = frozenset("ABCDEFG")
_EMPTY_DISTRIBUTION = {
    "A": 0,
    "B": 0,
    "C": 0,
    "D": 0,
    "E": 0,
    "F": 0,
    "G": 0,
    "unknown": 0,
}


@dataclass(frozen=True, slots=True)
class DpeInventoryServiceResult:
    """Internal aggregate kept separate from the closed public page."""

    page: DpeInventoryPage
    apartment_document_count: int | None


def _department_from_ban(ban_id: str) -> str:
    return ban_id[:3] if ban_id.startswith("97") else ban_id[:2]


def _validate_limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= DPE_INVENTORY_MAX_PAGE_SIZE:
        raise ValueError("invalid_page_size")
    return limit


def _energy_class(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in _ENERGY_CLASSES else None


def _number(value: Decimal | float | int | None) -> float | None:
    return None if value is None else float(value)


def assert_inventory_date_current(as_of_date: date | None) -> None:
    """Close the publication boundary again after any awaited work."""
    if as_of_date is not None and as_of_date != references.paris_today():
        raise RefreshRequiredError


def _minimize(record: DpeInventoryRecord) -> DpeInventoryRecord:
    """Purge even a contaminated tombstone before inspecting documentary values."""
    reconciled = getattr(record, "source_state", "unknown") in (
        "absent_from_complete_scope",
        "explicitly_deactivated",
    )
    if not getattr(record, "is_tombstone", False) and not reconciled:
        return record
    # Explicit private record construction also drops unexpected source fields.
    return DpeInventoryRecord(
        document_id=record.document_id,
        dpe_number=record.dpe_number,
        replaced_dpe_number=record.replaced_dpe_number,
        **{
            name: None
            for name in (
                "type_batiment",
                "surface_habitable",
                "etiquette_dpe",
                "etiquette_ges",
                "date_etablissement_dpe",
                "date_fin_validite_dpe",
                "annee_construction",
                "periode_construction",
                "type_installation_chauffage",
                "type_energie_principale_chauffage",
                "qualite_isolation_enveloppe",
                "conso_5_usages_ep",
                "emission_ges_5_usages",
                "cout_total_5_usages",
            )
        },
        regime_dpe=getattr(record, "regime_dpe", None),
        source_observations=getattr(record, "source_observations", []),
        source_state=getattr(record, "source_state", "unknown"),
        source_snapshot_at=getattr(record, "source_snapshot_at", None),
        is_tombstone=getattr(record, "is_tombstone", False),
        lifecycle_facts=getattr(record, "lifecycle_facts", {}),
        revision_evidence=getattr(record, "revision_evidence", {}),
    )


def _closed_issue_list(value: object, allowed: tuple[str, ...]) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise ValueError("invalid lifecycle issues")
    if value != [item for item in allowed if item in value]:
        raise ValueError("invalid lifecycle issues")
    return value


def _record_validity(record: DpeInventoryRecord, as_of_date: date) -> Validity:
    facts = getattr(record, "lifecycle_facts", {})
    if not isinstance(facts, dict):
        return "unknown"
    try:
        issues = _closed_issue_list(facts.get("date_issues", []), DATE_ISSUES)
    except (TypeError, ValueError):
        return "unknown"
    return validity_at(
        record.date_etablissement_dpe,
        record.date_fin_validite_dpe,
        as_of_date,
        date_issues=issues,
    )


def _lifecycle_source(record: DpeInventoryRecord) -> dict:
    observations = _previous_observations(
        {"source_observations": getattr(record, "source_observations", [])}
    )
    if any(type(entry[key]) is not str for entry in observations for key in entry):
        raise ValueError("invalid source observations")
    if any(not entry["import_id"].strip() for entry in observations):
        raise ValueError("invalid source observations")
    _assert_source_coherent(record, observations)
    regimes = {"dpe03existant": "existant", "dpe02neuf": "neuf"}
    known = {regimes[entry["source_slug"]] for entry in observations}
    legacy = getattr(record, "regime_dpe", None)
    if not observations and legacy in ("existant", "neuf"):
        known.add(legacy)
    snapshot = getattr(record, "source_snapshot_at", None)
    return {
        "source_state": getattr(record, "source_state", "unknown"),
        "source_snapshot_date": None
        if snapshot is None
        else _timestamp(snapshot).astimezone(ZoneInfo("Europe/Paris")).date(),
        "source_scope": source_scope(
            observations, getattr(record, "source_state", "unknown")
        ),
        "regimes": [regime for regime in ("existant", "neuf") if regime in known],
    }


def _assert_source_coherent(
    record: DpeInventoryRecord, observations: list[dict]
) -> None:
    """Mirror migration 091's source coherence without reading another relation."""
    state = getattr(record, "source_state", "unknown")
    snapshot = getattr(record, "source_snapshot_at", None)
    if state == "unknown":
        if observations or snapshot is not None:
            raise ValueError("inconsistent unknown source")
        return
    if state not in ("present", "absent_from_complete_scope") or snapshot is None:
        raise ValueError("unsupported source state")
    snapshot = _timestamp(snapshot)
    if any(_timestamp(entry["observed_at"]) > snapshot for entry in observations):
        raise ValueError("inconsistent source snapshot")
    if (state == "present") != any(
        entry["state"] == "present" for entry in observations
    ):
        raise ValueError("inconsistent source presence")


def _lifecycle_graph(record: DpeInventoryRecord) -> dict:
    facts = getattr(record, "lifecycle_facts", {})
    if not isinstance(facts, dict) or set(facts) != {
        "replacement_state",
        "graph_issues",
        "date_issues",
    }:
        raise ValueError("invalid lifecycle facts")
    graph_issues = _closed_issue_list(facts["graph_issues"], GRAPH_ISSUES)
    date_issues = _closed_issue_list(facts["date_issues"], DATE_ISSUES)
    if (
        "invalid_establishment_date" in date_issues
        and record.date_etablissement_dpe is not None
    ) or (
        "invalid_end_date" in date_issues and record.date_fin_validite_dpe is not None
    ):
        raise ValueError("inconsistent private date facts")
    return {
        "replacement_state": facts["replacement_state"],
        "graph_issues": graph_issues,
    }


def _lifecycle_availability(record: DpeInventoryRecord) -> str:
    evidence = getattr(record, "revision_evidence", None)
    if evidence != {
        "status": "unverified",
        "kind": None,
        "revision_date": None,
        "verified_at": None,
        "evidence_sha256": None,
        "official_source": None,
    }:
        raise ValueError("unsupported revision evidence")
    tombstone = getattr(record, "is_tombstone", False)
    reconciled = getattr(record, "source_state", "unknown") in (
        "absent_from_complete_scope",
        "explicitly_deactivated",
    )
    if type(tombstone) is not bool or tombstone != reconciled:
        raise ValueError("inconsistent source availability")
    return "source_reconciled" if tombstone else "available"


def _lifecycle(record: DpeInventoryRecord, as_of_date: date) -> DpeLifecycle | None:
    """Validate each private dimension; publish only the complete closed group."""
    try:
        source = _lifecycle_source(record)
        graph = _lifecycle_graph(record)
        availability = _lifecycle_availability(record)
        if (
            source["source_state"] == "unknown"
            and source["source_snapshot_date"] is None
            and graph["replacement_state"] == "not_checked"
        ):
            return None
        return DpeLifecycle.model_validate(
            {
                **source,
                **graph,
                "validity": "unknown"
                if availability == "source_reconciled"
                else _record_validity(record, as_of_date),
                "as_of_date": as_of_date,
                "revision_status": "unverified",
                "revision_kind": None,
                "revision_date": None,
                "detail_availability": availability,
            }
        )
    except (ValueError, TypeError, OverflowError, ValidationError):
        # Never log or reflect a private malformed object (including its keys).
        return None


def _assert_context_current(
    context: DpeContextReference,
    target: DpeInventoryTarget | None,
    generations: DpeRelationGenerations,
) -> DpeInventoryTarget:
    if target is None:
        raise RefreshRequiredError
    context.assert_current(
        address_id=target.address_id,
        department=target.department,
        ban_id=target.ban_id,
        addresses_generation=generations.addresses,
        diagnostics_generation=generations.diagnostics,
    )
    return target


def _item(record: DpeInventoryRecord, context: DpeContextReference) -> DpeInventoryItem:
    record = _minimize(record)
    return DpeInventoryItem(
        document_ref=issue_document(
            context,
            document_id=record.document_id,
            dpe_number=record.dpe_number,
        ),
        type_batiment=record.type_batiment,
        surface_habitable=_number(record.surface_habitable),
        etiquette_dpe=_energy_class(record.etiquette_dpe),
        etiquette_ges=_energy_class(record.etiquette_ges),
        date_etablissement_dpe=record.date_etablissement_dpe,
        date_fin_validite_dpe=record.date_fin_validite_dpe,
        lifecycle=_lifecycle(record, context.as_of_date),
    )


def _distribution(aggregate: DpeInventoryAggregate) -> DpeInventoryDistribution:
    return DpeInventoryDistribution.model_validate(aggregate.distribution)


def _identity_unavailable_page(page_size: int, as_of_date: date) -> DpeInventoryPage:
    return DpeInventoryPage(
        state="identity_unavailable",
        as_of_date=as_of_date,
        total=None,
        distribution=DpeInventoryDistribution.model_validate(_EMPTY_DISTRIBUTION),
        page_size=page_size,
        has_more=False,
        read_state="complete",
    )


async def _start_read_snapshot(session: AsyncSession) -> DpeRelationGenerations:
    """Set isolation before any read, then lock and identify both parents."""
    await session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    )
    from app.common.publication_readiness import guard_snapshot_publication

    await guard_snapshot_publication(session, ["dpe"])
    return await queries.lock_dpe_inventory_relations(session)


async def _target_from_context(
    session: AsyncSession,
    context: DpeContextReference,
    generations: DpeRelationGenerations,
) -> DpeInventoryTarget:
    target = await queries.dpe_inventory_target_by_id(
        session,
        address_id=context.address_id,
        department=context.department,
    )
    return _assert_context_current(context, target, generations)


async def _initial_target(
    session: AsyncSession,
    *,
    ban_id: str,
) -> DpeInventoryTarget | None:
    return await queries.dpe_inventory_target_by_ban(
        session,
        ban_id=ban_id,
        department=_department_from_ban(ban_id),
    )


def _new_context(
    target: DpeInventoryTarget,
    generations: DpeRelationGenerations,
    as_of_date: date,
) -> tuple[str, DpeContextReference]:
    reference = issue_context(
        address_id=target.address_id,
        department=target.department,
        ban_id=target.ban_id,
        addresses_generation=generations.addresses,
        diagnostics_generation=generations.diagnostics,
        as_of_date=as_of_date,
    )
    return reference, verify_context(reference, as_of_date=as_of_date)


async def _anchor_from_cursor(
    session: AsyncSession,
    *,
    cursor: DpeCursorReference | None,
    target: DpeInventoryTarget,
) -> DpeInventoryRecord | None:
    if cursor is None:
        return None
    anchor = await queries.dpe_inventory_record_by_id(
        session,
        document_id=cursor.anchor_id,
        ban_id=target.ban_id,
        department=target.department,
    )
    if anchor is None:
        raise RefreshRequiredError
    cursor.assert_current(
        anchor_id=anchor.document_id,
        dpe_number=anchor.dpe_number,
    )
    return anchor


async def load_dpe_inventory_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ban_id: str | None = None,
    context_ref: str | None = None,
    cursor: str | None = None,
    limit: int = DPE_INVENTORY_DEFAULT_PAGE_SIZE,
) -> DpeInventoryServiceResult:
    """Load a public page and its non-public document aggregate."""
    as_of_date = references.paris_today()
    page_size = _validate_limit(limit)
    if ban_id is not None and context_ref is not None:
        raise InvalidReferenceError
    if context_ref is None and cursor is not None:
        raise InvalidReferenceError
    if context_ref is None and not ban_id:
        assert_inventory_date_current(as_of_date)
        return DpeInventoryServiceResult(
            page=_identity_unavailable_page(page_size, as_of_date),
            apartment_document_count=None,
        )

    context = (
        verify_context(context_ref, as_of_date=as_of_date)
        if context_ref is not None
        else None
    )
    decoded_cursor = (
        verify_cursor(cursor, context=context, as_of_date=as_of_date)
        if cursor is not None and context is not None
        else None
    )

    try:
        async with session_factory() as session, session.begin():
            generations = await _start_read_snapshot(session)
            if context is None:
                target = await _initial_target(session, ban_id=ban_id or "")
                if target is None:
                    return DpeInventoryServiceResult(
                        page=_identity_unavailable_page(page_size, as_of_date),
                        apartment_document_count=None,
                    )
                returned_context_ref, context = _new_context(
                    target, generations, as_of_date
                )
            else:
                target = await _target_from_context(session, context, generations)
                returned_context_ref = context_ref or ""

            anchor = await _anchor_from_cursor(
                session,
                cursor=decoded_cursor,
                target=target,
            )
            records, has_more = await queries.dpe_inventory_page(
                session,
                ban_id=target.ban_id,
                department=target.department,
                limit=page_size,
                anchor=anchor,
            )
            aggregate = await queries.dpe_inventory_aggregate(
                session,
                ban_id=target.ban_id,
                department=target.department,
            )

            items = [_item(record, context) for record in records]
            next_cursor = None
            if has_more:
                last = records[-1]
                next_cursor = issue_cursor(
                    context,
                    anchor_id=last.document_id,
                    dpe_number=last.dpe_number,
                )
            state: Literal["ready", "absent"] = "ready" if aggregate.total else "absent"
            page = DpeInventoryPage(
                state=state,
                as_of_date=as_of_date,
                context_ref=returned_context_ref,
                items=items,
                total=aggregate.total,
                distribution=_distribution(aggregate),
                page_size=page_size,
                has_more=has_more,
                next_cursor=next_cursor,
                read_state="complete",
            )
    finally:
        assert_inventory_date_current(as_of_date)
    return DpeInventoryServiceResult(
        page=page,
        apartment_document_count=aggregate.apartment_document_count,
    )


async def load_dpe_inventory(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ban_id: str | None = None,
    context_ref: str | None = None,
    cursor: str | None = None,
    limit: int = DPE_INVENTORY_DEFAULT_PAGE_SIZE,
) -> DpeInventoryPage:
    """Load one closed public page without attributing a dwelling."""
    result = await load_dpe_inventory_result(
        session_factory,
        ban_id=ban_id,
        context_ref=context_ref,
        cursor=cursor,
        limit=limit,
    )
    return result.page


async def load_dpe_document(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    context_ref: str,
    document_ref: str,
) -> DpeConsultedDocument:
    """Load one allowlisted document tied to a current inventory context."""
    as_of_date = references.paris_today()
    context = verify_context(context_ref, as_of_date=as_of_date)
    document = verify_document(document_ref, context=context, as_of_date=as_of_date)

    async with session_factory() as session, session.begin():
        generations = await _start_read_snapshot(session)
        target = await _target_from_context(session, context, generations)
        record = await queries.dpe_inventory_record_by_id(
            session,
            document_id=document.document_id,
            ban_id=target.ban_id,
            department=target.department,
        )
        if record is None:
            raise RefreshRequiredError
        document.assert_current(
            document_id=record.document_id,
            dpe_number=record.dpe_number,
            as_of_date=as_of_date,
        )
        record = _minimize(record)
        lifecycle = _lifecycle(record, as_of_date)
        validity = (
            lifecycle.validity
            if lifecycle is not None
            else _record_validity(record, as_of_date)
        )
        projected = DpeConsultedDocument(
            context_ref=context_ref,
            document_ref=document_ref,
            type_batiment=record.type_batiment,
            surface_habitable=_number(record.surface_habitable),
            etiquette_dpe=_energy_class(record.etiquette_dpe),
            etiquette_ges=_energy_class(record.etiquette_ges),
            date_etablissement_dpe=record.date_etablissement_dpe,
            date_fin_validite_dpe=record.date_fin_validite_dpe,
            annee_construction=record.annee_construction,
            periode_construction=record.periode_construction,
            type_installation_chauffage=record.type_installation_chauffage,
            type_energie_principale_chauffage=(
                record.type_energie_principale_chauffage
            ),
            qualite_isolation_enveloppe=record.qualite_isolation_enveloppe,
            conso_5_usages_ep=_number(record.conso_5_usages_ep),
            emission_ges_5_usages=_number(record.emission_ges_5_usages),
            cout_total_5_usages=_number(record.cout_total_5_usages),
            validity_by_date=validity
            if validity in ("expired", "not_expired_by_date")
            else "unknown",
            lifecycle=lifecycle,
        )
    assert_inventory_date_current(as_of_date)
    return projected


__all__ = [
    "DPE_INVENTORY_DEFAULT_PAGE_SIZE",
    "DPE_INVENTORY_MAX_PAGE_SIZE",
    "DpeInventoryServiceResult",
    "load_dpe_document",
    "load_dpe_inventory",
    "load_dpe_inventory_result",
]
