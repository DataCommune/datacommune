"""Independent synthetic oracles for the laboratory-only DVF calculator."""

from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from fractions import Fraction
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.commune import indicators_dvf as dvf

pytestmark = pytest.mark.unit


def row(identity="sale-1", **changes):
    return dvf.CanonicalMutation(
        **{
            "id_mutation": identity,
            "code_departement": "44",
            "code_commune": "44001",
            "date_mutation": date(2025, 5, 1),
            "classe_mutation": "residentiel_maison",
            "mono_local": True,
            "est_vente": True,
            "hors_stats_vefa": False,
            "nb_communes": 1,
            "surface_bati_totale": Decimal("100"),
            "valeur_fonciere": Decimal("100000"),
            **changes,
        }
    )


def stock(rows):
    return dvf.CanonicalStock(
        input_contract="mutations_grain.complete-reconstruction.v1",
        source_generation=None,
        source_snapshot_sha256="a" * 64,
        reconstruction_sha256="b" * 64,
        mutations=tuple(rows),
    )


def calculate(rows, **changes):
    return dvf.calculate_dvf(
        stock(rows),
        **{
            "communes": ("44001", "85001"),
            "departments": ("44", "85"),
            "end_exclusive": date(2026, 1, 1),
            "observed_at": datetime(2026, 9, 9, tzinfo=UTC),
            **changes,
        },
    )


def cell(result, commune="44001", kind="residentiel_maison", period="current"):
    return next(
        c
        for c in result.cells
        if (c.commune, c.kind, c.period) == (commune, kind, period)
    )


@pytest.mark.parametrize("n", [0, 4, 5])
def test_zero_suppressed_and_threshold_have_distinct_results(n):
    result = calculate(
        [row(str(i), valeur_fonciere=Decimal(100001 + i)) for i in range(n)]
    )
    current = cell(result)
    assert len(result.cells) == 8
    assert current.count == n
    assert current.median_eur_m2 == (Fraction(100003, 100) if n == 5 else None)
    assert (
        current.median_state
        == {0: "empty_sample", 4: "insufficient_sample", 5: "value"}[n]
    )
    assert current.quantiles_status == result.trend_status == "pending"
    assert not hasattr(current, "p10") and not hasattr(result, "variation_pct")
    assert result.source_generation is None and result.publication_authorized is False
    assert cell(result, commune="85001").count == 0


def test_disjoint_boundaries_are_not_derived_from_maximum_observed_date():
    dates = [
        date(2023, 12, 31),
        date(2024, 1, 1),
        date(2024, 12, 31),
        date(2025, 1, 1),
        date(2025, 12, 31),
        date(2026, 1, 1),
    ]
    result = calculate([row(str(i), date_mutation=d) for i, d in enumerate(dates)])
    assert cell(result, period="previous").count == 2
    assert cell(result).count == 2
    assert cell(result).start_inclusive == date(2025, 1, 1)
    assert cell(result).end_exclusive == date(2026, 1, 1)


def test_leap_day_windows_follow_calendar_years_not_365_days():
    result = calculate(
        [
            row(str(i), date_mutation=d)
            for i, d in enumerate(
                [
                    date(2022, 2, 27),
                    date(2022, 2, 28),
                    date(2023, 2, 28),
                    date(2024, 2, 28),
                    date(2024, 2, 29),
                ]
            )
        ],
        end_exclusive=date(2024, 2, 29),
    )
    assert cell(result, period="previous").count == 1
    assert cell(result).count == 2
    assert cell(result, period="previous").start_inclusive == date(2022, 2, 28)


def test_types_are_separate_and_even_median_uses_exact_rationals():
    houses = [
        row(str(i), valeur_fonciere=Decimal(value), surface_bati_totale=Decimal("3"))
        for i, value in enumerate([1, 2, 3, 4, 5, 6])
    ]
    flats = [
        row(
            "flat-" + str(i),
            classe_mutation="residentiel_appartement",
            valeur_fonciere=Decimal("123456.789"),
            surface_bati_totale=Decimal("7"),
        )
        for i in range(5)
    ]
    with localcontext() as context:
        context.prec = 2
        result = calculate(houses + flats)
    assert cell(result).median_eur_m2 == Fraction(7, 6)
    assert cell(result, kind="residentiel_appartement").median_eur_m2 == Fraction(
        123456789, 7000
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"est_vente": False},
        {"est_vente": None},
        {"hors_stats_vefa": True},
        {"hors_stats_vefa": None},
        {"nb_communes": 2},
        {"nb_communes": None},
        {"surface_bati_totale": Decimal(0)},
        {"surface_bati_totale": None},
        {"valeur_fonciere": Decimal(-1)},
        {"valeur_fonciere": None},
        {"date_mutation": None},
        {"code_commune": None},
        {"code_commune": "44002"},
        {"classe_mutation": "mixte", "mono_local": False},
    ],
)
def test_canonical_exclusions_do_not_change_the_reference_denominator(changes):
    result = calculate([row(**changes)])
    assert len(result.cells) == 8 and all(c.count == 0 for c in result.cells)


def test_identical_duplicates_count_once_but_conflicts_fail_before_filtering():
    good = row()
    assert cell(calculate([good, good])).count == 1
    # The conflicting row would otherwise be removed before aggregation.
    with pytest.raises(
        dvf.DvfCalculationRefused, match="conflicting_canonical_mutation"
    ):
        calculate([good, row(nb_communes=2)])
    with pytest.raises(dvf.DvfCalculationRefused):
        calculate([good, row(code_departement="85", code_commune="85001")])


@pytest.mark.parametrize("identity", [" sale-1", "sale-1 ", "sale-1\n", "sale 1", "\t"])
def test_noncanonical_identity_cannot_turn_four_mutations_into_five(identity):
    with pytest.raises(ValidationError):
        row(identity)


def test_raw_dispositions_and_unattested_envelopes_are_not_input_types():
    with pytest.raises(dvf.DvfCalculationRefused):
        dvf.calculate_dvf(
            [{"id_mutation": "raw", "code_type_local": "1"}],
            communes=("44001",),
            departments=("44",),
            end_exclusive=date(2026, 1, 1),
            observed_at=datetime(2026, 9, 9, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        dvf.CanonicalStock(mutations=(row(),))
    with pytest.raises(ValidationError):
        dvf.CanonicalMutation(**{**row().model_dump(), "numero_disposition": "1"})


@pytest.mark.parametrize(
    "changes",
    [
        {"surface_bati_totale": 1.1},
        {"valeur_fonciere": "12.00"},
        {"valeur_fonciere": Decimal("NaN")},
        {"valeur_fonciere": Decimal("Infinity")},
        {"nb_communes": True},
        {"est_vente": 1},
        {"date_mutation": datetime(2025, 1, 1)},
        {"classe_mutation": "unknown"},
        {"mono_local": False},
        {"code_commune": "85001"},
        # A 75 commune filed under 44, a collectivity, a code naming nothing.
        {"code_commune": "75056"},
        {"code_departement": "975", "code_commune": "97501"},
        {"code_departement": "99", "code_commune": "99001"},
    ],
)
def test_malformed_or_inconsistent_canonical_fields_are_refused(changes):
    with pytest.raises(ValidationError):
        row(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"end_exclusive": date(2027, 1, 1)},
        {"end_exclusive": date(2, 1, 1)},
        {"end_exclusive": datetime(2026, 1, 1)},
        {"observed_at": datetime(2026, 9, 9)},
        {"communes": ("44001", "44001")},
        {"communes": ("99001",)},
        # A hosted commune outside the received lot, an invalid lot.
        {"communes": ("75056",)},
        {"departments": ("44", "99")},
        {"departments": ("85", "44")},
        {"departments": ()},
    ],
)
def test_invalid_explicit_boundary_observation_or_reference_is_refused(changes):
    with pytest.raises(dvf.DvfCalculationRefused):
        calculate([], **changes)


def test_real_generation_is_preserved_without_becoming_publication_authority():
    generation = UUID("11111111-1111-4111-8111-111111111111")
    source = stock([]).model_copy(update={"source_generation": generation})
    result = dvf.calculate_dvf(
        source,
        communes=("44001",),
        departments=("44",),
        end_exclusive=date(2026, 1, 1),
        observed_at=datetime(2026, 9, 9, tzinfo=UTC),
    )
    assert result.source_generation == generation
    assert result.publication_authorized is False


def test_hosted_lot_beyond_44_85_is_calculated_like_44_85():
    """Contract v3 (plan 04.1-10): any hostable departement of the lot."""
    rows = [
        row(f"paris-{i}", code_departement="75", code_commune="75056") for i in range(5)
    ] + [row("reunion", code_departement="974", code_commune="97411")]
    result = calculate(rows, communes=("75056", "97411"), departments=("75", "974"))
    assert cell(result, "75056").count == 5
    assert cell(result, "75056").median_eur_m2 == Fraction(1000)
    assert cell(result, "97411").count == 1
    assert cell(result, "97411").median_state == "insufficient_sample"


def test_mutation_outside_the_received_lot_refuses_the_whole_calculation():
    rows = [row(), row("paris", code_departement="75", code_commune="75056")]
    with pytest.raises(dvf.DvfCalculationRefused, match="mutation_outside"):
        calculate(rows)
