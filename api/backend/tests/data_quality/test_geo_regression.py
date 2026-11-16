"""Stub tests for geo dimension — Lambert positive control + banding (DQ-GEO-01, D-02).

Wave 0 — RED stubs. These tests fail at EXECUTION by TypeError: the current
`_flag_invalid(result, spec, invalid)` signature takes 3 args; the tests call it
with 4 args `(result, spec, invalid, with_geom)` which is the target signature (D-02).

_flag_out_of_dept and AuditResult/GeoSpec EXIST already — top-level imports OK.
_flag_invalid EXISTS but signature changes — top-level import OK; RED at execution.

Banding contract (D-02):
- INVALID_WARN_PCT: any invalid > 0 and below INVALID_ERROR_PCT → WARN
- INVALID_ERROR_PCT = 1.0: >= 1% invalid → ERROR (structural collapse)
- Lambert-93 positive control: equipements out_of_dept ~97% → ERROR via
  _flag_out_of_dept, unaffected by _flag_invalid banding (Lambert coords are
  geometrically valid — ST_IsValid=TRUE, so _flag_invalid won't touch them).
"""

import pytest

from app.data_quality.base import AuditResult, Finding  # noqa: F401 — already exist
from app.data_quality.geo_bounds import (  # already exist
    GeoSpec,
    _flag_invalid,
    _flag_out_of_dept,
)

pytestmark = pytest.mark.unit


def test_lambert_positive_control_stays_red_after_banding() -> None:
    """equipements Lambert-93 out_of_dept ~97% → still ERROR after banding D-02.

    The banding change to _flag_invalid must NOT interfere with _flag_out_of_dept.
    Lambert-93 coords produce geometrically VALID geometries (ST_IsValid=TRUE)
    but they land far outside France → out_of_dept ~97% → ERROR.
    """
    spec = GeoSpec("equipements", "equipements", "geom", "t.code_departement")
    result = AuditResult(
        dataset="equipements", table="equipements", row_count=2_722_677
    )

    with_geom = 2_600_000
    out_of_dept = 2_522_000  # ~97%
    out_pct = out_of_dept / with_geom * 100  # ~97%

    # _flag_out_of_dept already uses correct signature — stays RED
    _flag_out_of_dept(result, spec, out_of_dept, out_pct)

    # Call _flag_invalid with target signature (4 args) — RED: current is 3 args
    _flag_invalid(result, spec, 0, with_geom)  # invalid=0, with_geom=2_600_000

    geo_errors = [
        f for f in result.findings if f.severity == "ERROR" and f.category == "geo"
    ]
    assert len(geo_errors) >= 1, (
        "Lambert positive control must still produce ERROR after banding (D-02). "
        "out_of_dept at 97% must remain ERROR regardless of _flag_invalid changes."
    )
    assert any("hors" in f.title.lower() for f in geo_errors), (
        "Expected out_of_dept ERROR finding with 'hors' in title."
    )


def test_invalid_banding_cadastre_warn() -> None:
    """34 invalid geometries / 1_280_000 = 0.003% → WARN (not ERROR) after D-02.

    Below INVALID_ERROR_PCT (1.0%) but above 0 → must be WARN.
    The 'hair-trigger' old behavior (any invalid > 0 → ERROR) is replaced
    by banded severity: ERROR only if >= INVALID_ERROR_PCT.
    """
    spec = GeoSpec("cadastre", "parcelles", "geometrie", "t.code_departement")
    result = AuditResult(dataset="cadastre", table="parcelles", row_count=1_280_000)

    with_geom = 1_280_000
    invalid = 34  # 0.003% — cadastre real data

    # Call with target 4-arg signature — RED: current is 3 args
    _flag_invalid(result, spec, invalid, with_geom)

    geo_warns = [
        f for f in result.findings if f.severity == "WARN" and f.category == "geo"
    ]
    geo_errors = [
        f for f in result.findings if f.severity == "ERROR" and f.category == "geo"
    ]
    assert len(geo_warns) >= 1, (
        f"34 invalids / 1.28M = 0.003% must produce WARN (D-02 banding). "
        f"Old hair-trigger ERROR is replaced by WARN for this small share. "
        f"findings: {result.findings}"
    )
    assert len(geo_errors) == 0, (
        f"0.003% invalid must NOT produce ERROR (below INVALID_ERROR_PCT=1.0%). "
        f"findings: {result.findings}"
    )


def test_invalid_banding_collapse_error() -> None:
    """Massive invalid count (50% of geometries) → ERROR (structural collapse).

    When invalid count exceeds INVALID_ERROR_PCT (1.0%), the finding must be ERROR.
    """
    spec = GeoSpec("test_dataset", "test_table", "geom", "t.code_departement")
    result = AuditResult(
        dataset="test_dataset", table="test_table", row_count=1_000_000
    )

    with_geom = 1_000_000
    invalid = 500_000  # 50% — structural collapse

    # Call with target 4-arg signature — RED: current is 3 args
    _flag_invalid(result, spec, invalid, with_geom)

    geo_errors = [
        f for f in result.findings if f.severity == "ERROR" and f.category == "geo"
    ]
    assert len(geo_errors) >= 1, (
        f"50% invalid must produce ERROR (above INVALID_ERROR_PCT=1.0%). "
        f"findings: {result.findings}"
    )
