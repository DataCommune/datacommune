"""Typed absences of the flat /v1/commune columns (D-47), pure rules.

The rules read the served row and the hosting of each source only: these
tests build both by hand, no database, no HTTP.
"""

import pytest
from pydantic import ValidationError

from app.common.schemas import DatasetHosting
from app.common.semantic_contract import (
    ALLOWED_OUT_OF_COVERAGE_REASONS,
    load_registry,
)
from app.commune.absences import (
    COLUMN_ABSENCE_RULES,
    EtatColonne,
    column_states,
)
from app.commune.schemas import (
    COMMUNE_FIELD_SOURCES,
    COMMUNE_SOURCE_KEYS,
    ResultatCommuneSynthese,
    apply_commune_coverage,
    commune_field_id,
)
from app.dvf.couverture import SEUIL_ND_COMMUNE

pytestmark = pytest.mark.unit

HOSTED = DatasetHosting(etat="heberge")


def _coverage(**out_of_coverage: str) -> dict[str, DatasetHosting]:
    """Every source hosted, except those named with their D-24 reason."""
    return {
        source: (
            DatasetHosting(etat="hors_couverture", motif=out_of_coverage[source])
            if source in out_of_coverage
            else HOSTED
        )
        for source in COMMUNE_SOURCE_KEYS
    }


def _row(**values):
    return {
        "code_insee": "44991",
        "population": 100,
        "annee_demographie": 2022,
        **values,
    }


# --- DVF median under the publication threshold (COM-02 edge) ---------------


def test_median_at_the_threshold_is_served_without_state():
    row = _row(
        prix_m2_median_appartement=3100,
        nb_mutations_12m_appartement=SEUIL_ND_COMMUNE,
    )
    assert "prix_m2_median_appartement" not in column_states(row, _coverage())


def test_median_under_the_threshold_is_insufficient_sample_with_its_count():
    row = _row(
        prix_m2_median_appartement=None,
        nb_mutations_12m_appartement=SEUIL_ND_COMMUNE - 1,
    )
    state = column_states(row, _coverage())["prix_m2_median_appartement"]
    assert state.etat == "insufficient_sample"
    assert f"{SEUIL_ND_COMMUNE - 1} mutations d'appartements" in state.motif
    assert f"seuil de publication de {SEUIL_ND_COMMUNE}" in state.motif


def test_median_without_any_count_is_not_reported():
    row = _row(prix_m2_median_appartement=None, nb_mutations_12m_appartement=None)
    state = column_states(row, _coverage())["prix_m2_median_appartement"]
    assert state.etat == "not_reported"


def test_non_hosted_source_is_out_of_coverage_with_its_reason():
    row = _row(prix_m2_median_appartement=None, nb_mutations_12m_appartement=None)
    state = column_states(row, _coverage(dvf="dataset_non_heberge"))[
        "prix_m2_median_appartement"
    ]
    assert state == EtatColonne(etat="out_of_coverage", motif="dataset_non_heberge")


# --- EtatColonne: an absence carries no value -------------------------------


def test_absence_refuses_a_value_key():
    with pytest.raises(ValidationError):
        EtatColonne.model_validate(
            {"etat": "insufficient_sample", "motif": "4 mutations", "valeur": 3100}
        )


def test_out_of_coverage_needs_a_d24_reason_key():
    with pytest.raises(ValidationError):
        EtatColonne(etat="out_of_coverage", motif="hors du périmètre")
    with pytest.raises(ValidationError):
        EtatColonne(etat="not_reported", motif="dataset_non_heberge")


def test_suppressed_is_not_a_column_state():
    """Gate D-47: no import keeps the statistical-secrecy mark."""
    with pytest.raises(ValidationError):
        EtatColonne(etat="suppressed", motif="Secret statistique")


# --- the single projection -------------------------------------------------


def test_projection_serves_the_state_and_omits_the_field_when_empty():
    under = apply_commune_coverage(
        _row(prix_m2_median_appartement=None, nb_mutations_12m_appartement=4),
        _coverage(),
    )
    served = ResultatCommuneSynthese(**under).model_dump(mode="json")
    assert served["prix_m2_median_appartement"] is None
    assert served["etats"]["prix_m2_median_appartement"]["etat"] == (
        "insufficient_sample"
    )
    at = apply_commune_coverage(
        _row(prix_m2_median_appartement=3100, nb_mutations_12m_appartement=5),
        _coverage(),
    )
    assert "prix_m2_median_appartement" not in (at["etats"] or {})
    # No null column, no field: the served shape is unchanged for such a row.
    assert "etats" not in ResultatCommuneSynthese(code_insee="44991").model_dump(
        mode="json"
    )


def test_every_rule_names_the_source_of_its_column():
    for column, rule in COLUMN_ABSENCE_RULES.items():
        assert COMMUNE_FIELD_SOURCES[column] == rule.source, column


# --- the complete table (task 3): every flat column is qualified -----------

# Flat columns of the synthesis whose value has a contract entry.
FLAT_COLUMNS = frozenset(
    column
    for column in ResultatCommuneSynthese.model_fields
    if commune_field_id((column,)) in load_registry()
)


def test_every_flat_column_with_a_field_id_has_a_rule():
    assert len(FLAT_COLUMNS) == 41
    assert set(COLUMN_ABSENCE_RULES) == FLAT_COLUMNS


@pytest.mark.parametrize("column", sorted(FLAT_COLUMNS))
def test_non_hosted_source_gives_out_of_coverage_with_the_exact_reason(column):
    source = COMMUNE_FIELD_SOURCES[column]
    for motif in ALLOWED_OUT_OF_COVERAGE_REASONS:
        states = column_states(_row(**{column: None}), _coverage(**{source: motif}))
        assert states.get(column) == EtatColonne(etat="out_of_coverage", motif=motif)


@pytest.mark.parametrize(
    ("median", "count"),
    [
        ("prix_m2_median", "nb_mutations_12m"),
        ("prix_m2_median_maison", "nb_mutations_12m_maison"),
        ("prix_m2_median_appartement", "nb_mutations_12m_appartement"),
    ],
)
def test_each_dvf_median_follows_the_threshold(median, count):
    at = column_states(_row(**{median: 2900, count: SEUIL_ND_COMMUNE}), _coverage())
    assert median not in at
    under = column_states(
        _row(**{median: None, count: SEUIL_ND_COMMUNE - 1}), _coverage()
    ).get(median)
    assert under is not None and under.etat == "insufficient_sample"
    assert str(SEUIL_ND_COMMUNE - 1) in under.motif
    missing = column_states(_row(**{median: None, count: None}), _coverage()).get(
        median
    )
    assert missing is not None and missing.etat == "not_reported"


# (column, another column of the same source row: its vintage or a sibling)
VINTAGED = [
    ("nb_menages", "annee_demographie"),
    ("densite", "annee_demographie"),
    ("revenu_median", "annee_revenus"),
    ("taux_pauvrete", "annee_revenus"),
    ("indice_gini", "annee_revenus"),
    ("part_imposee", "annee_revenus"),
    ("taux_vacance", "annee_vacance"),
    ("nb_vacants_plus_2ans", "annee_vacance"),
    ("taux_tfpb", "annee_fiscalite"),
    ("taux_th_rs", "annee_fiscalite"),
    ("taux_teom", "annee_fiscalite"),
    ("nom_epci", "annee_fiscalite"),
    ("nb_equipements", "annee_equipements"),
    ("zone_abc", "zone_abc_reclassement"),
    ("surface_pm_ha", "nb_proprietaires_pm"),
]


@pytest.mark.parametrize(("column", "vintage"), VINTAGED)
def test_null_under_a_present_source_row_is_not_available(column, vintage):
    present = column_states(_row(**{column: None, vintage: 2021}), _coverage()).get(
        column
    )
    assert present is not None and present.etat == "not_available"
    # Nothing of the source in the row: no row proven for the commune.
    source = COMMUNE_FIELD_SOURCES[column]
    empty = dict.fromkeys(c for c, s in COMMUNE_FIELD_SOURCES.items() if s == source)
    absent = column_states(_row(**empty), _coverage()).get(column)
    assert absent is not None and absent.etat == "not_reported"


def test_a_rent_is_not_available_only_when_its_own_type_row_exists():
    """annee_loyers is null when the types differ: the type object is the proof."""
    apartment = {"annee": 2022, "loyer_m2": None}
    own = column_states(
        _row(loyer_m2_appartement=None, loyers_appartement=apartment), _coverage()
    )
    assert own["loyer_m2_appartement"].etat == "not_available"
    other = column_states(
        _row(
            loyer_m2_appartement=None,
            loyers_appartement=None,
            loyers_maison={"annee": 2021, "loyer_m2": 12},
            annee_loyers=2021,
        ),
        _coverage(),
    )
    assert other["loyer_m2_appartement"].etat == "not_reported"


def test_rent_vintage_null_with_both_types_is_not_available():
    states = column_states(
        _row(
            annee_loyers=None,
            loyers_appartement={"annee": 2022},
            loyers_maison={"annee": 2021},
        ),
        _coverage(),
    )
    assert states["annee_loyers"].etat == "not_available"


def test_fg_share_without_known_class_is_zero_denominator():
    block = {"resultat": {"part_fg_pct": {"etat": "zero_denominator", "motif": "x"}}}
    from_block = column_states(
        _row(dpe_passoires_pct=None, indicateurs_dpe=block), _coverage()
    )
    assert from_block["dpe_passoires_pct"].etat == "zero_denominator"
    # Without the block: diagnostics exist (a median year) but no known class.
    from_row = column_states(
        _row(dpe_passoires_pct=None, annee_construction_mediane=1975), _coverage()
    )
    assert from_row["dpe_passoires_pct"].etat == "zero_denominator"
    nothing = column_states(_row(dpe_passoires_pct=None), _coverage())
    assert nothing["dpe_passoires_pct"].etat == "not_reported"


def test_no_rule_produces_suppressed():
    """Gate D-47: the state must be provable row by row, and no import keeps it."""
    row = _row(**dict.fromkeys(FLAT_COLUMNS))
    for coverage in (_coverage(), _coverage(revenus="dataset_non_heberge")):
        states = column_states(row, coverage)
        assert set(states) == FLAT_COLUMNS
        assert {state.etat for state in states.values()} <= {
            "out_of_coverage",
            "insufficient_sample",
            "not_available",
            "not_reported",
            "zero_denominator",
        }


def _producible(rule) -> set[str]:
    states = {"out_of_coverage", "not_reported"}
    if rule.not_available:
        states.add("not_available")
    if rule.sample:
        states |= {"insufficient_sample", "not_available"}
    if rule.zero_denominator:
        states.add("zero_denominator")
    return states


@pytest.mark.parametrize("column", sorted(FLAT_COLUMNS))
def test_the_contract_allows_every_state_the_rule_can_produce(column):
    field = load_registry()[commune_field_id((column,))]
    missing = _producible(COLUMN_ABSENCE_RULES[column]) - set(field.allowed_states)
    assert missing == set(), (column, missing)
