"""Behavioral contract for factual, non-attributive DPE matching."""

import math
from datetime import date

import pytest
from pydantic import ValidationError

from app.dpe.matching import compare_inventory
from app.dpe.matching_schemas import (
    DpeMatchResult,
    DpeMatchSurface,
    DpeMatchTarget,
    DpeSurfaceComparison,
)
from app.dpe.schemas import (
    DpeAttribution,
    DpeInventoryDistribution,
    DpeInventoryItem,
    DpeInventoryPage,
    DpeLifecycle,
)


def _page(
    *items: DpeInventoryItem,
    state="ready",
    total=None,
    read_state="complete",
    graph_state="not_checked",
    has_more=False,
    as_of_date=None,
):
    if total is None and state == "absent":
        total = 0
    confirmed = total if total is not None else len(items)
    return DpeInventoryPage(
        state=state,
        context_ref="context-opaque" if state != "identity_unavailable" else None,
        items=list(items),
        total=total,
        distribution=DpeInventoryDistribution(unknown=confirmed),
        page_size=20,
        has_more=has_more,
        next_cursor="next-opaque" if has_more else None,
        read_state=read_state,
        graph_state=graph_state,
        as_of_date=as_of_date,
    )


@pytest.mark.parametrize(
    "value",
    [True, False, "42", 0, -1, math.nan, math.inf, -math.inf],
)
def test_match_surface_rejects_non_strict_positive_finite_numbers(value):
    with pytest.raises(ValidationError):
        DpeMatchSurface(value=value, nature="habitable_dpe")


def test_match_target_accepts_empty_or_complete_public_criteria_only():
    assert DpeMatchTarget() == DpeMatchTarget(type_bien=None, surface=None)
    target = DpeMatchTarget(
        type_bien="maison",
        surface={"value": 84.5, "nature": "habitable_dpe"},
    )
    assert target.surface is not None
    assert target.surface.value == 84.5

    for payload in (
        {"surface": 84.5},
        {"surface": {"value": 84.5}},
        {"type_bien": "immeuble"},
        {"etage": 2},
        {"porte": "A"},
        {"batiment": "B"},
        {"numero_ademe": "private"},
        {"adresse_brute": "private"},
    ):
        with pytest.raises(ValidationError):
            DpeMatchTarget.model_validate(payload)


def test_match_contracts_are_closed_and_hide_sensitive_rejected_values():
    sentinel = "PRIVATE-SENSITIVE-SENTINEL"
    with pytest.raises(ValidationError) as error:
        DpeMatchTarget.model_validate({"adresse_brute": sentinel})
    assert sentinel not in str(error.value)

    with pytest.raises(ValidationError) as error:
        DpeMatchSurface.model_validate(
            {"value": 42.0, "nature": "habitable_dpe", "porte": sentinel}
        )
    assert sentinel not in str(error.value)


def test_compare_inventory_preserves_rows_and_explains_missing_criteria():
    page = _page(
        DpeInventoryItem(document_ref="doc-a"),
        DpeInventoryItem(document_ref="doc-b", type_batiment="appartement"),
        total=3,
        read_state="partial",
        has_more=True,
    )

    result = compare_inventory(page, DpeMatchTarget())

    assert result.inventory is page
    assert [item.document_ref for item in result.inventory.items] == ["doc-a", "doc-b"]
    assert [item.document_ref for item in result.comparisons] == ["doc-a", "doc-b"]
    assert all(
        criterion.state == "not_applicable"
        for comparison in result.comparisons
        for criterion in comparison.criteria
    )
    assert result.attribution.status == "unassigned"
    assert result.attribution.diagnostic is None
    assert result.attribution.candidates == []
    assert "inventory_partial" in result.limits
    assert "candidates_outside_page" in result.limits


def test_type_absence_is_unknown_and_opposite_type_is_explicitly_conflicting():
    page = _page(
        DpeInventoryItem(document_ref="unknown"),
        DpeInventoryItem(document_ref="same", type_batiment="maison"),
        DpeInventoryItem(document_ref="opposite", type_batiment="appartement"),
        DpeInventoryItem(document_ref="mixed", type_batiment="immeuble"),
        total=4,
    )

    result = compare_inventory(page, DpeMatchTarget(type_bien="maison"))
    states = {
        comparison.document_ref: comparison.criteria[0].state
        for comparison in result.comparisons
    }

    assert states == {
        "unknown": "unknown",
        "same": "matching",
        "opposite": "conflicting",
        "mixed": "conflicting",
    }
    assert result.attribution.status == "unassigned"


def test_type_normalization_never_turns_missing_or_free_text_into_a_fact():
    page = _page(
        DpeInventoryItem(document_ref="missing", type_batiment=None),
        DpeInventoryItem(document_ref="blank", type_batiment="  "),
        DpeInventoryItem(document_ref="free", type_batiment="maison individuelle"),
        DpeInventoryItem(document_ref="normalized", type_batiment=" Maison "),
        total=4,
    )

    result = compare_inventory(page, DpeMatchTarget(type_bien="maison"))
    states = {
        comparison.document_ref: comparison.criteria[0].state
        for comparison in result.comparisons
    }
    assert states == {
        "missing": "unknown",
        "blank": "unknown",
        "free": "unknown",
        "normalized": "matching",
    }

    untargeted = compare_inventory(page, DpeMatchTarget())
    assert untargeted.comparisons[0].criteria[0].source is None
    assert untargeted.comparisons[1].criteria[0].source is None


@pytest.mark.parametrize("nature", ["carrez", "annonce", "dvf", "autre"])
def test_different_surface_natures_are_not_comparable(nature):
    page = _page(DpeInventoryItem(document_ref="doc", surface_habitable=42.0), total=1)

    result = compare_inventory(
        page,
        DpeMatchTarget(surface={"value": 42.0, "nature": nature}),
    )
    surface = result.comparisons[0].criteria[1]

    assert surface.state == "not_comparable"
    assert surface.delta is None
    assert result.attribution.status == "unassigned"


def test_comparable_surface_has_signed_delta_without_threshold_or_exclusion():
    page = _page(
        DpeInventoryItem(document_ref="lower", surface_habitable=40.0),
        DpeInventoryItem(document_ref="equal", surface_habitable=42.0),
        DpeInventoryItem(document_ref="missing", surface_habitable=None),
        total=3,
    )

    result = compare_inventory(
        page,
        DpeMatchTarget(surface={"value": 42.0, "nature": "habitable_dpe"}),
    )
    surfaces = {
        comparison.document_ref: comparison.criteria[1]
        for comparison in result.comparisons
    }

    assert (surfaces["lower"].state, surfaces["lower"].delta) == ("known", -2.0)
    assert (surfaces["equal"].state, surfaces["equal"].delta) == ("matching", 0.0)
    assert (surfaces["missing"].state, surfaces["missing"].delta) == (
        "unknown",
        None,
    )
    assert "surface_has_no_calibrated_threshold" in result.limits
    assert result.attribution.status == "unassigned"


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_reason"),
    [
        ("absent", "absent", "not_reported"),
        ("identity_unavailable", "absent", "identity_unavailable"),
        ("unavailable", "error", "temporary_error"),
    ],
)
def test_inventory_state_controls_non_attributive_fallback(
    state, expected_status, expected_reason
):
    page = _page(state=state, total=0 if state == "absent" else None)

    result = compare_inventory(page, DpeMatchTarget(type_bien="maison"))

    assert result.comparisons == []
    assert result.attribution.status == expected_status
    assert result.attribution.reason == expected_reason
    assert result.attribution.diagnostic is None


def test_graph_and_provenance_blockers_precede_candidate_comparison():
    page = _page(
        DpeInventoryItem(document_ref="doc", type_batiment="maison"),
        total=1,
        graph_state="incoherent",
    )

    result = compare_inventory(page, DpeMatchTarget(type_bien="maison"))

    assert result.attribution.status == "unassigned"
    assert result.attribution.graph_state == "incoherent"
    assert result.comparisons[0].blockers == [
        "address_unverified",
        "source_coverage_unknown",
        "source_state_unknown",
        "validity_unknown",
        "replacement_unchecked",
        "graph_incoherent",
    ]
    assert "graph_incoherent" in result.limits


def test_lifecycle_absence_replacement_and_validity_are_blockers_not_filters():
    lifecycle = DpeLifecycle(
        source_state="explicitly_deactivated",
        source_snapshot_date=date(2026, 9, 1),
        source_scope="44/85",
        regimes=[],
        replacement_state="replaced",
        graph_issues=[],
        validity="unknown",
        as_of_date=date(2026, 9, 9),
        revision_status="unverified",
        revision_kind=None,
        revision_date=None,
        detail_availability="source_reconciled",
    )
    page = _page(
        DpeInventoryItem(document_ref="kept", lifecycle=lifecycle),
        total=1,
        as_of_date=date(2026, 9, 9),
    )

    result = compare_inventory(page, DpeMatchTarget(type_bien="maison"))

    assert [comparison.document_ref for comparison in result.comparisons] == ["kept"]
    assert result.comparisons[0].criteria[0].state == "unknown"
    assert result.comparisons[0].blockers == [
        "address_unverified",
        "source_coverage_unknown",
        "source_deactivated",
        "validity_unknown",
        "replacement_replaced",
    ]
    assert result.attribution.status == "unassigned"


@pytest.mark.parametrize("issue", ["missing_target", "fork", "cycle", "truncated"])
def test_item_graph_issue_blocks_reasoning_even_when_page_graph_is_not_checked(issue):
    lifecycle = DpeLifecycle(
        source_state="present",
        source_snapshot_date=date(2026, 9, 1),
        source_scope="44/85",
        regimes=[],
        replacement_state="no_successor_known",
        graph_issues=[issue],
        validity="unknown",
        as_of_date=date(2026, 9, 9),
        revision_status="unverified",
        revision_kind=None,
        revision_date=None,
        detail_availability="available",
    )
    page = _page(
        DpeInventoryItem(document_ref="kept", lifecycle=lifecycle),
        total=1,
        as_of_date=date(2026, 9, 9),
    )

    result = compare_inventory(page, DpeMatchTarget())

    assert "graph_incoherent" in result.comparisons[0].blockers
    assert result.attribution.status == "unassigned"


def test_unknown_global_candidate_count_never_becomes_unique_evidence():
    page = _page(DpeInventoryItem(document_ref="only", type_batiment="maison"))

    result = compare_inventory(page, DpeMatchTarget(type_bien="maison"))

    assert "candidate_count_unknown" in result.limits
    assert result.attribution.status not in {"exact", "multiple", "building"}
    assert result.attribution.diagnostic is None


def test_unique_equal_type_and_surface_never_prove_dwelling_identity():
    page = _page(
        DpeInventoryItem(
            document_ref="only",
            type_batiment="maison",
            surface_habitable=84.5,
        ),
        total=1,
    )

    result = compare_inventory(
        page,
        DpeMatchTarget(
            type_bien="maison",
            surface={"value": 84.5, "nature": "habitable_dpe"},
        ),
    )

    assert result.rule_version == "dpe-factual-comparison-v1"
    assert [criterion.state for criterion in result.comparisons[0].criteria] == [
        "matching",
        "matching",
    ]
    assert result.attribution.status == "unassigned"
    assert result.attribution.reason == "dwelling_identity_unproven"
    assert result.attribution.diagnostic is None


def test_divided_house_with_two_equal_apartment_documents_keeps_both_unknown():
    page = _page(
        DpeInventoryItem(
            document_ref="division-a",
            type_batiment="appartement",
            surface_habitable=50.0,
        ),
        DpeInventoryItem(
            document_ref="division-b",
            type_batiment="appartement",
            surface_habitable=50.0,
        ),
        total=2,
    )

    result = compare_inventory(
        page,
        DpeMatchTarget(
            type_bien="maison",
            surface={"value": 50.0, "nature": "habitable_dpe"},
        ),
    )

    assert [comparison.document_ref for comparison in result.comparisons] == [
        "division-a",
        "division-b",
    ]
    assert [
        [criterion.state for criterion in comparison.criteria]
        for comparison in result.comparisons
    ] == [["conflicting", "matching"], ["conflicting", "matching"]]
    assert result.attribution.status == "unassigned"


def test_mixed_building_inventory_preserves_known_conflicting_and_unknown_types():
    page = _page(
        DpeInventoryItem(document_ref="house", type_batiment="maison"),
        DpeInventoryItem(document_ref="flat", type_batiment="appartement"),
        DpeInventoryItem(document_ref="building", type_batiment="immeuble"),
        DpeInventoryItem(document_ref="unspecified", type_batiment=None),
        total=4,
    )

    result = compare_inventory(page, DpeMatchTarget(type_bien="appartement"))

    assert [comparison.document_ref for comparison in result.comparisons] == [
        "house",
        "flat",
        "building",
        "unspecified",
    ]
    assert [comparison.criteria[0].state for comparison in result.comparisons] == [
        "conflicting",
        "matching",
        "conflicting",
        "unknown",
    ]
    assert result.attribution.status == "unassigned"


def test_document_reference_cannot_be_selected_even_when_it_names_a_candidate():
    page = _page(
        DpeInventoryItem(document_ref="selected", type_batiment="appartement"),
        DpeInventoryItem(document_ref="other", type_batiment="maison"),
        total=2,
    )
    with pytest.raises(ValidationError):
        DpeMatchTarget.model_validate(
            {"type_bien": "maison", "document_ref": "selected"}
        )

    result = compare_inventory(page, DpeMatchTarget(type_bien="maison"))

    assert result.comparisons[0].document_ref == "selected"
    assert result.comparisons[0].criteria[0].state == "conflicting"
    assert result.attribution.status == "unassigned"


@pytest.mark.parametrize("source", [None, 0.0, -1.0, math.nan, math.inf, -math.inf])
def test_missing_and_invalid_source_surfaces_remain_unknown(source):
    page = _page(
        DpeInventoryItem(document_ref="document", surface_habitable=source), total=1
    )

    result = compare_inventory(
        page,
        DpeMatchTarget(surface={"value": 42.0, "nature": "habitable_dpe"}),
    )

    surface = result.comparisons[0].criteria[1]
    assert surface.state == "unknown"
    assert surface.source is None
    assert surface.delta is None
    assert result.attribution.status == "unassigned"


def test_result_lists_every_applicable_global_limit_once_in_stable_order():
    page = _page(
        DpeInventoryItem(document_ref="visible", surface_habitable=40.0),
        total=2,
        read_state="partial",
        graph_state="incoherent",
        has_more=True,
    )

    result = compare_inventory(
        page,
        DpeMatchTarget(surface={"value": 42.0, "nature": "habitable_dpe"}),
    )

    assert result.limits == [
        "dwelling_identity_unproven",
        "address_association_unverified",
        "source_coverage_unknown",
        "graph_incoherent",
        "inventory_partial",
        "candidates_outside_page",
        "surface_has_no_calibrated_threshold",
    ]
    assert len(result.limits) == len(set(result.limits))


def test_surface_comparison_rejects_forged_delta_and_incomplete_values():
    common = {
        "state": "known",
        "target": 42.0,
        "source": 40.0,
        "target_nature": "habitable_dpe",
    }
    assert DpeSurfaceComparison(**common, delta=-2.0).delta == -2.0

    for changes in (
        {"delta": 2.0},
        {"delta": None},
        {"source": None, "delta": -2.0},
        {"target_nature": "carrez", "delta": -2.0},
        {"state": "not_comparable", "delta": -2.0},
    ):
        with pytest.raises(ValidationError):
            DpeSurfaceComparison(**{**common, **changes})


def test_result_rejects_promoted_attribution_and_comparison_reordering():
    page = _page(
        DpeInventoryItem(document_ref="first"),
        DpeInventoryItem(document_ref="second"),
        total=2,
    )
    valid = compare_inventory(page, DpeMatchTarget())
    promoted = DpeAttribution.model_construct(
        version=2,
        status="exact",
        reason=None,
        diagnostic=object(),
        candidates=[],
        building_context=None,
        graph_state="not_checked",
    )

    with pytest.raises(ValidationError):
        DpeMatchResult(
            inventory=page,
            attribution=promoted,
            comparisons=valid.comparisons,
            limits=valid.limits,
        )
    for comparisons in (
        list(reversed(valid.comparisons)),
        [valid.comparisons[0], valid.comparisons[0]],
        valid.comparisons[:1],
    ):
        with pytest.raises(ValidationError):
            DpeMatchResult(
                inventory=page,
                attribution=valid.attribution,
                comparisons=comparisons,
                limits=valid.limits,
            )

    malformed = valid.comparisons[0].model_copy(
        update={"criteria": [valid.comparisons[0].criteria[0]] * 2}
    )
    with pytest.raises(ValidationError):
        DpeMatchResult(
            inventory=page,
            attribution=valid.attribution,
            comparisons=[malformed, valid.comparisons[1]],
            limits=valid.limits,
        )
