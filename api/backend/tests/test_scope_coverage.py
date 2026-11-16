"""Truth table of the « hors couverture » rule (D-24, DATA-03)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.common.schemas import DatasetHosting, OutOfCoverageReason, ResolutionOutcome
from app.common.semantic_contract import (
    ALLOWED_OUT_OF_COVERAGE_REASONS,
    validate_out_of_coverage_reasons,
)
from app.common.source_catalog import AGGREGATE_REQUIRED_SOURCES, RESOLVER_SOURCE_MAP
from app.resolve.router import _provably_out_of_coverage
from app.resolve.schemas import DatasetResolutionState, out_of_coverage_state
from app.scope.coverage import (
    REASON_ORDER,
    hosting_by_source,
    hosting_for,
    hosting_for_key,
    hosting_for_sources,
)
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "fiche-fields.v1.json"

SCOPE = InstanceScope(
    datasets=frozenset({"dvf", "transports", "georisques"}),
    departements=("44", "85"),
)
NOT_HOSTED = OutOfCoverageReason.DATASET_NON_HEBERGE
OUTSIDE = OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE


def test_exactly_three_reasons_in_precedence_order():
    assert [reason.value for reason in REASON_ORDER] == [
        "dataset_non_heberge",
        "territoire_hors_perimetre",
        "source_sans_couverture",
    ]


@pytest.mark.parametrize(
    ("key", "departement", "etat", "motif"),
    [
        ("dvf", None, "heberge", None),
        ("dvf", "44", "heberge", None),
        ("dvf", "75", "hors_couverture", OUTSIDE),
        ("antennes", None, "hors_couverture", NOT_HOSTED),
        ("antennes", "44", "hors_couverture", NOT_HOSTED),
        # Both reasons apply: the dataset one wins.
        ("antennes", "75", "hors_couverture", NOT_HOSTED),
        ("georisques", "85", "heberge", None),
    ],
)
def test_hosting_for_truth_table(key, departement, etat, motif):
    hosting = hosting_for(key, SCOPE, departement)
    assert (hosting.etat, hosting.motif) == (etat, motif)


def test_hosted_dataset_carries_the_scope_departements():
    assert hosting_for("dvf", SCOPE, None).departements == ["44", "85"]
    assert hosting_for("antennes", SCOPE, None).departements == []


NO_SOURCE = OutOfCoverageReason.SOURCE_SANS_COUVERTURE
# permis: source local to 93 (KI-16); dvf: no Alsace-Moselle nor Mayotte.
SOURCE_SCOPE = InstanceScope(
    datasets=frozenset({"dvf", "permis"}), departements=("44", "57", "85")
)


@pytest.mark.parametrize(
    ("scope", "key", "departement", "etat", "motif"),
    [
        # Third reason: the catalogue says the source publishes nothing there.
        (SOURCE_SCOPE, "permis", "44", "hors_couverture", NO_SOURCE),
        (SOURCE_SCOPE, "dvf", "57", "hors_couverture", NO_SOURCE),
        (SOURCE_SCOPE, "dvf", "44", "heberge", None),
        # No departement: scope ∩ source coverage decides.
        (SOURCE_SCOPE, "permis", None, "hors_couverture", NO_SOURCE),
        (SOURCE_SCOPE, "dvf", None, "heberge", None),
        (
            InstanceScope(frozenset({"permis"}), ("44", "93")),
            "permis",
            None,
            "heberge",
            None,
        ),
        # Precedence: territory before source, dataset before both.
        (SOURCE_SCOPE, "permis", "33", "hors_couverture", OUTSIDE),
        (SOURCE_SCOPE, "antennes", "44", "hors_couverture", NOT_HOSTED),
    ],
)
def test_source_without_coverage_truth_table(scope, key, departement, etat, motif):
    hosting = hosting_for(key, scope, departement)
    assert (hosting.etat, hosting.motif) == (etat, motif)


def test_hosted_departements_are_those_the_source_publishes():
    # 57 is hosted but DVF does not publish it: not served as covered.
    assert hosting_for("dvf", SOURCE_SCOPE, None).departements == ["44", "85"]
    scope_93 = InstanceScope(frozenset({"permis"}), ("44", "93"))
    assert hosting_for("permis", scope_93, None).departements == ["93"]


def test_aggregate_without_source_coverage_keeps_the_source_reason():
    assert hosting_for_sources(["permis"], SOURCE_SCOPE, None).motif == NO_SOURCE
    # dvf covers 44: the aggregate stays hosted.
    assert hosting_for_sources(["permis", "dvf"], SOURCE_SCOPE, "44").etat == "heberge"


def test_empty_scope_hosts_nothing():
    empty = InstanceScope(datasets=frozenset(), departements=())
    assert hosting_for("dvf", empty, None).motif == NOT_HOSTED


def test_synthesis_and_unknown_keys_are_refused():
    with pytest.raises(ValueError, match="synthesis"):
        hosting_for("commune", SCOPE, None)
    with pytest.raises(KeyError):
        hosting_for("inconnu", SCOPE, None)


def test_aggregate_is_hosted_when_one_source_is():
    # transports = transports + gares; only transports is hosted.
    assert hosting_for_sources(["transports", "gares"], SCOPE, None).etat == "heberge"


def test_aggregate_reason_follows_precedence_among_sources():
    # dvf is outside for 75 (territory), antennes is not hosted: dataset wins.
    hosting = hosting_for_sources(["dvf", "antennes"], SCOPE, "75")
    assert (hosting.etat, hosting.motif) == ("hors_couverture", NOT_HOSTED)
    assert hosting_for_sources(["dvf"], SCOPE, "75").motif == OUTSIDE


def test_hosting_motif_iff_out_of_coverage():
    with pytest.raises(ValidationError):
        DatasetHosting(etat="hors_couverture")
    with pytest.raises(ValidationError):
        DatasetHosting(etat="heberge", motif=NOT_HOSTED)
    with pytest.raises(ValidationError):
        DatasetHosting(etat="hors_couverture", motif="cinquieme_etat")


def test_resolution_motif_only_on_out_of_coverage():
    state = out_of_coverage_state("antennes", NOT_HOSTED)
    assert state.model_dump()["motif"] == "dataset_non_heberge"
    with pytest.raises(ValidationError):
        DatasetResolutionState(
            dataset="dvf",
            presence="absent",
            absence_reason="not_reported",
            motif=NOT_HOSTED,
        )
    with pytest.raises(ValidationError):
        ResolutionOutcome(issue="exact", motif=OUTSIDE)


def test_motif_is_omitted_when_absent():
    # Additive field: existing states and outcomes keep their wire shape.
    state = DatasetResolutionState(dataset="dvf", presence="value")
    assert "motif" not in state.model_dump()
    assert "motif" not in ResolutionOutcome(issue="exact").model_dump()


def test_contract_publishes_the_same_three_reasons():
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert document["allowed_out_of_coverage_reasons"] == [
        reason.value for reason in OutOfCoverageReason
    ]
    assert list(ALLOWED_OUT_OF_COVERAGE_REASONS) == [
        reason.value for reason in OutOfCoverageReason
    ]
    assert validate_out_of_coverage_reasons(document) == []


@pytest.mark.parametrize(
    "reasons",
    [
        None,
        ["dataset_non_heberge", "territoire_hors_perimetre"],
        ["dataset_non_heberge", "dataset_non_heberge", "source_sans_couverture"],
        ["territoire_hors_perimetre", "dataset_non_heberge", "source_sans_couverture"],
        [*ALLOWED_OUT_OF_COVERAGE_REASONS, "cinquieme_etat"],
    ],
)
def test_contract_refuses_any_other_reason_list(reasons):
    document = {} if reasons is None else {"allowed_out_of_coverage_reasons": reasons}
    assert validate_out_of_coverage_reasons(document)


@pytest.mark.parametrize(
    ("dept", "proven"),
    [
        ("75", True),
        ("44", False),
        (None, False),
        # Corsican postcode on a 44/85 scope: neither 2A nor 2B is hosted.
        ("20", True),
        # Synthetic 99xxx codes name no department: no proof.
        ("99", False),
    ],
)
def test_territory_is_out_of_coverage_only_on_a_real_department(dept, proven):
    assert _provably_out_of_coverage(dept, SCOPE) is proven


@pytest.mark.parametrize("hosted", ["2A", "2B"])
def test_corsican_postcode_is_no_proof_when_a_corsican_departement_is_hosted(hosted):
    scope = InstanceScope(datasets=SCOPE.datasets, departements=("44", hosted))
    assert _provably_out_of_coverage("20", scope) is False


# -- partially hosted aggregate keys (02-09 task 4) --------------------------

COMMUNE = RESOLVER_SOURCE_MAP["commune"]


def test_aggregate_hosting_by_source_keeps_each_state():
    by_source = hosting_by_source(["transports", "gares"], SCOPE, None)
    assert {key: h.etat for key, h in by_source.items()} == {
        "transports": "heberge",
        "gares": "hors_couverture",
    }
    assert by_source["gares"].motif == NOT_HOSTED
    assert hosting_for_key("transports", SCOPE, None).etat == "heberge"


def test_aggregate_with_no_hosted_source_is_out_of_coverage():
    scope = InstanceScope(frozenset({"dvf"}), ("44", "85"))
    hosting = hosting_for_key("transports", scope, None)
    assert (hosting.etat, hosting.motif) == ("hors_couverture", NOT_HOSTED)


def test_commune_without_its_structural_source_is_out_of_coverage():
    assert AGGREGATE_REQUIRED_SOURCES["commune"] == ("demographie",)
    twelve = InstanceScope(frozenset(set(COMMUNE) - {"demographie"}), ("44", "85"))
    hosting = hosting_for_key("commune", twelve, "44")
    assert (hosting.etat, hosting.motif) == ("hors_couverture", NOT_HOSTED)
    # Without the structural rule, twelve hosted sources would host the key.
    assert hosting_for_sources(COMMUNE, twelve, "44").etat == "heberge"


def test_commune_outside_the_territory_keeps_the_territory_reason():
    full = InstanceScope(frozenset(COMMUNE), ("44", "85"))
    assert hosting_for_key("commune", full, "33").motif == OUTSIDE


def test_commune_with_demographie_but_without_revenus_is_hosted():
    scope = InstanceScope(frozenset(set(COMMUNE) - {"revenus"}), ("44", "85"))
    assert hosting_for_key("commune", scope, "44").etat == "heberge"
    # Column masking of revenus is the projection's job (test_commune_coverage).
    assert hosting_by_source(COMMUNE, scope, "44")["revenus"].motif == NOT_HOSTED
