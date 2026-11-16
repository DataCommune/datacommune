"""Closed additive lifecycle contracts, independent of the wall clock."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.dpe import schemas
from app.dpe.lifecycle import source_scope

pytestmark = pytest.mark.unit


def lifecycle(**changes):
    return {
        "source_state": "present",
        "source_snapshot_date": "2026-09-05",
        "source_scope": "44/85",
        "regimes": ["existant", "neuf"],
        "replacement_state": "replaced",
        "graph_issues": ["missing_target"],
        "validity": "unknown",
        "as_of_date": "2026-09-07",
        "revision_status": "unverified",
        "revision_kind": None,
        "revision_date": None,
        "detail_availability": "available",
        **changes,
    }


def test_whole_lifecycle_preserves_independent_facts_and_public_version():
    item = schemas.DpeInventoryItem(
        document_ref="opaque",
        lifecycle=lifecycle(validity="expired"),
        date_etablissement_dpe="2020-01-01",
        date_fin_validite_dpe="2026-09-06",
    )
    assert item.lifecycle.replacement_state == "replaced"
    assert item.lifecycle.validity == "expired"
    assert item.lifecycle.as_of_date == date(2026, 9, 7)
    assert schemas.DpeInventoryItem(document_ref="opaque").lifecycle is None


@pytest.mark.parametrize("field", list(lifecycle()))
def test_every_lifecycle_key_is_required(field):
    payload = lifecycle()
    del payload[field]
    with pytest.raises(ValidationError):
        schemas.DpeInventoryItem(document_ref="opaque", lifecycle=payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_state": "SECRET-UNSUPPORTED"},
        {"source_scope": "France"},
        {"source_snapshot_date": None},
        {"source_snapshot_date": "2026-09-05T00:00:00"},
        {"source_snapshot_date": "20260905"},
        {"as_of_date": 1788739200},
        {"as_of_date": "2026-09-07T00:00:00Z"},
        {"source_snapshot_date": "2026-09-08"},
        {"regimes": ["existant", "existant"]},
        {"regimes": ["unknown"]},
        {"regimes": "existant"},
        {"graph_issues": ["cycle", "cycle"]},
        {"graph_issues": ["SECRET-EDGE"]},
        {"replacement_state": "current"},
        {"validity": "valid"},
        {"revision_kind": "2026"},
        {"revision_date": "2026-09-01"},
        {"revision_status": "verified_attestation"},
        {
            "revision_status": "verified_attestation",
            "revision_kind": 2026,
            "revision_date": "2026-09-01",
        },
        {
            "revision_status": "verified_attestation",
            "revision_kind": "2026",
            "revision_date": "2026-09-08",
        },
        {"detail_availability": "SECRET-DETAIL"},
        {"numero_dpe": "SECRET-PRIVATE-ID"},
        {"SECRET-PRIVATE-KEY": "private-value"},
    ],
)
def test_invalid_lifecycle_is_rejected_as_a_whole(changes):
    with pytest.raises(ValidationError) as error:
        schemas.DpeInventoryItem(document_ref="opaque", lifecycle=lifecycle(**changes))
    assert "SECRET-PRIVATE-ID" not in str(error.value)
    assert "SECRET-PRIVATE-KEY" not in str(error.value)


def test_reserved_revision_shape_requires_both_fields_without_creating_evidence():
    model = schemas.DpeLifecycle(
        **lifecycle(
            revision_status="verified_attestation",
            revision_kind="2026",
            revision_date="2026-09-01",
        )
    )
    assert model.revision_kind == "2026"


def test_page_date_matches_all_lifecycle_dates():
    payload = dict(
        state="ready",
        context_ref="context",
        total=1,
        distribution={"unknown": 1},
        page_size=20,
        has_more=False,
        read_state="complete",
        items=[{"document_ref": "opaque", "lifecycle": lifecycle()}],
    )
    page = schemas.DpeInventoryPage(**payload, as_of_date="2026-09-07")
    assert page.version == 2
    for value in (None, "2026-09-06", "20260907"):
        with pytest.raises(ValidationError):
            schemas.DpeInventoryPage(**payload, as_of_date=value)


def test_legacy_unknown_with_end_date_is_allowed_without_wall_clock_recalculation():
    schemas.DpeConsultedDocument(
        context_ref="context",
        document_ref="document",
        date_etablissement_dpe="2030-01-01",
        date_fin_validite_dpe="2029-01-01",
        validity_by_date="unknown",
    )


def test_detail_validity_matches_explicit_lifecycle_date():
    common = dict(
        context_ref="context",
        document_ref="document",
        date_etablissement_dpe="2020-01-01",
        date_fin_validite_dpe="2026-09-06",
        lifecycle=lifecycle(validity="expired"),
    )
    schemas.DpeConsultedDocument(**common, validity_by_date="expired")
    with pytest.raises(ValidationError):
        schemas.DpeConsultedDocument(**common, validity_by_date="unknown")
    with pytest.raises(ValidationError):
        schemas.DpeConsultedDocument(
            **{**common, "date_etablissement_dpe": "2030-01-01"},
            validity_by_date="expired",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("etiquette_dpe", "D"),
        ("surface_habitable", 42),
        ("date_fin_validite_dpe", "2020-01-01"),
        ("type_batiment", "maison"),
    ],
)
def test_tombstone_rejects_old_content_on_both_public_shapes(field, value):
    payload = dict(
        document_ref="document",
        lifecycle=lifecycle(
            source_state="absent_from_complete_scope",
            detail_availability="source_reconciled",
            validity="unknown",
        ),
        **{field: value},
    )
    with pytest.raises(ValidationError):
        schemas.DpeInventoryItem(**payload)
    with pytest.raises(ValidationError):
        schemas.DpeConsultedDocument(
            **payload, context_ref="context", validity_by_date="unknown"
        )


@pytest.mark.parametrize(
    "established,end,state,legacy",
    [
        ("2020-01-01", "2026-09-07", "not_expired_by_date", "not_expired_by_date"),
        ("2020-01-01", "2026-09-06", "expired", "expired"),
        (None, "2030-01-01", "unknown", "unknown"),
        ("2020-01-01", None, "unknown", "unknown"),
        ("2030-01-01", "2020-01-01", "inconsistent_dates", "unknown"),
        ("2030-01-01", "2031-01-01", "future_establishment", "unknown"),
        (None, "2020-01-01", "inconsistent_dates", "unknown"),
    ],
)
def test_precise_validity_and_legacy_projection_share_explicit_date(
    established, end, state, legacy
):
    common = {
        "document_ref": "document",
        "date_etablissement_dpe": established,
        "date_fin_validite_dpe": end,
        "lifecycle": lifecycle(validity=state),
    }
    schemas.DpeInventoryItem(**common)
    detail = schemas.DpeConsultedDocument(
        **common, context_ref="context", validity_by_date=legacy
    )
    assert detail.version == 2


def test_unknown_source_and_reconciled_content_can_stay_explicitly_unknown():
    unknown = schemas.DpeLifecycle(
        **lifecycle(
            source_state="unknown",
            source_snapshot_date=None,
            regimes=[],
            replacement_state="not_checked",
            graph_issues=[],
        )
    )
    assert unknown.source_snapshot_date is None
    detail = schemas.DpeConsultedDocument(
        context_ref="context",
        document_ref="document",
        validity_by_date="unknown",
        lifecycle=lifecycle(
            source_state="absent_from_complete_scope",
            detail_availability="source_reconciled",
        ),
    )
    assert detail.etiquette_dpe is None


def test_source_scope_list_is_the_hostable_list():
    from app.scope.model import DEPARTEMENTS_HEBERGEABLES

    assert schemas.SOURCE_SCOPE_DEPARTEMENTS == DEPARTEMENTS_HEBERGEABLES


@pytest.mark.parametrize("scope", ["44/85", "44", "85", "971", "976", "75", "2A"])
def test_source_scope_accepts_a_hostable_departement_or_the_legacy_label(scope):
    """Plan 04.1-08: the scope names one hostable departement; 44/85 kept."""
    assert schemas.DpeLifecycle(**lifecycle(source_scope=scope)).source_scope == scope


@pytest.mark.parametrize(
    "scope",
    ["99", "975", "977", "988", "2a", "20", "44,85", "85/44", "France", "", " 44", 44],
)
def test_source_scope_refuses_anything_outside_the_hostable_list(scope):
    with pytest.raises(ValidationError):
        schemas.DpeInventoryItem(
            document_ref="opaque", lifecycle=lifecycle(source_scope=scope)
        )


def _observation(department, state, observed_at="2026-09-05T22:30:00Z", slug="x"):
    return {
        "source_slug": slug,
        "department": department,
        "observed_at": observed_at,
        "state": state,
    }


@pytest.mark.parametrize(
    "observations,state,expected",
    [
        ([_observation("971", "present")], "present", "971"),
        (
            [_observation("75", "absent_from_complete_scope")],
            "absent_from_complete_scope",
            "75",
        ),
        ([_observation("44", "present")], "present", "44/85"),
        ([_observation("85", "present")], "present", "44/85"),
        # Founding = the observations carrying the published state.
        (
            [
                _observation("971", "absent_from_complete_scope"),
                _observation("972", "present"),
            ],
            "present",
            "972",
        ),
        # Several departements: the earliest founding observation names it.
        (
            [
                _observation("972", "present", "2026-09-05T22:30:00Z"),
                _observation("971", "present", "2026-09-01T08:00:00Z"),
            ],
            "present",
            "971",
        ),
    ],
)
def test_source_scope_is_the_departement_of_the_founding_observation(
    observations, state, expected
):
    assert source_scope(observations, state) == expected


@pytest.mark.parametrize(
    "observations,state,expected",
    [
        # Rows imported before migration 091 carry no observation (44/85 era).
        ([], "unknown", "44/85"),
        ([], "absent_from_complete_scope", "44/85"),
        # No observation carries the state: all of them name the departement.
        ([_observation("971", "unknown")], "absent_from_complete_scope", "971"),
    ],
)
def test_source_scope_without_founding_observation(observations, state, expected):
    assert source_scope(observations, state) == expected


def test_ambiguous_source_scope_is_never_guessed():
    observations = [_observation("971", "present"), _observation("972", "present")]
    with pytest.raises(ValueError):
        source_scope(observations, "present")
