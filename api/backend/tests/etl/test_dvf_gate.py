"""Unit tests for the DVF success-ratio gate against legitimate 404s.

Regression: the `latest` data.gouv mirror prunes old vintages (e.g. 2020 was
removed in 2026) and never publishes DVF for Alsace-Moselle (57/67/68).  Those
absences return 404 and must NOT count against the swap success ratio, otherwise
a valid refresh aborts (observed: 10/12 files = 83.3% < 90% threshold).

5xx / timeout failures DO raise (not 404) and must stay counted, so the gate
still catches a real source outage.
"""

from datetime import date

import pytest

from app.etl_dvf.config import YEARS
from app.etl_dvf.pipeline import DvfPipeline
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit


def _dvf_on_44_85() -> DvfPipeline:
    """DVF bound to the instance scope {dvf} x {44, 85}, as the runner does."""
    pipeline = DvfPipeline()
    pipeline.bind_scope(InstanceScope(frozenset({"dvf"}), ("44", "85")))
    return pipeline


class TestDvfYearsWindow:
    """Regression (P38 DATA-03): YEARS must not hard-code its upper bound.

    `YEARS = list(range(2020, 2026))` silently excludes the current year once
    the year rolls over (live bug on 2026-07-04: 2026 was missing). The upper
    bound must derive from `date.today().year` so it self-extends every year.
    """

    def test_years_upper_bound_tracks_current_year(self):
        assert YEARS[-1] == date.today().year

    def test_years_lower_bound_preserved(self):
        assert 2020 in YEARS


class TestDvfExpectedFileCount:
    def test_before_download_returns_theoretical(self):
        """No download yet -> theoretical max (years x scoped depts)."""
        p = _dvf_on_44_85()
        assert p.expected_file_count == len(YEARS) * 2

    def test_pruned_year_excluded_from_denominator(self):
        """A vintage absent from the mirror (404 on 44 and 85) is excluded.

        This is the exact production bug: scope 44,85, N years = N*2 attempts,
        but one vintage is gone -> 2 x 404.  The denominator must drop by 2
        so that the remaining staged files / remaining available = 100%
        passes the 90% gate. (P38: N grows with date.today().year, so the
        expected count is derived from YEARS, not hard-coded.)
        """
        p = _dvf_on_44_85()
        p._not_found_count = 2  # one vintage pruned: 44 + 85
        assert p.expected_file_count == len(YEARS) * 2 - 2

    def test_outage_keeps_strict_denominator(self):
        """Too many 404s = suspected source outage -> keep full denominator.

        If the gate were naively 'exclude all 404', a mirror returning 404
        everywhere would make ratio = 0/0 -> the check is skipped and an
        empty swap proceeds.  The guard forces the full count instead.
        """
        p = _dvf_on_44_85()
        p._not_found_count = 9  # 9/12 = 75% missing -> outage, not pruning
        assert p.expected_file_count == len(YEARS) * 2

    def test_guard_boundary_just_below_excludes(self):
        """At exactly 40% (4/10), the strict > comparison still excludes 404s."""
        p = _dvf_on_44_85()
        theoretical = len(YEARS) * 2  # 12
        p._not_found_count = 4  # 4/12 = 33% <= 40% -> excluded
        assert p.expected_file_count == theoretical - 4

    def test_guard_boundary_above_keeps_full(self):
        """Just over 40% trips the guard -> full denominator.

        Boundary derived from YEARS (not hard-coded) so this stays correct
        as YEARS grows every year (P38 DATA-03).
        """
        p = _dvf_on_44_85()
        theoretical = len(YEARS) * 2
        p._not_found_count = int(theoretical * 0.4) + 1  # just over 40% -> guard
        assert p.expected_file_count == theoretical
