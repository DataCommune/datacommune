"""Pure documentary witnesses; no source, database or publication access."""

from datetime import date, datetime
from itertools import product

import pytest
from pydantic import ValidationError

from app.commune.indicators_dpe import CanonicalDpeDocument, calculate_dpe_documents
from tests.oracles.commune_indicators import ordinal_summary

AS_OF = date(2026, 9, 9)


def document(key="synthetic", energy="A", **changes):
    fields = dict(
        document_key=key,
        energy_class=energy,
        building_type="maison",
        source_state="unknown",
        regimes=frozenset(),
        is_tombstone=False,
        established_at=None,
        expires_at=None,
        construction_year=None,
        replacement_state="not_checked",
        graph_issues=frozenset(),
        date_issues=frozenset(),
        revision_status="unverified",
    )
    return CanonicalDpeDocument(**(fields | changes))


def calculate(rows):
    return calculate_dpe_documents(rows, as_of_date=AS_OF)


@pytest.mark.parametrize(
    ("labels", "modes", "ordinal"),
    [
        ("AAAADDDGG", ["A"], "D"),
        ("AAGG", ["A", "G"], "A"),
    ],
)
def test_mode_and_ordinal_are_distinct(labels, modes, ordinal):
    result = calculate(document(str(i), c) for i, c in enumerate(labels)).result
    assert result.modes == modes
    assert result.mediane_ordinale == ordinal
    assert result.part_fg_pct.valeur == pytest.approx(
        100 * labels.count("G") / len(labels)
    )


@pytest.mark.parametrize("n", [0, 1, 3])
def test_no_known_class_has_no_numeric_share(n):
    calc = calculate(document(str(i), None) for i in range(n))
    assert calc.result.population.total == calc.result.distribution.inconnue == n
    assert calc.result.distribution.classes == dict.fromkeys("ABCDEFG", 0)
    assert calc.result.modes == [] and calc.result.mediane_ordinale is None
    assert calc.result.part_fg_pct.model_dump() == {
        "etat": "zero_denominator",
        "motif": "Aucune classe DPE connue",
    }
    assert calc.construction_known == 0 and calc.construction_unknown == n
    assert calc.construction_median is None


def test_known_denominator_and_construction_are_separate():
    calc = calculate(
        [
            document("1", "F", construction_year=1900),
            document("2", "A", construction_year=2001),
            document("3", None),
        ]
    )
    assert calc.result.part_fg_pct.valeur == 50
    assert calc.result.population.total == 3
    assert (
        calc.construction_known,
        calc.construction_unknown,
        calc.construction_median,
    ) == (2, 1, 1900)


def test_source_cohort_overlap_never_duplicates_documents():
    multi = document("one", "G", source_state="present", regimes={"existant", "neuf"})
    calc = calculate([multi, multi, document("two", "A", regimes={"existant"})])
    assert calc.duplicate_inputs == 1
    assert calc.result.population.total == 2
    assert calc.result.population.regimes == {"existant": 2, "neuf": 1, "inconnu": 0}
    assert calc.result.population.source_inconnue == 1
    assert calc.result.population.revision_non_verifiee == 2
    assert calc.result.part_fg_pct.valeur == 50


def test_separate_observation_regimes_require_an_upstream_canonical_adapter():
    with pytest.raises(ValueError, match="Conflicting canonical"):
        calculate(
            [
                document("same", regimes={"existant"}),
                document("same", regimes={"neuf"}),
            ]
        )


def test_building_types_follow_existing_contract_without_housing_identity():
    calc = calculate(
        [
            document("1", building_type="appartement"),
            document("2", building_type="immeuble"),
            document("3", building_type="autre"),
            document("4", building_type=None),
        ]
    )
    assert calc.result.population.types_batiment == {
        "maison": 0,
        "appartement": 1,
        "autre": 2,
        "inconnu": 1,
    }


def test_a_new_reference_date_requires_an_explicit_new_calculation():
    row = document(established_at=AS_OF, expires_at=AS_OF)
    first = calculate([row])
    later = calculate_dpe_documents([row], as_of_date=date(2026, 9, 10))
    assert first.result.population.validite["not_expired_by_date"] == 1
    assert later.result.population.validite["expired"] == 1
    assert first.as_of_date == AS_OF


@pytest.mark.parametrize(
    "change",
    [
        {"energy_class": "G"},
        {"regimes": {"neuf"}},
        {"source_state": "present"},
        {"is_tombstone": True},
        {"graph_issues": {"missing_target"}},
        {"established_at": AS_OF},
        {"construction_year": 2020},
    ],
)
def test_conflicting_duplicates_fail_without_private_identity(change):
    key = "SYNTHETIC_PRIVATE_ID_SENTINEL"
    with pytest.raises(ValueError, match="Conflicting canonical") as exc:
        calculate([document(key), document(key, **change)])
    assert key not in str(exc.value)


def test_removed_inputs_are_excluded_once_and_cannot_win_conflicts():
    calc = calculate(
        [
            document("1", "G", is_tombstone=True),
            document("2", "G", source_state="absent_from_complete_scope"),
            document("3", "G", source_state="explicitly_deactivated"),
            document("4", "A"),
        ]
    )
    assert calc.excluded_removed == 3
    assert calc.result.population.total == 1 and calc.result.part_fg_pct.valeur == 0
    with pytest.raises(ValueError):
        calculate(
            [
                document("same", is_tombstone=True),
                document("same", "G", is_tombstone=True),
            ]
        )


@pytest.mark.parametrize(
    ("start", "end", "flags", "expected"),
    [
        (date(2020, 1, 1), AS_OF, set(), "not_expired_by_date"),
        (date(2020, 1, 1), date(2026, 9, 10), set(), "not_expired_by_date"),
        (date(2020, 1, 1), date(2026, 9, 8), set(), "expired"),
        (AS_OF, AS_OF, set(), "not_expired_by_date"),
        (date(2026, 9, 10), date(2030, 1, 1), set(), "future_establishment"),
        (date(2026, 9, 10), AS_OF, set(), "inconsistent_dates"),
        (None, AS_OF, {"invalid_establishment_date"}, "inconsistent_dates"),
        (None, AS_OF, set(), "unknown"),
    ],
)
def test_calendar_states_keep_fixed_date_and_inconsistent_priority(
    start, end, flags, expected
):
    calc = calculate(
        [
            document(
                established_at=start,
                expires_at=end,
                date_issues=flags,
                replacement_state="no_successor_known",
            )
        ]
    )
    assert calc.as_of_date == AS_OF
    assert calc.result.population.validite[expected] == 1
    assert calc.restricted_date_and_no_successor_known == int(
        expected == "not_expired_by_date"
    )
    assert (
        calc.result.date_etablissement_min
        == calc.result.date_etablissement_max
        == start
    )
    assert calc.result.dates_etablissement_inconnues == int(start is None)


def test_graph_reserves_overlap_without_claiming_certification():
    calc = calculate(
        [
            document(
                "1",
                "F",
                graph_issues={"cycle", "ambiguous_target"},
                replacement_state="ambiguous",
            ),
            document(
                "2", "G", graph_issues={"missing_target"}, replacement_state="replaced"
            ),
            document(
                "3",
                "A",
                graph_issues={"missing_target", "truncated"},
                replacement_state="no_successor_known",
                established_at=AS_OF,
                expires_at=AS_OF,
            ),
        ]
    )
    assert calc.result.population.total == 3
    assert calc.graph_issues == {
        "cycle": 1,
        "fork": 0,
        "missing_target": 2,
        "truncated": 1,
        "ambiguous_target": 1,
    }
    assert calc.restricted_date_and_no_successor_known == 1
    assert calc.result.population.revision_non_verifiee == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"energy_class": "H"},
        {"revision_status": "verified_attestation"},
        {"construction_year": True},
        {"construction_year": float("nan")},
        {"source_state": "invented"},
        {"graph_issues": {"invented"}},
        {"established_at": AS_OF, "date_issues": {"invalid_establishment_date"}},
        {"document_key": " "},
        {"is_tombstone": 1},
    ],
)
def test_invalid_or_unsupported_projection_fails_closed(changes):
    with pytest.raises(ValidationError):
        document(**changes)


def test_public_result_and_input_repr_do_not_leak_identity():
    row = document("SYNTHETIC_PRIVATE_ID_SENTINEL")
    assert row.document_key not in repr(row)
    assert row.document_key not in str(row.model_dump())
    assert row.document_key not in str(calculate([row]))
    with pytest.raises(TypeError):
        calculate([row.model_dump()])
    with pytest.raises(ValidationError):
        calculate_dpe_documents([row], as_of_date=datetime(2026, 9, 9))


def test_625_small_distributions_against_independent_literal_rank_oracle():
    for labels in product([None, "A", "D", "F", "G"], repeat=4):
        expected = ordinal_summary(list(labels))
        result = calculate(document(str(i), c) for i, c in enumerate(labels)).result
        assert result.population.total == expected["total"]
        assert result.population.classes_connues == expected["known"]
        assert result.distribution.inconnue == expected["unknown"]
        assert result.modes == expected["modes"]
        assert result.mediane_ordinale == expected["median"]
