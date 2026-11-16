"""Exact decimal ties found by replaying the complete frozen LOVAC source."""

from decimal import Decimal

import pytest

from tests.test_etl_vacance_nullability import _transform

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "total,vacant,long,expected",
    [
        ("640", "92", "20", ("14.38", "11.25", "3.12")),
        ("640", "184", "92", ("28.75", "14.38", "14.38")),
        ("32", "1", "0", ("3.12", "3.12", "0.00")),
        ("32", "3", "1", ("9.38", "6.25", "3.12")),
    ],
)
def test_decimal_half_even_ties_for_all_three_rates(
    tmp_path, monkeypatch, total, vacant, long, expected
):
    row = _transform(
        tmp_path, monkeypatch, total=total, vacant=vacant, long_vacant=long
    )
    assert [
        Decimal(row[key])
        for key in ("taux_vacance", "taux_vacance_moins_2ans", "taux_vacance_plus_2ans")
    ] == [Decimal(v) for v in expected]
