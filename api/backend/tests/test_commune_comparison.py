"""Comparability of communes (D-48, COM-03): the single rule, pure.

Syntheses are built by hand through the same projection as ``/v1/commune``
(``apply_commune_coverage``): no database, no HTTP.
"""

import copy
import dataclasses
import math
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from app.common.exceptions import ErreurApiError
from app.common.schemas import DatasetHosting
from app.common.semantic_contract import load_registry
from app.commune.comparison import (
    METHODE_INCONNUE,
    Critere,
    ValeurServie,
    _value,
    build_comparison,
    compare_cell,
)
from app.commune.router import comparison_codes
from app.commune.schemas import (
    COMMUNE_SOURCE_KEYS,
    CelluleComparaison,
    CommuneComparee,
    CritereComparaison,
    ResultatCommuneSynthese,
    apply_commune_coverage,
    commune_field_id,
)
from app.loyers.schemas import ResultatLoyer
from tests import test_commune_indicator_contract as indicator_fixtures

pytestmark = pytest.mark.unit

HOSTED = DatasetHosting(etat="heberge", departements=["44", "85"])
OUT = DatasetHosting(etat="hors_couverture", motif="territoire_hors_perimetre")


def _commune(code: str, **columns) -> CommuneComparee:
    """A hosted commune whose synthesis serves *columns* (others null)."""
    couverture = dict.fromkeys(COMMUNE_SOURCE_KEYS, HOSTED)
    row = {"code_insee": code, "nom_commune": f"Commune {code}", **columns}
    served = apply_commune_coverage(row, couverture)
    return CommuneComparee(
        code_insee=code,
        nom=f"Commune {code}",
        departement=code[:2],
        hebergement=HOSTED,
        synthese=ResultatCommuneSynthese(**served),
    )


def _out_of_coverage(code: str) -> CommuneComparee:
    return CommuneComparee(
        code_insee=code,
        nom=f"Commune {code}",
        departement=code[:2],
        hebergement=OUT,
        synthese=None,
    )


def _critere(communes, cle: str) -> CritereComparaison:
    result = build_comparison(communes)
    return next(critere for critere in result.criteres if critere.cle == cle)


def test_gap_is_served_for_values_of_the_same_vintage():
    critere = _critere(
        [
            _commune("44109", revenu_median=21000.0, annee_revenus=2021),
            _commune("85047", revenu_median=22480.0, annee_revenus=2021),
        ],
        "revenu_median",
    )
    reference, other = critere.cellules
    assert (reference.ecart, reference.motif_non_calcul) == (None, None)
    assert reference.periode == other.periode == "2021"
    assert other.ecart is not None
    assert other.ecart.valeur == 1480
    assert other.motif_non_calcul is None
    assert critere.field_id == "revenus.revenu_median"


def test_different_vintages_give_a_reason_and_no_gap():
    critere = _critere(
        [
            _commune("44109", revenu_median=21000.0, annee_revenus=2021),
            _commune("85047", revenu_median=22480.0, annee_revenus=2019),
        ],
        "revenu_median",
    )
    other = critere.cellules[1]
    assert other.ecart is None
    assert other.motif_non_calcul == "periodes_differentes"
    assert other.periode == "2019"


def test_equal_values_of_different_vintages_are_not_a_zero_gap():
    critere = _critere(
        [
            _commune("44109", revenu_median=21000.0, annee_revenus=2021),
            _commune("85047", revenu_median=21000.0, annee_revenus=2019),
        ],
        "revenu_median",
    )
    other = critere.cellules[1]
    assert other.ecart is None
    assert other.motif_non_calcul == "periodes_differentes"
    assert other.identique_a_la_reference is False
    assert critere.identiques is False


def test_an_absent_value_gives_donnee_absente_with_its_declared_state():
    critere = _critere(
        [
            _commune("44109", revenu_median=21000.0, annee_revenus=2021),
            _commune("85047", annee_revenus=2021),
        ],
        "revenu_median",
    )
    other = critere.cellules[1]
    assert other.valeur is None
    assert other.etat == "not_available"
    assert other.ecart is None
    assert other.motif_non_calcul == "donnee_absente"


def test_equal_values_of_the_same_vintage_give_a_served_zero_gap():
    critere = _critere(
        [
            _commune("44109", revenu_median=21000.0, annee_revenus=2021),
            _commune("85047", revenu_median=21000.0, annee_revenus=2021),
        ],
        "revenu_median",
    )
    other = critere.cellules[1]
    assert other.ecart is not None
    assert other.ecart.valeur == 0
    assert other.ecart.unite == "euro_per_uc_year"
    assert other.motif_non_calcul is None
    assert other.identique_a_la_reference is True
    assert critere.identiques is True


# --- Task 2: the complete rule ---------------------------------------------


def _rate(reference: float, other: float) -> CritereComparaison:
    return _critere(
        [
            _commune("44109", taux_pauvrete=reference, annee_revenus=2021),
            _commune("85047", taux_pauvrete=other, annee_revenus=2021),
        ],
        "taux_pauvrete",
    )


def test_a_rate_gap_is_served_in_points():
    other = _rate(15.2, 16.0).cellules[1]
    assert other.ecart is not None
    assert other.ecart.unite == "point"
    assert other.ecart.valeur == pytest.approx(0.8)


def test_another_unit_keeps_the_unit_of_the_field():
    critere = _critere(
        [
            _commune("44109", nb_menages=1000, annee_demographie=2021),
            _commune("85047", nb_menages=1250, annee_demographie=2021),
        ],
        "nb_menages",
    )
    assert critere.cellules[1].ecart.model_dump() == {"valeur": 250, "unite": "count"}


@pytest.mark.parametrize(
    ("reference", "other", "expected"),
    [(12.34, 12.345, 0.01), (12.345, 12.34, -0.01), (12.34, 12.344, 0.0)],
)
def test_the_gap_is_rounded_half_away_from_zero_at_the_field_precision(
    reference, other, expected
):
    ecart = _rate(reference, other).cellules[1].ecart
    assert ecart is not None, "a tiny gap is served, never an absence"
    assert ecart.valeur == expected
    # 0.004 rounds to a served 0, not to a negative zero.
    assert math.copysign(1, ecart.valeur) == math.copysign(1, expected or 1.0)


def test_a_negative_tiny_gap_is_served_as_zero_not_negative_zero():
    ecart = _rate(12.344, 12.34).cellules[1].ecart
    assert ecart.valeur == 0
    assert math.copysign(1, ecart.valeur) == 1


def _dvf_block(shift_years: int = 0, maison_courante: float = 2200.0) -> dict:
    """The synthetic DVF block, its windows moved by *shift_years*."""
    block = indicator_fixtures.dvf()

    def moved(day: str) -> str:
        return f"{int(day[:4]) + shift_years}{day[4:]}"

    for kind in ("residentiel_maison", "residentiel_appartement"):
        for window in ("precedente", "courante"):
            fenetre = block[kind][window]["fenetre"]
            for bound in ("debut_inclus", "fin_exclue"):
                fenetre[bound] = moved(fenetre[bound])
    anchor = block["ancrage_temporel"]
    for bound in ("periode_debut_inclus", "periode_fin_exclue"):
        anchor[bound] = moved(anchor[bound])
    block["residentiel_maison"]["courante"]["mediane"]["valeur"] = maison_courante
    return block


MEDIANE = "indicateurs_dvf.residentiel_maison.courante.mediane"


def test_dvf_medians_of_the_same_window_give_the_gap():
    critere = _critere(
        [
            _commune("44109", indicateurs_dvf=_dvf_block()),
            _commune("85047", indicateurs_dvf=_dvf_block(maison_courante=2300.0)),
        ],
        MEDIANE,
    )
    reference, other = critere.cellules
    assert critere.field_id == "dvf.indicateurs.maison.mediane_courante"
    assert reference.periode == other.periode == "[2024-01-01, 2025-01-01["
    assert other.ecart.model_dump() == {"valeur": 100, "unite": "EUR/m2"}


def test_dvf_medians_of_different_windows_are_not_compared():
    critere = _critere(
        [
            _commune("44109", indicateurs_dvf=_dvf_block()),
            _commune("85047", indicateurs_dvf=_dvf_block(shift_years=-1)),
        ],
        MEDIANE,
    )
    other = critere.cellules[1]
    assert other.periode == "[2023-01-01, 2024-01-01["
    assert other.ecart is None
    assert other.motif_non_calcul == "periodes_differentes"


def test_a_variation_is_dated_by_both_windows_and_keeps_its_absence():
    critere = _critere(
        [
            _commune("44109", indicateurs_dvf=_dvf_block()),
            _commune("85047", indicateurs_dvf=_dvf_block()),
        ],
        "indicateurs_dvf.residentiel_maison.variation_pct",
    )
    reference = critere.cellules[0]
    assert reference.periode == "[2023-01-01, 2024-01-01[ → [2024-01-01, 2025-01-01["
    assert (reference.etat, reference.valeur) == ("not_qualified", None)
    assert critere.cellules[1].motif_non_calcul == "donnee_absente"


def _served(**changes) -> ValeurServie:
    base = {
        "etat": "value",
        "valeur": 10.0,
        "periode": "2021",
        "unite": "count",
        "definition": ("demographie.nb_menages",),
    }
    return ValeurServie(**{**base, **changes})


FIELD = load_registry()["demographie.nb_menages"]


@pytest.mark.parametrize(
    ("changes", "motif"),
    [
        (
            {"hors_couverture": True, "valeur": None, "etat": "out_of_coverage"},
            "commune_hors_couverture",
        ),
        ({"valeur": None, "etat": "not_reported"}, "donnee_absente"),
        ({"periode": "2019"}, "periodes_differentes"),
        ({"unite": "hectare"}, "unites_differentes"),
        ({"definition": ("demographie.nb_menages", "1.0")}, "definitions_differentes"),
    ],
)
def test_each_incompatibility_gives_its_reason_and_no_gap(changes, motif):
    verdict = compare_cell(FIELD, _served(), _served(**changes))
    assert (verdict.ecart, verdict.motif) == (None, motif)
    assert verdict.identique is False


def test_identical_at_display_precision_when_the_gap_rounds_to_zero():
    # C49: « Identique » means equal at display precision.
    field = dataclasses.replace(FIELD, fraction_digits=0)
    verdict = compare_cell(field, _served(valeur=10.2), _served(valeur=10.4))
    assert (verdict.ecart.valeur, verdict.identique) == (0, True)


def test_a_gap_that_rounds_to_one_is_not_identical():
    field = dataclasses.replace(FIELD, fraction_digits=0)
    verdict = compare_cell(field, _served(valeur=10.2), _served(valeur=11.2))
    assert (verdict.ecart.valeur, verdict.identique) == (1, False)


def test_without_rounding_a_raw_difference_is_not_identical():
    field = dataclasses.replace(FIELD, fraction_digits=None)
    verdict = compare_cell(field, _served(valeur=10.2), _served(valeur=10.4))
    assert verdict.identique is False


def test_precedence_follows_the_order_of_the_rule():
    everything = {"periode": "2019", "unite": "hectare", "definition": ("x",)}
    verdict = compare_cell(FIELD, _served(), _served(**everything))
    assert verdict.motif == "periodes_differentes"
    verdict = compare_cell(
        FIELD, _served(), _served(**everything, valeur=None, etat="not_available")
    )
    assert verdict.motif == "donnee_absente"
    verdict = compare_cell(
        FIELD,
        _served(hors_couverture=True, valeur=None, etat="out_of_coverage"),
        _served(valeur=None, etat="not_available"),
    )
    assert verdict.motif == "commune_hors_couverture"


RENT = ResultatLoyer.model_config["json_schema_extra"]["examples"][0]


def _rent(type_prediction: str, loyer: float = 12.3) -> dict:
    return {
        **RENT,
        "type_habitat": "maison",
        "loyer_m2": loyer,
        "annee": 2025,
        "trimestre_reference": 3,
        "type_prediction": type_prediction,
    }


@pytest.mark.parametrize("cle", ["loyer_m2_maison", "loyers_maison.loyer_m2"])
def test_rents_of_different_scales_are_different_definitions(cle):
    critere = _critere(
        [
            _commune("44109", loyer_m2_maison=12.3, loyers_maison=_rent("commune")),
            _commune("85047", loyer_m2_maison=10.8, loyers_maison=_rent("epci", 10.8)),
        ],
        cle,
    )
    reference, other = critere.cellules
    assert reference.periode == other.periode == "2025 T3"
    assert other.ecart is None
    assert other.motif_non_calcul == "definitions_differentes"


def test_rents_of_the_same_scale_are_compared_in_their_unit():
    critere = _critere(
        [
            _commune("44109", loyer_m2_maison=12.3, loyers_maison=_rent("commune")),
            _commune(
                "85047", loyer_m2_maison=10.8, loyers_maison=_rent("commune", 10.8)
            ),
        ],
        "loyer_m2_maison",
    )
    assert critere.cellules[1].ecart.model_dump() == {
        "valeur": -1.5,
        "unite": "euro_per_m2_month",
    }


@pytest.mark.parametrize(("other", "identical"), [("B1", True), ("C", False)])
def test_a_non_numeric_value_says_only_identical_or_not(other, identical):
    critere = _critere(
        [_commune("44109", zone_abc="B1"), _commune("85047", zone_abc=other)],
        "zone_abc",
    )
    cellule = critere.cellules[1]
    assert cellule.ecart is None
    assert cellule.motif_non_calcul == "non_numerique"
    assert cellule.identique_a_la_reference is identical
    assert critere.identiques is identical


def test_dpe_modes_are_compared_as_a_whole_without_a_gap():
    dpe = indicator_fixtures.dpe()
    critere = _critere(
        [
            _commune("44109", indicateurs_dpe=dpe),
            _commune("85047", indicateurs_dpe=copy.deepcopy(dpe)),
        ],
        "indicateurs_dpe.resultat.modes",
    )
    reference, other = critere.cellules
    assert reference.valeur == ["A"]
    assert reference.periode == other.periode == "calcul du 2026-09-08T10:00:00Z"
    assert other.motif_non_calcul == "non_numerique"
    assert other.identique_a_la_reference is True


def _dpe_sans_classe() -> dict:
    """A DPE block without any known class, as ``indicators_dpe.py`` serves it."""
    payload = indicator_fixtures.dpe()
    resultat = ("resultat",)
    for classe in "ABCDEFG":
        indicator_fixtures.change(
            payload, (*resultat, "distribution", "classes", classe), 0
        )
    indicator_fixtures.change(payload, (*resultat, "distribution", "inconnue"), 10)
    indicator_fixtures.change(payload, (*resultat, "population", "classes_connues"), 0)
    indicator_fixtures.change(payload, (*resultat, "modes"), [])
    indicator_fixtures.change(payload, (*resultat, "mediane_ordinale"), None)
    indicator_fixtures.change(
        payload,
        (*resultat, "part_fg_pct"),
        {"etat": "zero_denominator", "motif": "Aucune classe DPE connue"},
    )
    return payload


@pytest.mark.parametrize(
    "cle",
    ["indicateurs_dpe.resultat.modes", "indicateurs_dpe.resultat.mediane_ordinale"],
)
def test_an_empty_served_list_is_an_absence_with_its_declared_state(cle):
    # CR-02: no known class serves modes == [] and a null ordinal median; their
    # cause is the state declared on part_fg_pct, never a value.
    critere = _critere(
        [
            _commune("44109", indicateurs_dpe=indicator_fixtures.dpe()),
            _commune("85047", indicateurs_dpe=_dpe_sans_classe()),
        ],
        cle,
    )
    cellule = critere.cellules[1]
    assert cellule.etat == "zero_denominator"
    assert cellule.valeur is None
    assert cellule.ecart is None
    assert cellule.motif_non_calcul == "donnee_absente"
    assert cellule.identique_a_la_reference is False
    assert critere.identiques is False


def test_two_empty_lists_follow_the_absence_rule():
    # Two absences of the same declared state and period stay identical, like
    # two not_reported values: the fiche shows the same state for both (D-51).
    critere = _critere(
        [
            _commune("44109", indicateurs_dpe=_dpe_sans_classe()),
            _commune("85047", indicateurs_dpe=_dpe_sans_classe()),
        ],
        "indicateurs_dpe.resultat.modes",
    )
    reference, other = critere.cellules
    for cellule in (reference, other):
        assert (cellule.etat, cellule.valeur) == ("zero_denominator", None)
    assert other.motif_non_calcul == "donnee_absente"
    assert other.identique_a_la_reference is True
    assert critere.identiques is True


def test_an_empty_list_without_a_declared_sibling_state_is_an_undeclared_absence():
    row = {
        "indicateurs_dpe": {
            "resultat": {
                "etat": "value",
                "modes": [],
                "part_fg_pct": {"etat": "value", "valeur": 12.5},
            }
        }
    }
    critere = Critere(
        "indicateurs_dpe.resultat.modes",
        ("indicateurs_dpe", "resultat", "modes"),
        None,
        load_registry()["dpe.indicateurs.modes"],
    )

    assert _value(row, critere) == (None, None)


def test_the_schema_refuses_an_empty_list_served_as_a_value():
    with pytest.raises(ValidationError, match="never a served value"):
        CelluleComparaison(code_insee="44109", etat="value", valeur=[], periode=None)
    cellule = CelluleComparaison(
        code_insee="44109", etat="value", valeur=["A"], periode=None
    )
    assert cellule.valeur == ["A"]


def test_part_fg_is_a_rate_in_points():
    critere = _critere(
        [
            _commune("44109", indicateurs_dpe=indicator_fixtures.dpe()),
            _commune("85047", indicateurs_dpe=indicator_fixtures.dpe()),
        ],
        "indicateurs_dpe.resultat.part_fg_pct",
    )
    assert critere.cellules[1].ecart.model_dump() == {"valeur": 0.0, "unite": "point"}


def test_a_commune_out_of_coverage_keeps_its_column_without_any_gap():
    result = build_comparison(
        [
            _commune("44109", revenu_median=21000.0, annee_revenus=2021),
            _out_of_coverage("33063"),
            _commune("85047", revenu_median=21000.0, annee_revenus=2021),
        ]
    )
    assert [c.code_insee for c in result.communes] == ["44109", "33063", "85047"]
    assert result.communes[1].synthese is None
    assert result.criteres
    for critere in result.criteres:
        cellule = critere.cellules[1]
        assert (cellule.etat, cellule.valeur) == ("out_of_coverage", None)
        assert cellule.motif_non_calcul == "commune_hors_couverture"
        assert cellule.ecart is None
        assert critere.identiques is False


def test_a_reference_out_of_coverage_gives_no_gap_to_any_column():
    result = build_comparison(
        [_out_of_coverage("33063"), _commune("85047", revenu_median=1.0)]
    )
    assert result.criteres
    for critere in result.criteres:
        assert critere.cellules[1].motif_non_calcul == "commune_hors_couverture"


def test_identiques_needs_equal_values_states_and_periods_in_every_column():
    same = {"revenu_median": 21000.0, "annee_revenus": 2021}
    critere = _critere(
        [
            _commune("44109", **same),
            _commune("85047", **same),
            _commune("44001", **same),
        ],
        "revenu_median",
    )
    assert critere.identiques is True
    critere = _critere(
        [
            _commune("44109", **same),
            _commune("85047", **same),
            _commune("44001", revenu_median=21000.0, annee_revenus=2020),
        ],
        "revenu_median",
    )
    assert critere.identiques is False
    # Two absences of the same state and period are identical, never a gap.
    critere = _critere(
        [_commune("44109", annee_revenus=2021), _commune("85047", annee_revenus=2021)],
        "indice_gini",
    )
    assert critere.identiques is True
    assert critere.cellules[1].motif_non_calcul == "donnee_absente"


def _leaves(value: Any, path: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _leaves(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, (*path, f"[{index}]"))
    else:
        yield path


# Contract fields that date another value: served as its period, not compared.
PERIOD_FIELDS = {
    "demographie.annee",
    "revenus.annee",
    "vacance.annee",
    "fiscalite.annee",
    "loyers.annee",
    "equipements.annee",
    "loyers.prediction.annee",
    "loyers.prediction.trimestre_reference",
}


def test_every_valued_field_of_a_full_synthesis_is_a_criterion():
    registry = load_registry()
    row = {
        "revenu_median": 21000.0,
        "annee_revenus": 2021,
        "zone_abc": "B1",
        "loyer_m2_maison": 12.3,
        "loyers_maison": _rent("commune"),
        "indicateurs_dvf": _dvf_block(),
        "indicateurs_dpe": indicator_fixtures.dpe(),
    }
    communes = [_commune("44109", **row), _commune("85047", **row)]
    result = build_comparison(communes)
    served = communes[0].synthese.model_dump(mode="json")
    expected = {
        commune_field_id(path)
        for column, value in served.items()
        if column not in {"couverture", "etats"}
        for path in _leaves(value, (column,))
        if commune_field_id(path) in registry
    } - PERIOD_FIELDS
    assert expected <= {critere.field_id for critere in result.criteres}
    keys = [critere.cle for critere in result.criteres]
    assert len(keys) == len(set(keys))
    assert "annee_revenus" not in keys
    assert "indicateurs_dvf.residentiel_maison.courante.quantiles.p10" in keys
    assert "indicateurs_dpe.resultat.population.types_batiment.maison" in keys


@pytest.mark.parametrize(
    "codes",
    ["44109", "44109,85047,44001,44002,44003", "44109,44109", "44109,4410", "", None],
)
def test_codes_outside_two_to_four_distinct_insee_codes_are_refused(codes):
    with pytest.raises(ErreurApiError) as refused:
        comparison_codes(codes)
    assert refused.value.code == 422


def test_codes_are_normalized_and_kept_in_order():
    assert comparison_codes(" 2a004 ,44109") == ["2A004", "44109"]


def test_a_compared_cell_needs_a_gap_or_its_reason():
    cellule = CelluleComparaison(
        code_insee="44109", etat="value", valeur=1, periode="2021"
    )
    with pytest.raises(ValidationError):
        CritereComparaison(
            cle="nb_menages",
            field_id="demographie.nb_menages",
            unite="count",
            identiques=False,
            cellules=[
                cellule,
                cellule.model_copy(update={"identique_a_la_reference": False}),
            ],
        )


# --- D-15 review: a period unknown for a commune is never « the same » -------


def test_values_whose_vintage_is_unknown_on_both_sides_are_not_compared():
    critere = _critere(
        [
            _commune("44109", revenu_median=21000.0),
            _commune("85047", revenu_median=22480.0),
        ],
        "revenu_median",
    )
    reference, other = critere.cellules
    assert reference.periode is other.periode is None
    assert other.ecart is None
    assert other.motif_non_calcul == "periode_inconnue"
    assert critere.identiques is False


def test_a_vintage_unknown_on_one_side_is_periode_inconnue():
    critere = _critere(
        [
            _commune("44109", revenu_median=21000.0, annee_revenus=2021),
            _commune("85047", revenu_median=21000.0),
        ],
        "revenu_median",
    )
    assert critere.cellules[1].motif_non_calcul == "periode_inconnue"


def test_a_rent_without_its_year_is_periode_inconnue():
    critere = _critere(
        [
            _commune("44109", loyer_m2_maison=12.3, loyers_maison=_rent("commune")),
            _commune("85047", loyer_m2_maison=10.8),
        ],
        "loyer_m2_maison",
    )
    other = critere.cellules[1]
    assert other.periode is None
    assert other.motif_non_calcul == "periode_inconnue"


def test_a_dvf_value_without_its_window_is_periode_inconnue():
    # A published DVF price always has its window (IndicateursDvf validator):
    # the rule is exercised on the served values themselves.
    dated = _served(periode="[2024-01-01, 2025-01-01[", periode_par_commune=True)
    undated = _served(periode=None, periode_par_commune=True)
    verdict = compare_cell(FIELD, dated, undated)
    assert (verdict.ecart, verdict.motif) == (None, "periode_inconnue")
    verdict = compare_cell(FIELD, undated, undated)
    assert (verdict.ecart, verdict.motif) == (None, "periode_inconnue")


def test_an_unknown_provenance_period_shared_by_the_instance_is_still_compared():
    critere = _critere(
        [
            _commune("44109", nb_mutations_12m=40),
            _commune("85047", nb_mutations_12m=55),
        ],
        "nb_mutations_12m",
    )
    reference, other = critere.cellules
    assert reference.periode is other.periode is None
    assert other.ecart.model_dump() == {"valeur": 15, "unite": "count"}
    assert other.motif_non_calcul is None


def test_dvf_windows_and_vintages_are_dated_commune_by_commune():
    communes = [
        _commune("44109", revenu_median=1.0, indicateurs_dvf=_dvf_block()),
        _commune("85047", revenu_median=2.0, indicateurs_dvf=_dvf_block()),
    ]
    result = build_comparison(communes)
    motifs = {c.cle: c.cellules[1].motif_non_calcul for c in result.criteres}
    assert motifs["revenu_median"] == "periode_inconnue"
    assert motifs[MEDIANE] is None


# --- 04-07: the same rule for addresses (CMP-01, D-116) --------------------------

TEXTE = load_registry()["adresse.risques.argiles_au_point"]


def _texte(**changes) -> ValeurServie:
    base = {
        "etat": "value",
        "valeur": "Exposition moyenne",
        "periode": "2026-10-05",
        "unite": "none",
        "definition": ("adresse.risques.argiles_au_point", "au_point"),
    }
    return ValeurServie(**{**base, **changes})


@pytest.mark.parametrize(
    ("other", "categorie"),
    [("Exposition moyenne", "identique"), ("Exposition forte", "different")],
)
def test_a_categorical_value_gets_a_served_verdict_without_a_figure(other, categorie):
    verdict = compare_cell(TEXTE, _texte(), _texte(valeur=other), categoriel=True)
    assert (verdict.ecart, verdict.motif, verdict.categorie) == (None, None, categorie)
    assert verdict.identique is (categorie == "identique")


def test_the_commune_comparator_keeps_non_numerique_by_default():
    verdict = compare_cell(TEXTE, _texte(), _texte(valeur="Exposition forte"))
    assert (verdict.motif, verdict.categorie) == ("non_numerique", None)


def test_a_categorical_value_is_never_compared_across_periods_or_definitions():
    for changes, motif in (
        ({"periode": "2026-10-04"}, "periodes_differentes"),
        (
            {"definition": ("adresse.risques.argiles_au_point", "x")},
            "definitions_differentes",
        ),
        ({"valeur": None, "etat": "temporary_error"}, "donnee_absente"),
    ):
        verdict = compare_cell(TEXTE, _texte(), _texte(**changes), categoriel=True)
        assert (verdict.motif, verdict.categorie) == (motif, None)


def test_an_address_out_of_coverage_names_the_address():
    hors = _served(hors_couverture=True, valeur=None, etat="out_of_coverage")
    verdict = compare_cell(
        FIELD, _served(), hors, hors_couverture="adresse_hors_couverture"
    )
    assert (verdict.ecart, verdict.motif, verdict.identique) == (
        None,
        "adresse_hors_couverture",
        False,
    )
    # Two absences of the same state and period stay identical (D-116).
    absente = _served(valeur=None, etat="not_available")
    verdict = compare_cell(FIELD, absente, absente, categoriel=True)
    assert (verdict.motif, verdict.identique) == ("donnee_absente", True)


# --- 04-10: documents compared, same rule (CMP-02, R06-09/7, D-116) -------------

CLASSE = load_registry()["dpe.document.etiquette_dpe"]
CONSO = load_registry()["dpe.document.conso_ep"]


def _document(**changes) -> ValeurServie:
    base = {
        "etat": "value",
        "valeur": "D",
        "periode": None,
        "unite": "none",
        "definition": ("dpe.document.etiquette_dpe",),
        "methode": "actuelle",
    }
    return ValeurServie(**{**base, **changes})


def test_two_methods_of_calculation_never_compare_not_even_the_same_class():
    verdict = compare_cell(
        CLASSE, _document(methode="ancienne"), _document(), categoriel=True
    )
    assert (verdict.ecart, verdict.motif, verdict.categorie) == (
        None,
        "methodes_differentes",
        None,
    )
    assert verdict.identique is False


def test_two_methods_never_get_a_gap_on_a_figure():
    reference = _document(valeur=17830.4, unite="kwh_ep_per_year", methode="ancienne")
    verdict = compare_cell(
        CONSO, reference, _document(valeur=12000.4, unite=reference.unite)
    )
    assert (verdict.ecart, verdict.motif) == (None, "methodes_differentes")


@pytest.mark.parametrize("methode", [METHODE_INCONNUE, None])
def test_an_unestablished_method_gives_no_verdict_and_is_never_identical(methode):
    verdict = compare_cell(
        CLASSE, _document(), _document(methode=methode), categoriel=True
    )
    assert (verdict.motif, verdict.categorie, verdict.identique) == (
        "methode_indeterminee",
        None,
        False,
    )


def test_the_same_method_gets_the_served_verdict_and_gap():
    verdict = compare_cell(CLASSE, _document(), _document(valeur="C"), categoriel=True)
    assert (verdict.motif, verdict.categorie) == (None, "different")
    reference = _document(valeur=17830.4, unite="kwh_ep_per_year")
    gap = compare_cell(
        CONSO, reference, _document(valeur=12000.4, unite=reference.unite)
    )
    assert gap.ecart.model_dump() == {"valeur": -5830, "unite": "kwh_ep_per_year"}


def test_an_absent_document_value_says_donnee_absente_before_the_method():
    absente = _document(valeur=None, etat="not_reported", methode=None)
    verdict = compare_cell(
        CLASSE, _document(methode="ancienne"), absente, categoriel=True
    )
    assert verdict.motif == "donnee_absente"
    # Two absences of the same state stay identical, whatever the documents (D-116).
    verdict = compare_cell(CLASSE, absente, absente, categoriel=True)
    assert (verdict.motif, verdict.identique) == ("donnee_absente", True)


def test_a_withheld_value_is_never_compared_nor_identical():
    masquee = _document(
        valeur=None, etat="suppressed", motif="complement_present", non_affichee=True
    )
    for reference, cellule in ((_document(), masquee), (masquee, masquee)):
        verdict = compare_cell(CLASSE, reference, cellule, categoriel=True)
        assert (verdict.motif, verdict.identique) == (
            "adresse_declaree_non_affichee",
            False,
        )


def test_communes_and_addresses_carry_no_method_their_verdicts_unchanged():
    # No method on either side: the rule of 03-09 and 04-07, untouched.
    assert _served().methode is None and _served().non_affichee is False
    verdict = compare_cell(FIELD, _served(), _served(valeur=_served().valeur + 1))
    assert verdict.motif is None and verdict.ecart is not None


# C32 (Ianis, 2026-10-10, WINDOWS n° 92) and C34 (WINDOWS n° 95): the six flat
# dvf columns computed on the same 12 months take the window of those months;
# no window known, they keep the period of the source (C14). A column of
# another source without a vintage keeps its own period.
_COLONNES_12_MOIS = (
    "prix_m2_median_maison",
    "prix_m2_median_appartement",
    "prix_m2_median",
    "nb_mutations_12m",
    "nb_mutations_12m_maison",
    "nb_mutations_12m_appartement",
)


@pytest.mark.parametrize(
    ("fenetre", "attendue"),
    [
        ("2024-12-31 → 2025-12-31", "2024-12-31 → 2025-12-31"),
        (None, "2021-01-02 → 2025-12-31"),
    ],
)
def test_flat_dvf_columns_are_dated_by_their_twelve_months(fenetre, attendue):
    colonnes = dict.fromkeys(_COLONNES_12_MOIS, 2000) | {"nb_monuments": 3}
    communes = [_commune("44109", **colonnes), _commune("85191", **colonnes)]
    periodes = dict.fromkeys(COMMUNE_SOURCE_KEYS) | {
        "dvf": "2021-01-02 → 2025-12-31",
        "monuments": "2026-03-01",
    }
    result = build_comparison(
        communes, source_periods=periodes, dvf_median_window=fenetre
    )
    criteres = {critere.cle: critere for critere in result.criteres}
    for cle in _COLONNES_12_MOIS:
        assert {c.periode for c in criteres[cle].cellules} == {attendue}, cle
    assert {c.periode for c in criteres["nb_monuments"].cellules} == {"2026-03-01"}


def test_closed_gate_blocks_give_no_indicator_criterion():
    # Todo 2026-11-01: blocks the instance does not publish are an explicit
    # absence, never a criterion; the flat columns stay the compared values.
    gated = {
        "etat": "not_qualified",
        "motif": "indicateurs_communaux_non_publies",
    }
    communes = [
        _commune(
            code, prix_m2_median=3000.0, indicateurs_dvf=gated, indicateurs_dpe=gated
        )
        for code in ("44109", "85047")
    ]
    result = build_comparison(communes)
    cles = {critere.cle for critere in result.criteres}
    assert "prix_m2_median" in cles
    assert not {cle for cle in cles if cle.startswith("indicateurs_")}
