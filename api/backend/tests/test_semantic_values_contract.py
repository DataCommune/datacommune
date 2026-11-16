"""Served values audited against the semantic contract (SEM-01, SEM-02).

Every adversarial case starts from the real producer (``apply_commune_coverage``
and ``assemble_provenance``, or a resolve state built by the resolver's own
constructors) and changes a single point.

Scope of the audit, as written in plan 02-09: structure, presence of a
contract entry per served numeric value, consistency of state and reason,
and the ORIGIN of a known period in the producer's facts. It checks neither
the accuracy of a value nor the correctness of an address/object match:
those belong to the real-data measures of plan 02-18.
"""

import copy
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.common.schemas import (
    MetaReponse,
    OutOfCoverageReason,
    ReponseApi,
    ResolutionOutcome,
    SourcePeriod,
)
from app.common.semantic_audit import (
    audit_commune_payload,
    audit_provenance_origin,
    audit_resolve_payload,
)
from app.common.semantic_contract import load_registry
from app.commune.schemas import (
    COMMUNE_SOURCE_KEYS,
    ResultatCommuneSynthese,
    apply_commune_coverage,
    commune_coverage,
)
from app.database import get_db
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.loyers.schemas import ResultatLoyer
from app.resolve.schemas import out_of_coverage_state, resolution_state
from app.scope.model import InstanceScope
from tests import test_commune_indicator_contract as indicator_fixtures

pytestmark = pytest.mark.unit

D = datetime(2026, 9, 22, 8, 30, tzinfo=UTC)
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SCOPE = InstanceScope(frozenset({*COMMUNE_SOURCE_KEYS, "dvf", "dpe"}), ("44", "85"))
ROW = {
    "code_insee": "44109",
    "nom_commune": "Nantes",
    "code_departement": "44",
    "code_region": "52",
    "population": 320732,
    "annee_demographie": 2021,
    "revenu_median": 22480.0,
    "taux_vacance": 7.8,
    "zone_abc": "B1",
    "zone_abc_reclassement": False,
    "nb_equipements": 0,
    "annee_equipements": 2022,
    "fibre_pct": 81.5,
}


def _commune_payload(row=ROW, scope=SCOPE, facts=None) -> dict:
    """A /v1/commune body assembled by the task 2 producer."""
    served = apply_commune_coverage(row, commune_coverage(scope, row["code_insee"]))
    facts = facts or {}
    provenances = [
        assemble_provenance(key, facts.get(key, ProvenanceFacts()), NOW)
        for key in COMMUNE_SOURCE_KEYS
    ]
    body = ReponseApi(
        donnees=[ResultatCommuneSynthese(**served)],
        meta=MetaReponse(source=provenances),
    )
    return body.model_dump(mode="json")


# -- audit_commune_payload ---------------------------------------------------


def test_payload_of_the_producer_passes():
    facts = {"revenus": ProvenanceFacts(source_period_label="2021")}
    assert audit_commune_payload(_commune_payload(facts=facts)) == []


def test_blocks_of_a_closed_rollout_gate_pass():
    # Todo 2026-11-01: the explicit absence served while the gate is closed
    # goes through the producer and the audit unchanged. The audit checks a
    # block state against a leaf of the contract only: no leaf names a whole
    # block, so this proves "no error", not an allowed state.
    gated = {"etat": "not_qualified", "motif": "indicateurs_communaux_non_publies"}
    row = {**ROW, "indicateurs_dvf": gated, "indicateurs_dpe": gated}
    payload = _commune_payload(row=row)
    assert payload["donnees"][0]["indicateurs_dvf"] == gated
    assert audit_commune_payload(payload) == []


async def test_commune_http_exposes_the_exact_dvf_median_window_once(
    client, monkeypatch
):
    """85191 keeps its established median and receives its inclusive window."""
    from app.commune import router as commune_router
    from app.main import app

    row = {
        **ROW,
        "code_insee": "85191",
        "nom_commune": "Saint-Jean-de-Monts",
        "code_departement": "85",
        "prix_m2_median": 2357,
        "nb_mutations_12m": 460,
    }
    read_synthesis = AsyncMock(return_value=[row])
    read_window = AsyncMock(return_value="2024-08-19 → 2025-08-19")
    monkeypatch.setattr(commune_router, "commune_synthese_par_codes", read_synthesis)
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", read_window)
    # The yearly price series is another read of the route (migration 115).
    monkeypatch.setattr(
        commune_router, "tendances_prix_par_codes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        commune_router, "get_provenance_facts", AsyncMock(return_value={})
    )
    app.dependency_overrides[get_db] = lambda: object()

    response = await client.get("/v1/commune", params={"code_insee": "85191"})

    assert response.status_code == 200, response.text
    commune = response.json()["donnees"][0]
    assert commune["prix_m2_median"] == 2357
    assert commune["nb_mutations_12m"] == 460
    assert commune["fenetre_mediane_dvf"] == "2024-08-19 → 2025-08-19"
    read_window.assert_awaited_once()


async def test_commune_http_keeps_dvf_median_window_absent_without_dvf_counts(
    client, monkeypatch
):
    """No current-day or source-period fallback is invented without a DVF block."""
    from app.commune import router as commune_router
    from app.main import app

    row = {
        **ROW,
        "code_insee": "85191",
        "nom_commune": "Saint-Jean-de-Monts",
        "code_departement": "85",
        "prix_m2_median": None,
        "nb_mutations_12m": None,
    }
    read_window = AsyncMock(return_value="2024-08-19 → 2025-08-19")
    monkeypatch.setattr(
        commune_router,
        "commune_synthese_par_codes",
        AsyncMock(return_value=[row]),
    )
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", read_window)
    # The yearly price series is another read of the route (migration 115).
    monkeypatch.setattr(
        commune_router, "tendances_prix_par_codes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        commune_router, "get_provenance_facts", AsyncMock(return_value={})
    )
    app.dependency_overrides[get_db] = lambda: object()

    response = await client.get("/v1/commune", params={"code_insee": "85191"})

    assert response.status_code == 200, response.text
    commune = response.json()["donnees"][0]
    assert commune["prix_m2_median"] is None
    assert commune["fenetre_mediane_dvf"] is None
    read_window.assert_not_awaited()


async def test_commune_http_dates_a_served_dvf_count_below_median_threshold(
    client, monkeypatch
):
    """An insufficient sample still describes the exact window of its count."""
    from app.commune import router as commune_router
    from app.main import app

    row = {
        **ROW,
        "code_insee": "85191",
        "nom_commune": "Saint-Jean-de-Monts",
        "code_departement": "85",
        "prix_m2_median": None,
        "nb_mutations_12m": 3,
    }
    read_window = AsyncMock(return_value="2024-08-19 → 2025-08-19")
    monkeypatch.setattr(
        commune_router,
        "commune_synthese_par_codes",
        AsyncMock(return_value=[row]),
    )
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", read_window)
    # The yearly price series is another read of the route (migration 115).
    monkeypatch.setattr(
        commune_router, "tendances_prix_par_codes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        commune_router, "get_provenance_facts", AsyncMock(return_value={})
    )
    app.dependency_overrides[get_db] = lambda: object()

    response = await client.get("/v1/commune", params={"code_insee": "85191"})

    assert response.status_code == 200, response.text
    commune = response.json()["donnees"][0]
    assert commune["prix_m2_median"] is None
    assert commune["nb_mutations_12m"] == 3
    assert commune["fenetre_mediane_dvf"] == "2024-08-19 → 2025-08-19"
    read_window.assert_awaited_once()


def test_numeric_column_without_contract_entry_is_an_error():
    payload = _commune_payload()
    payload["donnees"][0]["nb_licornes"] = 3
    errors = audit_commune_payload(payload)
    assert any("nb_licornes" in error for error in errors), errors


RENT = ResultatLoyer.model_config["json_schema_extra"]["examples"][0]
RENT_DETAIL = {
    **RENT,
    "nb_observations_commune": 12450,
    "nb_observations_maille": 30000,
    "r2_ajuste": 0.82,
    "trimestre_reference": 3,
    "charges_comprises": True,
    "meuble": False,
    "surface_reference_m2": 52,
    "surface_piece_reference_m2": 22.2,
}


def _dvf_block():
    block = indicator_fixtures.dvf()
    for kind in ("residentiel_maison", "residentiel_appartement"):
        for window in ("precedente", "courante"):
            median = block[kind][window]["mediane"]["valeur"]
            block[kind][window]["quantiles"] = {
                "etat": "value",
                "p10": median - 500.0,
                "p50": float(median),
                "p90": median + 500.0,
                "qualification": "synthetic",
            }
    return block


def test_every_nested_value_of_a_full_payload_has_a_contract_entry():
    # D3: rent objects and both indicator blocks, audited without exclusion.
    row = {
        **ROW,
        "annee_loyers": 2025,
        "loyers_appartement": RENT_DETAIL,
        "loyers_maison": {**RENT_DETAIL, "type_habitat": "maison"},
        "indicateurs_dvf": _dvf_block(),
        "indicateurs_dpe": indicator_fixtures.dpe(),
    }
    payload = _commune_payload(row=row)
    served = payload["donnees"][0]
    assert served["indicateurs_dvf"]["residentiel_maison"]["courante"]["effectif"] == 5
    assert served["indicateurs_dpe"]["resultat"]["population"]["total"] > 0
    assert served["loyers_maison"]["loyer_m2_bas"] == 19.8
    assert audit_commune_payload(payload) == []


def test_nested_numeric_value_without_contract_entry_is_an_error():
    payload = _commune_payload(row={**ROW, "loyers_appartement": RENT})
    assert audit_commune_payload(payload) == []
    payload["donnees"][0]["loyers_appartement"]["loyer_inconnu"] = 1.5
    payload["donnees"][0]["indicateurs_dpe"] = {"resultat": {"indice_inconnu": 2}}
    errors = audit_commune_payload(payload)
    assert any("loyers_appartement.loyer_inconnu" in error for error in errors), errors
    assert any("indicateurs_dpe.resultat.indice_inconnu" in e for e in errors), errors
    # The string fields of the nested object are not numeric values.
    assert not any("loyers_appartement.type_habitat" in e for e in errors)


def test_vintages_are_audited_against_their_contract_entry():
    # IN-01: annee_<source> is <source>.annee, no longer declared non-semantic.
    payload = _commune_payload()
    assert payload["donnees"][0]["annee_equipements"] == 2022
    contract = {
        key: value
        for key, value in load_registry().items()
        if key != "equipements.annee"
    }
    errors = audit_commune_payload(payload, contract)
    assert errors == [
        "donnees[0].annee_equipements: numeric value without a semantic contract entry"
    ]


def test_meta_without_the_thirteen_sources_is_an_error():
    # IN-02: an empty meta.source must not pass the audit vacuously.
    payload = _commune_payload()
    payload["meta"]["source"] = []
    assert audit_commune_payload(payload)
    payload = _commune_payload()
    payload["meta"]["source"].pop()
    assert audit_commune_payload(payload)


def test_known_period_without_label_is_an_error():
    payload = _commune_payload()
    payload["meta"]["source"][0]["periode"] = {
        "statut": "connue",
        "label": None,
        "debut": None,
        "fin": None,
    }
    errors = audit_commune_payload(payload)
    assert any("demographie" in error and "label" in error for error in errors), errors


def test_zero_on_an_out_of_coverage_source_is_an_error():
    payload = _commune_payload()
    assert payload["donnees"][0]["nb_equipements"] == 0
    payload["donnees"][0]["couverture"]["equipements"] = {
        "etat": "hors_couverture",
        "motif": "dataset_non_heberge",
        "departements": [],
    }
    errors = audit_commune_payload(payload)
    assert any("nb_equipements" in error for error in errors), errors


# -- D-47: no flat column served null without a declared state -------------


def test_every_null_column_of_the_producer_carries_a_declared_state():
    served = _commune_payload()["donnees"][0]
    assert served["nb_menages"] is None
    # demographie row present (population, vintage): the missing count is not
    # published; revenus row present (revenu_median): same for the Gini index.
    assert served["etats"]["nb_menages"]["etat"] == "not_available"
    assert served["etats"]["indice_gini"]["etat"] == "not_available"
    # Nothing of fiscalite in the row: no row of the source for the commune.
    assert served["etats"]["taux_tfpb"]["etat"] == "not_reported"


def test_null_column_without_declared_state_is_an_error():
    payload = _commune_payload()
    payload["donnees"][0]["etats"].pop("nb_menages", None)
    errors = audit_commune_payload(payload)
    assert any("nb_menages" in error for error in errors), errors


def test_state_outside_the_allowed_states_of_its_field_is_an_error():
    payload = _commune_payload()
    payload["donnees"][0]["etats"]["indice_gini"] = {
        "etat": "zero_denominator",
        "motif": "Aucune base de calcul.",
    }
    errors = audit_commune_payload(payload)
    assert any("indice_gini" in e and "zero_denominator" in e for e in errors), errors


def test_state_declared_on_a_served_value_is_an_error():
    payload = _commune_payload()
    assert payload["donnees"][0]["population"] == 320732
    payload["donnees"][0]["etats"]["population"] = {
        "etat": "not_reported",
        "motif": "La source chargée n'a aucune ligne pour la commune.",
    }
    errors = audit_commune_payload(payload)
    assert any("population" in error for error in errors), errors


def test_out_of_coverage_state_must_match_the_hosting_of_its_source():
    payload = _commune_payload()
    payload["donnees"][0]["etats"]["indice_gini"] = {
        "etat": "out_of_coverage",
        "motif": "dataset_non_heberge",
    }
    errors = audit_commune_payload(payload)
    assert any("indice_gini" in error for error in errors), errors


def test_block_absence_outside_the_allowed_states_is_an_error():
    payload = _commune_payload(row={**ROW, "indicateurs_dvf": _dvf_block()})
    assert audit_commune_payload(payload) == []
    window = payload["donnees"][0]["indicateurs_dvf"]["residentiel_maison"]["courante"]
    window["mediane"] = {"etat": "suppressed", "motif": "secret"}
    errors = audit_commune_payload(payload)
    assert any(
        "residentiel_maison.courante.mediane" in e and "suppressed" in e for e in errors
    ), errors


# -- audit_provenance_origin: the origin, not an equality of strings ---------


def test_label_equal_to_the_import_date_is_legitimate_when_upstream_says_so():
    # (i) The source published the day it was imported: label == import date.
    facts = ProvenanceFacts(last_success_at=D, source_updated_at=D)
    provenance = assemble_provenance("dvf", facts, NOW)
    assert provenance.periode == SourcePeriod(statut="connue", label="2026-09-22")
    assert provenance.derniere_importation_reussie.date().isoformat() == "2026-09-22"
    assert audit_provenance_origin(facts, provenance) == []


def test_label_without_any_upstream_fact_is_fabricated():
    # (ii) What a regression of _assemble_period would produce.
    facts = ProvenanceFacts(last_success_at=D)
    honest = assemble_provenance("dvf", facts, NOW)
    fabricated = honest.model_copy(
        update={"periode": SourcePeriod(statut="connue", label="2026-09-22")}
    )
    errors = audit_provenance_origin(facts, fabricated)
    assert errors and "dvf" in errors[0]


def test_label_that_no_upstream_fact_carries_is_fabricated():
    facts = ProvenanceFacts(last_success_at=D, source_period_label="2024")
    provenance = assemble_provenance("revenus", facts, NOW)
    assert audit_provenance_origin(facts, provenance) == []
    shifted = provenance.model_copy(
        update={"periode": SourcePeriod(statut="connue", label="2025")}
    )
    assert audit_provenance_origin(facts, shifted)


def test_empty_upstream_label_follows_the_producer_truth_test():
    # IN-03: an empty label alone never makes a known period, as in the producer.
    facts = ProvenanceFacts(last_success_at=D, source_period_label="")
    honest = assemble_provenance("dvf", facts, NOW)
    assert honest.periode.statut == "inconnue"
    forced = honest.model_copy(
        update={"periode": SourcePeriod(statut="connue", label="")}
    )
    assert audit_provenance_origin(facts, forced)
    # With an upstream date the producer keeps the given (empty) label: same
    # origin for the audit; the empty label itself is refused on the payload.
    dated = ProvenanceFacts(
        last_success_at=D, source_period_label="", source_updated_at=D
    )
    provenance = assemble_provenance("dvf", dated, NOW)
    assert provenance.periode == SourcePeriod(statut="connue", label="")
    assert audit_provenance_origin(dated, provenance) == []


@pytest.mark.parametrize(
    ("key", "statut"), [("dvf", "inconnue"), ("sirene", "glissante")]
)
def test_producer_never_makes_a_period_of_an_import_date(key, statut):
    # The guarantee of provenance.py the audit relies on.
    facts = ProvenanceFacts(last_success_at=D)
    provenance = assemble_provenance(key, facts, NOW)
    assert provenance.periode.statut == statut
    assert provenance.periode.label is None
    assert audit_provenance_origin(facts, provenance) == []


# -- audit_resolve_payload ---------------------------------------------------


def _resolve_payload() -> dict:
    states = {
        "dvf": resolution_state("dvf", [{"valeur_fonciere": 0}], is_error=False),
        "loyers": resolution_state("loyers", None, is_error=False),
        "antennes": out_of_coverage_state(
            "antennes", OutOfCoverageReason.DATASET_NON_HEBERGE
        ),
    }
    return {
        "statut": "ok",
        "donnees": [
            {
                "dataset_states": {
                    name: state.model_dump(mode="json")
                    for name, state in states.items()
                }
            }
        ],
        "meta": {"resolution": ResolutionOutcome(issue="exact").model_dump()},
    }


def test_resolve_payload_of_the_producer_passes():
    assert audit_resolve_payload(_resolve_payload()) == []


def test_out_of_coverage_without_reason_is_an_error():
    payload = _resolve_payload()
    del payload["donnees"][0]["dataset_states"]["antennes"]["motif"]
    errors = audit_resolve_payload(payload)
    assert any("antennes" in error for error in errors), errors


def test_reason_on_a_value_state_is_an_error():
    payload = _resolve_payload()
    payload["donnees"][0]["dataset_states"]["dvf"]["motif"] = "dataset_non_heberge"
    errors = audit_resolve_payload(payload)
    assert any("dvf" in error for error in errors), errors


def test_unknown_reason_is_an_error():
    payload = _resolve_payload()
    states = payload["donnees"][0]["dataset_states"]
    states["antennes"]["motif"] = "cinquieme_etat"
    assert audit_resolve_payload(payload)


def test_outcome_out_of_coverage_needs_its_reason():
    payload = _resolve_payload()
    payload["donnees"] = []
    payload["meta"]["resolution"] = {"issue": "out_of_coverage", "raison": "x"}
    assert audit_resolve_payload(payload)
    fixed = copy.deepcopy(payload)
    fixed["meta"]["resolution"]["motif"] = "territoire_hors_perimetre"
    assert audit_resolve_payload(fixed) == []


def test_rent_descriptions_name_no_vintage():
    # IN-14: the vintage is loyers.prediction.annee (etl_loyers.config.ANNEE),
    # never a year written in a description.
    import re

    descriptions = {
        field_id: field.description
        for field_id, field in load_registry().items()
        if field_id.startswith("loyers.prediction.")
    }
    assert len(descriptions) == 12
    dated = [
        key for key, text in descriptions.items() if re.search(r"\b20\d\d\b", text)
    ]
    assert dated == []
