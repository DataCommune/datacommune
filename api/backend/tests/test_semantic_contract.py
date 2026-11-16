"""Semantic contract registry tests (Phase 43, D-03/D-04/D-06).

Promoted from the offline scaffold (Plan 43-02): the validator prototype
that lived here is now the real backend authority in
`app.common.semantic_contract`, imported directly instead of re-implemented.
These tests cover both the validator logic (synthetic fixtures, including
invalid ones that must turn red) and the real shipped registry
(`contracts/fiche-fields.v1.json` + its byte-identical packaged copies).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from app.common.semantic_contract import (
    ALLOWED_FORMATTERS,
    ALLOWED_RATTACHEMENTS,
    ALLOWED_STATES,
    RESERVED_PHASE_44_KEYS,
    FieldContract,
    SemanticContractError,
    assert_fields_covered,
    get_field,
    list_field_ids,
    load_registry,
    validate_field_entry,
    validate_rattachements,
    validate_registry,
)

# tests/test_semantic_contract.py -> parents[1] is api/backend, parents[2] is api/,
# parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SOURCE = _REPO_ROOT / "contracts" / "fiche-fields.v1.json"
_BACKEND_COPY = (
    _REPO_ROOT / "api" / "backend" / "app" / "common" / "fiche-fields.v1.json"
)
_FRONTEND_COPY = (
    _REPO_ROOT / "frontend" / "src" / "lib" / "data" / "fiche-fields.v1.json"
)


def _valid_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "field_id": "fiscalite.taux_tfpb",
        "value_type": "decimal",
        "unit": "percent_point",
        "precision": {"fraction_digits": 2},
        "plausible_range": {"min": 0, "max": 100},
        "zero_semantics": "measured_zero",
        "formatter": "percent_points",
        "allowed_states": ["value", "not_reported", "temporary_error"],
    }
    base.update(overrides)
    return base


# --- validator: synthetic fixtures (promoted from the 43-02 scaffold) ------------


def test_valid_synthetic_entry_has_no_errors() -> None:
    assert validate_field_entry(_valid_entry()) == []


def test_missing_required_key_is_rejected() -> None:
    entry = _valid_entry()
    del entry["precision"]
    errors = validate_field_entry(entry)
    assert any("missing required keys" in e for e in errors)


def test_unknown_value_type_is_rejected() -> None:
    entry = _valid_entry(value_type="float128")
    errors = validate_field_entry(entry)
    assert any("unknown value_type" in e for e in errors)


def test_unknown_formatter_is_rejected() -> None:
    """Synthetic invalid fixture: an unknown formatter must turn this red."""
    entry = _valid_entry(formatter="eval(payload)")
    errors = validate_field_entry(entry)
    assert any("unknown formatter" in e for e in errors)


def test_unknown_zero_semantics_is_rejected() -> None:
    entry = _valid_entry(zero_semantics="always_zero")
    errors = validate_field_entry(entry)
    assert any("unknown zero_semantics" in e for e in errors)


def test_state_outside_d04_closed_set_is_rejected() -> None:
    entry = _valid_entry(allowed_states=["value", "maybe_absent"])
    errors = validate_field_entry(entry)
    assert any("unknown states" in e for e in errors)


def test_empty_allowed_states_is_rejected() -> None:
    entry = _valid_entry(allowed_states=[])
    errors = validate_field_entry(entry)
    assert any("allowed_states must not be empty" in e for e in errors)


def test_phase_44_provenance_keys_are_rejected() -> None:
    """D-06: Phase 43 must never smuggle in Phase 44's provenance/freshness."""
    entry = _valid_entry(provenance={"producer": "DGFiP"})
    errors = validate_field_entry(entry)
    assert any("reserved Phase 44 keys" in e for e in errors)


def test_duplicate_field_id_across_registry_is_rejected() -> None:
    registry = [_valid_entry(), _valid_entry()]
    errors = validate_registry(registry)
    assert any("duplicate field_id" in e for e in errors)


def test_registry_with_two_distinct_valid_entries_has_no_errors() -> None:
    registry = [
        _valid_entry(),
        _valid_entry(
            field_id="demographie.densite", unit="person_per_km2", formatter="density"
        ),
    ]
    assert validate_registry(registry) == []


# --- real backend authority: app.common.semantic_contract ------------------------


def test_load_registry_succeeds_on_the_shipped_packaged_copy() -> None:
    load_registry.cache_clear()
    registry = load_registry()
    assert isinstance(registry, dict)
    assert len(registry) > 0
    assert all(isinstance(v, FieldContract) for v in registry.values())


def test_get_field_returns_typed_contract_for_a_known_field() -> None:
    contract = get_field("fiscalite.taux_tfpb")
    assert contract.value_type == "decimal"
    assert contract.unit == "percent_point"
    assert contract.formatter == "percent_points"
    assert contract.zero_semantics == "measured_zero"
    assert "value" in contract.allowed_states


def test_get_field_raises_key_error_for_unknown_field() -> None:
    with pytest.raises(KeyError):
        get_field("does.not.exist")


def test_assert_fields_covered_passes_for_known_fields() -> None:
    assert_fields_covered(["fiscalite.taux_tfpb", "demographie.superficie"])


def test_assert_fields_covered_raises_for_a_field_missing_a_contract() -> None:
    """The coverage assertion required by D-03: a new Fiche field without a
    contract must fail loudly, not silently pass through."""
    with pytest.raises(SemanticContractError, match="missing a semantic contract"):
        assert_fields_covered(["fiscalite.taux_tfpb", "bien.some_future_field"])


# --- real shipped registry: contracts/fiche-fields.v1.json + copies --------------


def test_shipped_registry_has_no_duplicate_field_ids() -> None:
    field_ids = list_field_ids()
    # list_field_ids is already a set/frozenset keyed by field_id, so a
    # duplicate would have silently overwritten a prior entry — cross-check
    # against the raw file to catch that case explicitly.
    import json

    document = json.loads(_SOURCE.read_bytes())
    raw_ids = [entry["field_id"] for entry in document["fields"]]
    assert len(raw_ids) == len(set(raw_ids)), (
        "duplicate field_id in contracts/fiche-fields.v1.json"
    )
    assert set(raw_ids) == set(field_ids)


def test_shipped_registry_has_no_reserved_phase_44_keys() -> None:
    import json

    document = json.loads(_SOURCE.read_bytes())
    for entry in document["fields"]:
        overlap = RESERVED_PHASE_44_KEYS & entry.keys()
        assert not overlap, (
            f"{entry['field_id']} carries reserved Phase 44 keys: {overlap}"
        )


def test_shipped_registry_formatters_and_states_are_in_the_closed_enums() -> None:
    for contract in load_registry().values():
        assert contract.formatter in ALLOWED_FORMATTERS
        assert set(contract.allowed_states) <= ALLOWED_STATES


def test_shipped_registry_covers_the_fiscalite_rate_fields() -> None:
    """Sentinel from 43-CONTEXT.md: 40.01 must stay 40.01, never 0.4001 —
    the registry must at least know about the four DGFiP rate fields."""
    assert_fields_covered(
        [
            "fiscalite.taux_tfpb",
            "fiscalite.taux_tfpnb",
            "fiscalite.taux_th_rs",
            "fiscalite.taux_teom",
        ]
    )


def test_global_property_tax_rates_have_no_invented_semantic_ceiling() -> None:
    registry = load_registry()
    for field_id in ("fiscalite.taux_tfpb", "fiscalite.taux_tfpnb"):
        contract = registry[field_id]
        assert contract.range_min == 0
        assert contract.range_max is None


# --- D-47 / D-46 (plan 03-06): typed absences and the dominant DPE class ---------


def test_the_state_vocabulary_is_aligned_on_the_indicator_absences() -> None:
    """IndicateurAbsent.etat and the flat-column states are contract states."""
    import json
    import typing

    from app.commune.absences import EtatAbsenceColonne
    from app.commune.indicator_schemas import IndicateurAbsent

    indicator = set(typing.get_args(IndicateurAbsent.model_fields["etat"].annotation))
    column = set(typing.get_args(EtatAbsenceColonne))
    assert indicator | column <= ALLOWED_STATES
    document = json.loads(_SOURCE.read_bytes())
    assert set(document["allowed_states"]) == ALLOWED_STATES
    assert len(document["allowed_states"]) == len(ALLOWED_STATES)


def test_block_fields_allow_the_absences_their_block_produces() -> None:
    registry = load_registry()
    for kind in ("maison", "appartement"):
        for field in ("mediane_courante", "mediane_precedente", "variation_pct"):
            states = registry[f"dvf.indicateurs.{kind}.{field}"].allowed_states
            assert {"insufficient_sample", "not_qualified", "not_reported"} <= set(
                states
            ), field
    for quantile in ("p10", "p50", "p90"):
        states = registry[f"dvf.indicateurs.quantiles.{quantile}"].allowed_states
        assert "not_qualified" in states
    assert "zero_denominator" in registry["dpe.indicateurs.part_fg_pct"].allowed_states


def test_the_dominant_dpe_class_has_contract_entries() -> None:
    from app.commune.schemas import commune_field_id

    registry = load_registry()
    for field_id in ("dpe.indicateurs.modes", "dpe.indicateurs.mediane_ordinale"):
        contract = registry[field_id]
        assert (contract.value_type, contract.formatter) == ("string", "text")
        assert contract.description and contract.description.strip()
    assert "not_reported" in registry["dpe.indicateurs.mediane_ordinale"].allowed_states
    path = ("indicateurs_dpe", "resultat")
    assert commune_field_id((*path, "modes", "[1]")) == "dpe.indicateurs.modes"
    assert (
        commune_field_id((*path, "mediane_ordinale"))
        == "dpe.indicateurs.mediane_ordinale"
    )


def test_shipped_registry_covers_the_superficie_densite_fields() -> None:
    """D-07/D-08 sentinel: superficie/densite must be governed fields, not
    ad hoc numbers with no unit/precision/zero-semantics contract."""
    assert_fields_covered(["demographie.superficie", "demographie.densite"])


# --- SEM-02: nature of the match (04-03) ------------------------------------------


def test_rattachement_outside_the_closed_list_is_rejected() -> None:
    errors = validate_field_entry(_valid_entry(rattachement="parcelle_la_plus_proche"))
    assert any("rattachement" in error for error in errors), errors


def test_rattachement_inside_the_closed_list_is_accepted() -> None:
    for rattachement in ALLOWED_RATTACHEMENTS:
        assert validate_field_entry(_valid_entry(rattachement=rattachement)) == []


def test_the_document_lists_exactly_the_allowed_rattachements() -> None:
    document = {"allowed_rattachements": list(ALLOWED_RATTACHEMENTS)}
    assert validate_rattachements(document) == []
    assert validate_rattachements({}) != []
    assert validate_rattachements(
        {"allowed_rattachements": [*ALLOWED_RATTACHEMENTS, "rayon_de_la_commune"]}
    )


def test_allowed_rattachements_is_the_closed_list_of_04_03() -> None:
    # 04-05 adds « Dans la commune de l'adresse » (similar sales in the commune);
    # 04-06 adds the building the RNB links to the BAN key (RNB identifier);
    # C37 adds the sales whose declared DVF address is the address of the page;
    # 04.1-13 adds the DPE whose raw address names the address exactly;
    # 04.7-10 the key figures of a departement page, computed over the
    # communes of the departement (never a fact at an address or commune).
    assert ALLOWED_RATTACHEMENTS == (
        "adresse_saisie_diagnostiqueur",
        "parcelle_du_point",
        "batiment_du_point",
        "batiment_rnb_de_l_adresse",
        "au_point",
        "rayon_du_point",
        "commune_de_l_adresse",
        "eligibilite_adresse",
        "commune_reference",
        "adresse_declaree_vente",
        "adresse_brute_exacte",
        "departement",
    )


# 04.7-10: the four cards of a departement page and their bases.
_DEPARTEMENT_CARDS = (
    "departement.population.total",
    "departement.population.communes_comptees",
    "departement.prix.appartement.mediane_m2",
    "departement.prix.appartement.effectif",
    "departement.prix.maison.mediane_m2",
    "departement.prix.maison.effectif",
    "departement.dpe.part_fg_pct",
    "departement.dpe.classes_connues",
)


@pytest.mark.parametrize("field_id", _DEPARTEMENT_CARDS)
def test_departement_cards_are_defined_at_the_departement(field_id) -> None:
    contract = get_field(field_id)
    assert contract.rattachement == "departement"
    assert contract.description
    assert "temporary_error" in contract.allowed_states
    # Never a fabricated zero: an absence of sale or document is a state.
    assert "not_reported" in contract.allowed_states


def test_departement_states_match_the_absences_the_route_serves() -> None:
    assert (
        "insufficient_sample"
        in get_field("departement.prix.maison.mediane_m2").allowed_states
    )
    assert "zero_denominator" in get_field("departement.dpe.part_fg_pct").allowed_states
    # A served count of sales is never 0 (not_reported instead).
    assert get_field("departement.prix.maison.effectif").zero_semantics == (
        "implausible_zero"
    )
    # The DPE share follows the production population, said by its definition.
    assert (
        "population documentaire"
        in (get_field("departement.dpe.part_fg_pct").description or "").lower()
    )


@pytest.mark.parametrize(
    ("field_id", "rattachement"),
    [
        ("adresse.dpe.nombre_declares", "adresse_saisie_diagnostiqueur"),
        ("dpe.document.etiquette_dpe", "adresse_saisie_diagnostiqueur"),
        ("dpe.document.etiquette_ges", "adresse_saisie_diagnostiqueur"),
        ("dpe.document.surface_habitable", "adresse_saisie_diagnostiqueur"),
        ("adresse.dvf.ventes_parcelle.nombre", "parcelle_du_point"),
        ("adresse.dvf.ventes_parcelle.derniere", "parcelle_du_point"),
        ("adresse.dvf.ventes_adresse.nombre", "adresse_declaree_vente"),
        ("adresse.risques.argiles_au_point", "au_point"),
        ("adresse.reference_commune.prix_median_maison", "commune_reference"),
        ("adresse.reference_commune.prix_median_appartement", "commune_reference"),
    ],
)
def test_address_fields_carry_their_rattachement(field_id, rattachement) -> None:
    contract = get_field(field_id)
    assert contract.rattachement == rattachement
    assert contract.description


def test_the_clay_landmark_is_never_a_count_of_risks() -> None:
    """N-03: the landmark is the exposure measured at the point, a text."""
    contract = get_field("adresse.risques.argiles_au_point")
    assert contract.value_type == "string"
    assert contract.zero_semantics == "not_applicable"
    assert not any("nombre_au_point" in field_id for field_id in list_field_ids())


def test_the_declared_dpe_count_never_names_a_dwelling() -> None:
    contract = get_field("adresse.dpe.nombre_declares")
    assert contract.value_type == "integer"
    assert contract.zero_semantics == "measured_zero"
    assert "logement précis" in (contract.description or "")
    assert "temporary_error" in contract.allowed_states


# --- byte-stable packaged copies (T-43-05) ----------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packaged_copies_are_byte_identical_to_the_source_of_truth() -> None:
    """The sync script is the only writer of the packaged copies (D-03 truth:
    'derivees et byte-stables, jamais des registres paralleles'). A manual
    edit to either copy — or a stale copy after the source changed — must
    turn this test red."""
    assert _SOURCE.exists(), f"missing canonical source: {_SOURCE}"
    assert _BACKEND_COPY.exists(), f"missing backend packaged copy: {_BACKEND_COPY}"
    assert _FRONTEND_COPY.exists(), f"missing frontend packaged copy: {_FRONTEND_COPY}"

    source_hash = _sha256(_SOURCE)
    assert _sha256(_BACKEND_COPY) == source_hash, (
        "backend packaged copy has drifted from the source"
    )
    assert _sha256(_FRONTEND_COPY) == source_hash, (
        "frontend packaged copy has drifted from the source"
    )
