"""Closed additive contracts on synthetic inputs; no DB or upstream request."""

import copy
import json
from collections import Counter
from datetime import date
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.commune.indicator_schemas import IndicateursDpe, IndicateursDvf
from app.commune.schemas import ResultatCommuneSynthese
from app.dpe.lifecycle import validity_at
from tests.oracles import commune_indicators as oracle

pytestmark = pytest.mark.unit

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/commune_indicators/synthetic.json").read_text()
)


def absent(state="not_qualified"):
    return {"etat": state, "motif": "Synthetic test: no real publication decision"}


def number(value):
    return {"etat": "value", "valeur": value}


def metadata():
    return {
        "version_contrat": "1.0",
        "version_population": "synthetic-test-only-v1",
        "date_calcul": "2026-09-08T10:00:00Z",
        "generation": None,
        "provenance": [
            {
                "nom": "Synthetic fixture",
                "producteur": "Test",
                "licence": "test",
                "reference": "synthetic.json",
                "date_publication_source": None,
                "date_snapshot": "2026-09-07T10:00:00Z",
                "date_import": "2026-09-07T12:00:00Z",
                "couverture": "complete",
            }
        ],
    }


def dvf():
    category = {
        "precedente": {
            "fenetre": {"debut_inclus": "2023-01-01", "fin_exclue": "2024-01-01"},
            "effectif": 5,
            "mediane": number(2000),
            "quantiles": absent(),
        },
        "courante": {
            "fenetre": {"debut_inclus": "2024-01-01", "fin_exclue": "2025-01-01"},
            "effectif": 5,
            "mediane": number(2200),
            "quantiles": absent(),
        },
        "variation_pct": absent(),
        "qualification_tendance": None,
    }
    meta = metadata()
    meta["provenance"][0]["couverture"] = "unknown"
    return meta | {
        "version_contrat": "1.1",
        "ancrage_temporel": {
            "etat": "delivery_received_and_stock_bound",
            "version_qualification": "dvf-delivery-qualification.v2",
            "lot_id": "synthetic-test-only-lot",
            "periode_debut_inclus": "2023-01-01",
            "periode_fin_exclue": "2025-01-01",
            "departements": ["44", "85"],
            "acquisition_complete": True,
            "revisions_possibles": True,
            "exhaustivite_historique": "unknown",
            "generation_fournisseur": None,
            "date_observation": "2026-09-07T09:00:00Z",
            "preuve_qualification_sha256": "a" * 64,
            "preuve_observation_sha256": "b" * 64,
            "preuve_reconstruction_sha256": "c" * 64,
            "snapshot_sha256": "d" * 64,
            "contenu_projete_sha256": "e" * 64,
            "cog_sha256": "f" * 64,
            "methode_projection": "sha256-sorted-row-digests-json.v1",
            "generation_stock": None,
        },
        "population": "mutations_residentielles_mono_local_hors_vefa_mono_commune",
        "unite_prix": "EUR/m2",
        "methode_quantiles": "percentile_cont",
        "methode_variation": "100*(mediane_courante/mediane_precedente-1)",
        "residentiel_maison": category,
        "residentiel_appartement": copy.deepcopy(category),
    }


def test_legacy_dvf_block_requires_explicit_temporal_migration():
    payload = dvf()
    payload["version_contrat"] = "1.0"
    payload.pop("ancrage_temporel", None)
    # This unpublished experimental block cannot silently acquire v2 meaning.
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(payload)


@pytest.mark.parametrize(
    "key,value",
    [
        ("etat", "complete"),
        ("version_qualification", "dvf-delivery-qualification.v1"),
        ("lot_id", " "),
        ("periode_debut_inclus", "2024-01-01"),
        ("periode_fin_exclue", "2026-01-01"),
        ("periode_fin_exclue", "2024-12-31"),
        ("periode_debut_inclus", "2025-01-01"),
        ("departements", ["44"]),
        ("departements", ["44", "85", "85"]),
        ("departements", ["44", "49"]),
        ("acquisition_complete", False),
        ("acquisition_complete", 1),
        ("revisions_possibles", False),
        ("revisions_possibles", "true"),
        ("exhaustivite_historique", "complete"),
        ("generation_fournisseur", "invented"),
        ("date_observation", "2027-01-01T00:00:00Z"),
        ("date_observation", "2026-01-01T00:00:00"),
        ("preuve_qualification_sha256", "unverified"),
        ("preuve_observation_sha256", "a" * 63),
        ("preuve_reconstruction_sha256", "x" * 64),
        ("snapshot_sha256", None),
        ("contenu_projete_sha256", ""),
        ("cog_sha256", "unknown"),
        ("methode_projection", "count_only"),
        ("generation_stock", "00000000-0000-4000-8000-000000000001"),
        ("complete_before", "2025-01-01"),
        ("watermarks", []),
        ("private_path", "/private/receipt.json"),
    ],
)
def test_received_lot_cannot_misstate_scope_or_evidence(key, value):
    payload = dvf()
    payload["ancrage_temporel"][key] = value
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(payload)


V3 = "dvf-delivery-qualification.v3"


@pytest.mark.parametrize(
    "departements",
    [["75"], ["44", "75", "85"], ["2A", "2B"], ["01", "971", "976"], ["44", "85"]],
)
def test_v3_lot_accepts_any_hosted_departements(departements):
    """Plan 04.1-10: a v3 lot names its received hostable departements."""
    payload = dvf()
    payload["ancrage_temporel"].update(
        version_qualification=V3, departements=departements
    )
    encoded = IndicateursDvf.model_validate(payload).model_dump(mode="json")
    assert encoded["ancrage_temporel"]["departements"] == departements
    assert encoded["ancrage_temporel"]["version_qualification"] == V3


@pytest.mark.parametrize(
    "version,departements",
    [
        # Outside the hostable list: unknown code, overseas collectivity.
        (V3, ["44", "99"]),
        (V3, ["975"]),
        (V3, ["20"]),
        # Empty, repeated or out of canonical order.
        (V3, []),
        (V3, ["75", "75"]),
        (V3, ["85", "44"]),
        (V3, [44]),
        # The v2 reading rule is unchanged: exactly 44 and 85.
        ("dvf-delivery-qualification.v2", ["44", "75", "85"]),
        ("dvf-delivery-qualification.v2", ["75"]),
    ],
)
def test_lot_scope_outside_its_version_rule_is_refused(version, departements):
    payload = dvf()
    payload["ancrage_temporel"].update(
        version_qualification=version, departements=departements
    )
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(payload)


def test_received_lot_describes_prices_without_qualifying_them_automatically():
    encoded = IndicateursDvf.model_validate(dvf()).model_dump(mode="json")
    assert encoded["provenance"][0]["couverture"] == "unknown"
    assert encoded["ancrage_temporel"]["revisions_possibles"] is True
    assert encoded["ancrage_temporel"]["generation_fournisseur"] is None
    assert encoded["ancrage_temporel"]["exhaustivite_historique"] == "unknown"
    for category in ("residentiel_maison", "residentiel_appartement"):
        assert encoded[category]["courante"]["mediane"] == number(2200)
        assert encoded[category]["courante"]["effectif"] == 5
        assert encoded[category]["courante"]["quantiles"] == absent()
        assert encoded[category]["variation_pct"] == absent()
    unknown = dvf()
    unknown["ancrage_temporel"] = {"etat": "unknown", "motif": "No stock binding"}
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(unknown)


def test_delivery_references_are_required_and_nullable_generation_is_honest():
    payload = dvf()
    for key in payload["ancrage_temporel"]:
        bad = copy.deepcopy(payload)
        del bad["ancrage_temporel"][key]
        with pytest.raises(ValidationError):
            IndicateursDvf.model_validate(bad)
    generation = "00000000-0000-4000-8000-000000000001"
    payload["generation"] = generation
    payload["ancrage_temporel"]["generation_stock"] = generation
    assert str(IndicateursDvf.model_validate(payload).generation) == generation


@pytest.mark.parametrize(
    "observed,accepted",
    [("2022-01-01T00:00:00Z", False), ("2025-01-01T00:00:00Z", True)],
)
def test_annual_received_period_cannot_follow_its_observation(observed, accepted):
    payload = dvf()
    payload["ancrage_temporel"]["date_observation"] = observed
    if accepted:
        IndicateursDvf.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            IndicateursDvf.model_validate(payload)


def test_b_comes_from_received_period_not_latest_observed_event():
    # Literal memberships are independent from the schema and its validators.
    events = ["2022-12-31", "2023-01-01", "2024-01-01", "2024-12-20", "2025-01-01"]
    assert oracle.window_members("2025-01-01", events) == (
        ["2023-01-01"],
        ["2024-01-01", "2024-12-20"],
    )
    payload = dvf()
    payload["ancrage_temporel"]["periode_fin_exclue"] = "2024-12-20"
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(payload)
    # A larger acquired period can contain both windows without changing them.
    payload = dvf()
    payload["ancrage_temporel"]["periode_debut_inclus"] = "2022-01-01"
    payload["ancrage_temporel"]["departements"] = ["85", "44"]
    IndicateursDvf.model_validate(payload)


def test_candidate_2024_2025_windows_can_be_described_without_publication_go():
    payload = dvf()
    payload["ancrage_temporel"].update(
        periode_debut_inclus="2024-01-01", periode_fin_exclue="2026-01-01"
    )
    for category in ("residentiel_maison", "residentiel_appartement"):
        payload[category]["precedente"]["fenetre"] = {
            "debut_inclus": "2024-01-01",
            "fin_exclue": "2025-01-01",
        }
        payload[category]["courante"]["fenetre"] = {
            "debut_inclus": "2025-01-01",
            "fin_exclue": "2026-01-01",
        }
    encoded = IndicateursDvf.model_validate(payload).model_dump(mode="json")
    assert encoded["residentiel_maison"]["variation_pct"] == absent()
    assert encoded["residentiel_appartement"]["courante"]["quantiles"] == absent()


def test_measurement_delivery_cannot_be_used_as_an_automatic_public_mapping():
    # The private measure contract and the public evidence projection are distinct.
    private_delivery = {
        "schema_version": "dvf-delivery-qualification.v2",
        "release_id": "synthetic-test-only-lot",
        "period_start": "2023-01-01",
        "period_end_exclusive": "2025-01-01",
        "acquisition_complete": True,
        "late_registrations_possible": True,
        "all_mutations_before_b_complete": "unknown",
        "observer_binding": {"path": "private.json", "sha256": "a" * 64},
    }
    payload = dvf()
    payload["ancrage_temporel"] = private_delivery
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(payload)
    for coverage in ("complete", "partial", "delivery_received_and_stock_bound"):
        payload = dvf()
        payload["provenance"][0]["couverture"] = coverage
        with pytest.raises(ValidationError):
            IndicateursDvf.model_validate(payload)


def dpe():
    return metadata() | {
        "population": "diagnostics_canoniques_disponibles_hors_traces_retirees",
        "periode": "bornes_etablissement_observees_non_exhaustives",
        "date_evaluation": "2026-09-08",
        "methode_mediane": "percentile_disc",
        "methode_modes": "toutes_classes_frequence_maximale_ordre_A_G",
        "resultat": {
            "etat": "value",
            "population": {
                "total": 10,
                "classes_connues": 9,
                "types_batiment": {
                    "maison": 9,
                    "appartement": 0,
                    "autre": 0,
                    "inconnu": 1,
                },
                "validite": {
                    "unknown": 1,
                    "expired": 2,
                    "not_expired_by_date": 7,
                    "inconsistent_dates": 0,
                    "future_establishment": 0,
                },
                "remplacement": {
                    "not_checked": 1,
                    "no_successor_known": 7,
                    "replaced": 2,
                    "ambiguous": 0,
                },
                "regimes": {"existant": 8, "neuf": 3, "inconnu": 1},
                "source_inconnue": 1,
                "revision_non_verifiee": 10,
            },
            "distribution": {
                "classes": {"A": 4, "B": 0, "C": 0, "D": 3, "E": 0, "F": 0, "G": 2},
                "inconnue": 1,
            },
            "modes": ["A"],
            "mediane_ordinale": "D",
            "part_fg_pct": number(200 / 9),
            "date_etablissement_min": "2021-07-01",
            "date_etablissement_max": "2026-01-01",
            "dates_etablissement_inconnues": 1,
        },
    }


def change(payload, path, value):
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return payload


def dated_dpe(known, low, high, validity):
    payload = dpe()
    result = payload["resultat"]
    result["dates_etablissement_inconnues"] = 10 - known
    result["date_etablissement_min"] = low
    result["date_etablissement_max"] = high
    result["population"]["validite"] = {
        key: validity.get(key, 0) for key in result["population"]["validite"]
    }
    return payload


@pytest.mark.parametrize(
    "known,low,high,validity",
    [
        (1, "2021-07-01", "2026-01-01", {"unknown": 9, "not_expired_by_date": 1}),
        (
            9,
            "2030-01-01",
            "2030-02-01",
            {"unknown": 1, "expired": 2, "not_expired_by_date": 7},
        ),
        (1, "2026-01-01", "2026-01-01", {"unknown": 8, "expired": 2}),
        (9, "2021-01-01", "2026-09-08", {"unknown": 1, "future_establishment": 9}),
        (9, "2021-01-01", "2030-01-01", {"unknown": 1, "not_expired_by_date": 9}),
        (9, "2030-01-01", "2030-02-01", {"unknown": 2, "future_establishment": 8}),
        (9, "2021-01-01", "2030-01-01", {"unknown": 1, "future_establishment": 9}),
    ],
)
def test_impossible_establishment_bounds_and_validity_margins_rejected(
    known, low, high, validity
):
    with pytest.raises(ValidationError):
        IndicateursDpe.model_validate(dated_dpe(known, low, high, validity))


@pytest.mark.parametrize(
    "known,low,high,validity",
    [
        (1, "2026-09-08", "2026-09-08", {"unknown": 9, "not_expired_by_date": 1}),
        (9, "2021-01-01", "2021-01-01", {"unknown": 1, "not_expired_by_date": 9}),
        (
            9,
            "2021-01-01",
            "2026-09-08",
            {"unknown": 1, "expired": 2, "not_expired_by_date": 7},
        ),
        (
            9,
            "2030-01-01",
            "2030-02-01",
            {"unknown": 1, "future_establishment": 8, "inconsistent_dates": 1},
        ),
        (9, "2030-01-01", "2030-02-01", {"unknown": 1, "inconsistent_dates": 9}),
        (
            9,
            "2021-01-01",
            "2030-01-01",
            {"unknown": 1, "not_expired_by_date": 8, "inconsistent_dates": 1},
        ),
        (1, "2030-01-01", "2030-01-01", {"unknown": 8, "inconsistent_dates": 2}),
        (0, None, None, {"unknown": 8, "inconsistent_dates": 2}),
    ],
)
def test_compatible_date_diversity_and_unknown_margins_are_preserved(
    known, low, high, validity
):
    IndicateursDpe.model_validate(dated_dpe(known, low, high, validity))


def test_date_bounds_preserve_margins_constructed_by_canonical_lifecycle():
    # Exhaust these tiny documentary populations to check necessary constraints
    # do not invent a joint distribution or override lifecycle precedence.
    evaluation = date(2026, 9, 8)
    past, future = date(2026, 9, 7), date(2026, 9, 9)
    documents = [
        (None, None, ()),
        (None, past, ("synthetic source issue",)),
        (past, past, ()),
        (past, evaluation, ()),
        (evaluation, past, ()),
        (evaluation, future, ()),
        (future, future, ()),
        (future, None, ()),
    ]
    for pair in product(documents, repeat=2):
        dates = [established for established, _, _ in pair if established]
        validity = Counter({"unknown": 8})
        for established, end, issues in pair:
            validity[validity_at(established, end, evaluation, date_issues=issues)] += 1
        payload = dated_dpe(
            len(dates),
            min(dates) if dates else None,
            max(dates) if dates else None,
            validity,
        )
        IndicateursDpe.model_validate(payload)


@pytest.mark.parametrize(
    "validity,accepted",
    [
        ({"not_expired_by_date": 9, "inconsistent_dates": 1}, False),
        ({"expired": 4, "not_expired_by_date": 5, "inconsistent_dates": 1}, False),
        ({"not_expired_by_date": 8, "inconsistent_dates": 2}, True),
    ],
)
def test_future_maximum_requires_a_known_date_not_consumed_by_nonfuture_states(
    validity, accepted
):
    payload = dated_dpe(9, "2021-01-01", "2030-01-01", validity)
    if accepted:
        # One inconsistent record may have the future date and the other no
        # establishment date. Do not assume every inconsistent date is known.
        IndicateursDpe.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            IndicateursDpe.model_validate(payload)


@pytest.mark.parametrize(
    "established,accepted", [("2026-09-08", False), ("2026-09-07", True)]
)
def test_expired_requires_establishment_strictly_before_evaluation(
    established, accepted
):
    payload = dated_dpe(1, established, established, {"expired": 1, "unknown": 9})
    if accepted:
        IndicateursDpe.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            IndicateursDpe.model_validate(payload)


@pytest.mark.parametrize("expired,accepted", [(9, False), (8, True)])
def test_maximum_at_evaluation_needs_a_known_date_not_consumed_by_expired(
    expired, accepted
):
    payload = dated_dpe(
        9,
        "2021-01-01",
        "2026-09-08",
        {"expired": expired, "inconsistent_dates": 10 - expired},
    )
    if accepted:
        IndicateursDpe.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            IndicateursDpe.model_validate(payload)


def test_legacy_fields_and_absent_blocks_remain_compatible():
    payload = {"code_insee": "00000", "dpe_median": "D", "prix_m2_median": 1234}
    result = ResultatCommuneSynthese.model_validate(payload)
    assert result.model_dump(exclude_unset=True) == payload
    assert result.indicateurs_dvf is None and result.indicateurs_dpe is None


@pytest.mark.parametrize("field", ["indicateurs_dvf", "indicateurs_dpe"])
def test_unqualified_source_block_needs_no_invented_metadata(field):
    payload = {
        "code_insee": "00000",
        field: {
            "etat": "not_qualified",
            "motif": "source_generation_unqualified",
        },
    }
    value = ResultatCommuneSynthese.model_validate_json(json.dumps(payload))
    assert json.loads(value.model_dump_json(exclude_unset=True)) == payload


@pytest.mark.parametrize("field", ["indicateurs_dvf", "indicateurs_dpe"])
@pytest.mark.parametrize(
    "extra,value",
    [
        ("valeur", 0),
        ("prix_m2_median", 2000),
        ("effectif", 0),
        ("count", 5),
        ("provenance", []),
        ("generation", None),
        ("date_calcul", "2026-09-09T00:00:00Z"),
    ],
)
def test_absent_source_block_rejects_numeric_or_provenance_payload(field, extra, value):
    with pytest.raises(ValidationError):
        ResultatCommuneSynthese.model_validate(
            {
                "code_insee": "00000",
                field: {
                    "etat": "not_qualified",
                    "motif": "source_generation_unqualified",
                    extra: value,
                },
            }
        )


@pytest.mark.parametrize(
    "field,model,payload",
    [
        ("indicateurs_dvf", IndicateursDvf, dvf),
        ("indicateurs_dpe", IndicateursDpe, dpe),
    ],
)
def test_complete_and_explicit_null_blocks_remain_compatible(field, model, payload):
    complete = model.model_validate(payload())
    result = ResultatCommuneSynthese.model_validate(
        {"code_insee": "00000", field: complete.model_dump(mode="json")}
    )
    assert getattr(result, field) == complete
    assert (
        ResultatCommuneSynthese.model_validate_json(result.model_dump_json()) == result
    )
    legacy = {"code_insee": "00000", field: None}
    assert (
        ResultatCommuneSynthese.model_validate(legacy).model_dump(exclude_unset=True)
        == legacy
    )


@pytest.mark.parametrize(
    "model,payload", [(IndicateursDvf, dvf), (IndicateursDpe, dpe)]
)
def test_complete_synthetic_contract_roundtrips(model, payload):
    value = model.model_validate(payload())
    assert model.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    "path,value",
    [
        (("version_contrat",), "2"),
        (("version_population",), " "),
        (("date_calcul",), "2026-09-08T10:00:00"),
        (("generation",), "invented"),
        (("provenance",), []),
        (("provenance", 0, "date_import"), "2027-01-01T00:00:00Z"),
        (("residentiel_maison", "courante", "fenetre", "debut_inclus"), "2026-01-01"),
        (("residentiel_maison", "courante", "effectif"), True),
        (("residentiel_maison", "courante", "effectif"), -1),
        (("residentiel_maison", "courante", "effectif"), 4),
        (("residentiel_maison", "courante", "mediane", "valeur"), 0),
        (("residentiel_maison", "courante", "mediane", "valeur"), float("nan")),
        (("residentiel_maison", "courante", "mediane", "valeur"), float("inf")),
        (("residentiel_maison", "courante", "mediane", "valeur"), float("-inf")),
        (("residentiel_maison", "courante", "mediane", "valeur"), True),
        (("residentiel_maison", "courante", "mediane", "valeur"), "2200"),
        (
            ("residentiel_maison", "courante", "quantiles"),
            {"etat": "suppressed", "motif": "private", "p10": 123},
        ),
    ],
)
def test_invalid_dvf_states_rejected(path, value):
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(change(dvf(), path, value))


@pytest.mark.parametrize(
    "path,value",
    [
        (("date_evaluation",), "2026-09-09"),
        (("date_evaluation",), "2026-09-08T00:00:00Z"),
        (("resultat", "distribution", "classes", "A"), 5),
        (("resultat", "distribution", "classes", "A"), 4.5),
        (("resultat", "distribution", "classes", "H"), 0),
        (("resultat", "modes"), ["A", "A"]),
        (("resultat", "modes"), ["D"]),
        (("resultat", "mediane_ordinale"), "A"),
        (("resultat", "part_fg_pct"), number(20)),
        (("resultat", "population", "total"), 12),
        (("resultat", "population", "classes_connues"), 10),
        (("resultat", "population", "validite", "unknown"), 2),
        (("resultat", "population", "regimes", "existant"), 11),
        (("resultat", "date_etablissement_min"), "2027-01-01"),
        (("resultat", "dates_etablissement_inconnues"), 11),
        (("resultat", "private_documents"), ["synthetic-private-id"]),
    ],
)
def test_invalid_dpe_states_rejected(path, value):
    with pytest.raises(ValidationError):
        IndicateursDpe.model_validate(change(dpe(), path, value))


@pytest.mark.parametrize(
    "state",
    [
        "not_reported",
        "out_of_coverage",
        "insufficient_sample",
        "suppressed",
        "not_qualified",
        "zero_denominator",
    ],
)
def test_absence_cannot_hide_numeric_or_private_payload(state):
    payload = dpe() | {"resultat": absent(state)}
    encoded = IndicateursDpe.model_validate(payload).model_dump(mode="json")
    assert set(encoded["resultat"]) == {"etat", "motif"}
    for field in ("valeur", "distribution", "private_documents"):
        polluted = copy.deepcopy(payload)
        polluted["resultat"][field] = 99
        with pytest.raises(ValidationError):
            IndicateursDpe.model_validate(polluted)


def test_quantile_order_and_median_and_qualification():
    payload = dvf()
    window = payload["residentiel_maison"]["courante"]
    window["quantiles"] = {
        "etat": "value",
        "p10": 2200,
        "p50": 2200,
        "p90": 2200,
        "qualification": "synthetic-only",
    }
    IndicateursDvf.model_validate(payload)
    for key, value in (
        ("p10", 2300),
        ("p50", 2199),
        ("qualification", ""),
        ("p90", float("inf")),
    ):
        corrupt = copy.deepcopy(payload)
        corrupt["residentiel_maison"]["courante"]["quantiles"][key] = value
        with pytest.raises(ValidationError):
            IndicateursDvf.model_validate(corrupt)


def test_variation_requires_exact_medians_and_separate_qualification():
    payload = dvf()
    category = payload["residentiel_maison"]
    category["variation_pct"] = number(10)
    category["qualification_tendance"] = "synthetic-only-policy"
    IndicateursDvf.model_validate(payload)
    for path, value in [
        (("provenance", 0, "couverture"), "partial"),
        (("provenance", 0, "couverture"), "complete"),
        (("residentiel_maison", "qualification_tendance"), None),
        (("residentiel_maison", "variation_pct", "valeur"), 0),
        (("residentiel_maison", "precedente", "mediane"), absent("suppressed")),
    ]:
        with pytest.raises(ValidationError):
            IndicateursDvf.model_validate(change(copy.deepcopy(payload), path, value))


def test_zero_effectif_is_distinct_from_missing_or_suppressed_value():
    payload = dvf()
    current = payload["residentiel_appartement"]["courante"]
    current.update(effectif=0, mediane=absent("not_reported"))
    encoded = IndicateursDvf.model_validate(payload).model_dump(mode="json")
    assert encoded["residentiel_appartement"]["courante"]["effectif"] == 0
    assert "valeur" not in encoded["residentiel_appartement"]["courante"]["mediane"]


def test_unknown_watermark_never_forces_invented_window_or_price():
    payload = dvf()
    payload["ancrage_temporel"] = {"etat": "unknown", "motif": "Lot non qualifié"}
    for key in ("residentiel_maison", "residentiel_appartement"):
        for period in ("precedente", "courante"):
            payload[key][period].update(
                fenetre=None,
                effectif=None,
                motif_periode="Watermark inconnu",
                mediane=absent(),
            )
    IndicateursDvf.model_validate(payload)
    for key, value in (
        ("mediane", number(2000)),
        ("motif_periode", None),
        ("effectif", 5),
    ):
        bad = copy.deepcopy(payload)
        bad["residentiel_maison"]["courante"][key] = value
        with pytest.raises(ValidationError):
            IndicateursDvf.model_validate(bad)


@pytest.mark.parametrize(
    "factory,model", [(dvf, IndicateursDvf), (dpe, IndicateursDpe)]
)
@pytest.mark.parametrize(
    "field",
    [
        "version_contrat",
        "version_population",
        "population",
        "date_calcul",
        "generation",
        "provenance",
    ],
)
def test_additive_block_requires_explicit_identity_dates_and_provenance(
    factory, model, field
):
    payload = factory()
    del payload[field]
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_literal_synthetic_file_is_explicitly_not_source_evidence():
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/commune_indicators/synthetic.json"
        ).read_text()
    )
    assert fixture["evidence_kind"] == "synthetic-test-only"
    assert fixture["real_publication_authorized"] is False


@pytest.mark.parametrize("case", FIXTURES["prices"], ids=lambda case: case["name"])
def test_literal_price_oracles(case):
    oracle.assert_price_case(case, synthetic_median_n=FIXTURES["policy"]["median_n"])


@pytest.mark.parametrize("case", FIXTURES["energy"], ids=lambda case: case["name"])
def test_literal_energy_oracles(case):
    oracle.assert_energy_case(case)
    permuted = copy.deepcopy(case)
    permuted["classes"].reverse()
    oracle.assert_energy_case(permuted)


@pytest.mark.parametrize("case", FIXTURES["windows"])
def test_literal_calendar_boundaries_and_leap_year(case):
    oracle.assert_window_case(case)
    previous, current = oracle.window_members(case["anchor"], case["dates"])
    assert not set(previous) & set(current)
    assert case["anchor"] not in current


def test_oracle_canaries_really_reject_class_boundary_and_denominator_corruption():
    wrong_class = copy.deepcopy(FIXTURES["energy"][0])
    wrong_class["classes"][0] = "G"
    with pytest.raises(AssertionError):
        oracle.assert_energy_case(wrong_class)
    wrong_denominator = copy.deepcopy(FIXTURES["energy"][0])
    wrong_denominator["expected"]["known"] += 1
    with pytest.raises(AssertionError):
        oracle.assert_energy_case(wrong_denominator)
    wrong_boundary = copy.deepcopy(FIXTURES["windows"][0])
    wrong_boundary["expected_current"].append(wrong_boundary["anchor"])
    with pytest.raises(AssertionError):
        oracle.assert_window_case(wrong_boundary)
    wrong_price = copy.deepcopy(FIXTURES["prices"][1])
    wrong_price["values"][2] = 301
    with pytest.raises(AssertionError):
        oracle.assert_price_case(wrong_price, synthetic_median_n=5)


def test_rounded_equal_medians_have_nonzero_exact_variation():
    from decimal import Decimal

    case = FIXTURES["rounded_medians"]
    assert round(Decimal(case["previous"])) == case["display_previous"]
    assert round(Decimal(case["current"])) == case["display_current"]
    exact = oracle.variation(case["previous"], case["current"], complete_source=True)
    assert exact == Decimal(case["expected_variation"])
    assert exact != 0
    assert oracle.variation("2000", "2000", complete_source=True) == 0
    assert oracle.variation("2000", "2200", complete_source=False) is None
    payload = dvf()
    row = payload["residentiel_maison"]
    row["precedente"]["mediane"] = number(float(case["previous"]))
    row["courante"]["mediane"] = number(float(case["current"]))
    row["variation_pct"] = number(float(exact))
    row["qualification_tendance"] = "synthetic-only"
    IndicateursDvf.model_validate(payload)
    row["variation_pct"] = number(0)
    with pytest.raises(ValidationError):
        IndicateursDvf.model_validate(payload)


def test_source_duplicates_count_diagnostics_once_and_conflicts_are_refused():
    observations = FIXTURES["source_duplicates"]
    documents = oracle.canonical_documents(observations)
    assert len(documents) == FIXTURES["expected_distinct_diagnostics"]
    assert oracle.ordinal_summary(list(documents.values())) == {
        "total": 2,
        "known": 2,
        "unknown": 0,
        "modes": ["A", "G"],
        "median": "A",
        "fg_numerator": 1,
    }
    conflict = copy.deepcopy(observations)
    conflict[1]["class"] = "G"
    with pytest.raises(ValueError):
        oracle.canonical_documents(conflict)


def test_oracles_have_no_application_or_sql_dependency():
    import ast

    source = Path(oracle.__file__).read_text()
    modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }
    assert modules <= {"calendar", "collections", "datetime", "decimal"}


@pytest.mark.parametrize(
    "classes,known,unknown,modes,median",
    [
        (
            {"A": 2, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0, "G": 2},
            4,
            6,
            ["A", "G"],
            "A",
        ),
        ({"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0, "G": 0}, 0, 10, [], None),
    ],
)
def test_dpe_ties_and_unknown_denominator_contract(
    classes, known, unknown, modes, median
):
    payload = dpe()
    result = payload["resultat"]
    result["distribution"] = {"classes": classes, "inconnue": unknown}
    result["population"]["classes_connues"] = known
    result.update(
        modes=modes,
        mediane_ordinale=median,
        part_fg_pct=number(50) if known else absent("zero_denominator"),
    )
    IndicateursDpe.model_validate(payload)
    result["part_fg_pct"] = number(0)
    with pytest.raises(ValidationError):
        IndicateursDpe.model_validate(payload)
    if modes:
        result["part_fg_pct"] = number(50)
        result["modes"].reverse()
        with pytest.raises(ValidationError):
            IndicateursDpe.model_validate(payload)


@pytest.mark.parametrize(
    "code,accepted",
    [
        ("44109", True),
        ("75056", True),
        ("2A004", True),
        ("97411", True),
        ("97501", False),
        ("99001", False),
        ("4410", False),
    ],
)
def test_stored_cell_code_is_a_hosted_commune(code, accepted):
    from app.commune.indicator_results import _Cell

    cell = {
        "dataset_name": "dvf",
        "code_insee": code,
        "public_payload": {},
        "private_binding": {},
    }
    if accepted:
        assert _Cell.model_validate(cell).code_insee == code
    else:
        with pytest.raises(ValidationError):
            _Cell.model_validate(cell)
