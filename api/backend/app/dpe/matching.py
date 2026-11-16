"""Pure factual comparison policy for the public DPE inventory."""

import math

from app.dpe.matching_schemas import (
    DpeDocumentComparison,
    DpeMatchResult,
    DpeMatchTarget,
    DpeSurfaceComparison,
    DpeTypeComparison,
)
from app.dpe.schemas import DpeAttribution, DpeInventoryItem, DpeInventoryPage


def _type_comparison(
    item: DpeInventoryItem, target: DpeMatchTarget
) -> DpeTypeComparison:
    raw_source = item.type_batiment
    normalized_source = raw_source.strip().casefold() if raw_source else None
    if normalized_source in {"maison", "appartement"}:
        source = normalized_source
    elif normalized_source == "immeuble":
        source = "autre"
    else:
        source = None
    if target.type_bien is None:
        return DpeTypeComparison(state="not_applicable", source=source)
    if source is None:
        return DpeTypeComparison(state="unknown", target=target.type_bien)
    state = "matching" if source == target.type_bien else "conflicting"
    return DpeTypeComparison(
        state=state,
        target=target.type_bien,
        source=source,
    )


def _valid_surface(value: float | None) -> float | None:
    if (
        value is None
        or isinstance(value, bool)
        or value <= 0
        or not math.isfinite(value)
    ):
        return None
    return value


def _surface_comparison(
    item: DpeInventoryItem, target: DpeMatchTarget
) -> DpeSurfaceComparison:
    source = _valid_surface(item.surface_habitable)
    if target.surface is None:
        return DpeSurfaceComparison(state="not_applicable", source=source)
    if target.surface.nature != "habitable_dpe":
        return DpeSurfaceComparison(
            state="not_comparable",
            target=target.surface.value,
            source=source,
            target_nature=target.surface.nature,
        )
    if source is None:
        return DpeSurfaceComparison(
            state="unknown",
            target=target.surface.value,
            target_nature=target.surface.nature,
        )
    delta = source - target.surface.value
    return DpeSurfaceComparison(
        state="matching" if delta == 0 else "known",
        target=target.surface.value,
        source=source,
        target_nature=target.surface.nature,
        delta=delta,
    )


def _blockers(page: DpeInventoryPage, item: DpeInventoryItem) -> list[str]:
    blockers: list[str] = []
    if page.address_check == "unverified":
        blockers.append("address_unverified")
    if page.source_coverage == "unknown":
        blockers.append("source_coverage_unknown")
    lifecycle = item.lifecycle
    if lifecycle is None:
        blockers.extend(
            ["source_state_unknown", "validity_unknown", "replacement_unchecked"]
        )
    else:
        source = {
            "unknown": "source_state_unknown",
            "absent_from_complete_scope": "source_absent",
            "explicitly_deactivated": "source_deactivated",
        }.get(lifecycle.source_state)
        validity = {
            "unknown": "validity_unknown",
            "expired": "validity_expired",
            "inconsistent_dates": "validity_inconsistent",
            "future_establishment": "validity_future",
        }.get(lifecycle.validity)
        replacement = {
            "not_checked": "replacement_unchecked",
            "replaced": "replacement_replaced",
            "ambiguous": "replacement_ambiguous",
        }.get(lifecycle.replacement_state)
        blockers.extend(value for value in (source, validity, replacement) if value)
    if page.graph_state == "incoherent" or (
        lifecycle is not None and lifecycle.graph_issues
    ):
        blockers.append("graph_incoherent")
    if page.read_state == "partial":
        blockers.append("inventory_partial")
    return blockers


def _attribution(page: DpeInventoryPage) -> DpeAttribution:
    if page.state == "identity_unavailable":
        return DpeAttribution(status="absent", reason="identity_unavailable")
    if page.state == "unavailable":
        return DpeAttribution(status="error", reason="temporary_error")
    if page.state == "absent":
        return DpeAttribution(status="absent", reason="not_reported")
    return DpeAttribution(
        status="unassigned",
        reason="dwelling_identity_unproven",
        graph_state=page.graph_state,
    )


def _limits(page: DpeInventoryPage, target: DpeMatchTarget) -> list[str]:
    limits = ["dwelling_identity_unproven"]
    if page.address_check == "unverified":
        limits.append("address_association_unverified")
    if page.source_coverage == "unknown":
        limits.append("source_coverage_unknown")
    if page.graph_state == "incoherent":
        limits.append("graph_incoherent")
    if page.read_state == "partial":
        limits.append("inventory_partial")
    if page.has_more or (page.total is not None and page.total > len(page.items)):
        limits.append("candidates_outside_page")
    if page.total is None:
        limits.append("candidate_count_unknown")
    if target.surface is not None:
        limits.append("surface_has_no_calibrated_threshold")
    return limits


def compare_inventory(page: DpeInventoryPage, target: DpeMatchTarget) -> DpeMatchResult:
    """Compare every public item without ordering, filtering or attribution."""
    comparisons = [
        DpeDocumentComparison(
            document_ref=item.document_ref,
            criteria=[
                _type_comparison(item, target),
                _surface_comparison(item, target),
            ],
            blockers=_blockers(page, item),
        )
        for item in page.items
    ]
    return DpeMatchResult(
        inventory=page,
        attribution=_attribution(page),
        comparisons=comparisons,
        limits=_limits(page, target),
    )
