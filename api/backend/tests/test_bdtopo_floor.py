"""Unit tests for BdTopoPipeline.min_staging_rows proportional floor (B3-2).

Verifies (departements read from the bound instance scope, D-22):
  1. 4-dept scope: floor < 3_000_000 — GATE-01 no longer aborts.
  2. GATE-01 passes for a realistic 4-dept staging (~2M rows).
  3. Full-France (102 depts): floor == 40_000_000 (non-regression D-07).

All tests are pure logic: no DB, no network, no I/O.
"""

import logging

import pytest

from app.etl.base import DEPARTEMENTS
from app.etl.runner import PipelineRunner
from app.etl_bdtopo.pipeline import BdTopoPipeline

# ---------------------------------------------------------------------------
# 1. 4-dept scope: floor < 3M
# ---------------------------------------------------------------------------

_FOUR_DEPTS = ("75", "92", "93", "94")


@pytest.mark.unit
def test_min_staging_rows_default_below_3m(bind_departements) -> None:
    """With a 4-dept scope, the floor must be < 3_000_000.

    The proportional formula yields ~1.57M for 4/102 depts.
    """
    bind_departements(_FOUR_DEPTS, BdTopoPipeline)
    p = BdTopoPipeline()
    assert p.min_staging_rows < 3_000_000, (
        f"Expected floor < 3_000_000, got {p.min_staging_rows:,}"
    )


# ---------------------------------------------------------------------------
# 2. GATE-01 passes for a realistic 4-dept staging
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gate01_passes_for_realistic_4dept_staging(bind_departements) -> None:
    """GATE-01 must return None (pass) when staging has 2M rows (4 depts).

    PipelineRunner._check_min_row_count is a staticmethod that only reads
    pipeline.min_staging_rows — no DB call involved.
    """
    bind_departements(_FOUR_DEPTS, BdTopoPipeline)
    p = BdTopoPipeline()
    log = logging.getLogger("test_gate01")
    result = PipelineRunner._check_min_row_count(p, 2_000_000, log)
    assert result is None, (
        f"GATE-01 should pass for 2M rows with default scope, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 3. Full-France non-regression: floor == 40M when scope = 102 depts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_min_staging_rows_full_france_equals_40m(bind_departements) -> None:
    """With a 102-dept scope, the floor must remain exactly 40_000_000."""
    bind_departements(DEPARTEMENTS, BdTopoPipeline)
    p = BdTopoPipeline()
    assert p.min_staging_rows == 40_000_000, (
        f"Expected full-France floor == 40_000_000, got {p.min_staging_rows:,}"
    )
