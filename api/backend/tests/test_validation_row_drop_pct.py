"""row_drop_pct fits validation_runs.row_drop_pct NUMERIC(5,2) (04.1-21)."""

from __future__ import annotations

import pytest

from app.etl.validation import _row_drop_pct


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (None, 10, None),
        (0, 10, None),
        (100, 80, 20.0),
        (100, 150, -50.0),
        # Growth beyond -999.99 % (seed rows, national over 44/85): not stored.
        (9, 655_043, None),
        (1_022_232, 26_000_000, None),
        (100, 1_099, -999.0),
    ],
)
def test_row_drop_pct_is_representable_or_none(before, after, expected):
    assert _row_drop_pct(before, after) == expected
