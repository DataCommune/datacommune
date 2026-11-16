"""Stub tests for geo spatial derivation mode (DQ-GEO-02, D-07).

Wave 0 — RED stubs. These tests fail at EXECUTION by ImportError/AttributeError
on symbols not yet created:
  - GeoSpec.spatial_mode attribute (not yet in GeoSpec dataclass)
  - _flag_no_dept function (not yet in geo_bounds.py)

Collection stays GREEN because the access to these symbols is inside each
test body.

Spatial mode contract (D-07):
- GeoSpec gains spatial_mode: bool = False
- When spatial_mode=True, dept is derived by spatial containment (ST_Covers)
  instead of a dept_expr column value
- Semantic: no_dept = point outside all France polygons = ERROR
- out_of_dept is N/A for spatial_mode datasets (dept always matches by construction)
"""

import pytest

from app.data_quality.base import AuditResult
from app.data_quality.geo_bounds import GeoSpec  # exists

pytestmark = pytest.mark.unit


def test_no_dept_error_for_point_outside_france() -> None:
    """Points with no matching dept polygon (hors France) → ERROR in spatial mode.

    no_dept=500 on 500_000 with_geom means 0.1% of points land outside all
    department polygons — e.g. Lambert-93 coordinates treated as WGS-84,
    or foreign locations. Must be flagged as ERROR (D-07 sémantique no_dept).
    """
    from app.data_quality.geo_bounds import _flag_no_dept  # RED: not yet created

    spec = GeoSpec(
        dataset="arrets_transport",
        table="arrets_transport",
        geom_col="position",
        dept_expr="",  # no dept_expr in spatial mode
    )
    # Verify spatial_mode exists as attribute (RED: not yet added to GeoSpec)
    assert hasattr(spec, "spatial_mode"), (
        "GeoSpec must have spatial_mode attribute (D-07). "
        "Not yet implemented — this assertion must trigger RED."
    )

    result = AuditResult(
        dataset="arrets_transport",
        table="arrets_transport",
        row_count=500_000,
    )

    no_dept = 500  # 500 points outside France polygons
    with_geom = 500_000

    _flag_no_dept(result, spec, no_dept=no_dept, with_geom=with_geom)

    geo_errors = [
        f for f in result.findings if f.severity == "ERROR" and f.category == "geo"
    ]
    assert len(geo_errors) >= 1, (
        f"no_dept={no_dept} must produce ERROR (points hors France = bug Lambert). "
        f"findings: {result.findings}"
    )
    assert any(
        "france" in f.title.lower() or "departement" in f.title.lower()
        for f in geo_errors
    ), (
        "ERROR finding must mention 'france' or 'departement' in its title. "
        f"findings: {geo_errors}"
    )


def test_spatial_mode_skips_out_of_dept() -> None:
    """GeoSpec spatial_mode=True: out_of_dept is N/A by construction.

    In spatial mode, dept is derived from containment — a point is always
    'in' the dept polygon that contains it by definition. The out_of_dept
    metric is semantically undefined and must not be calculated.
    """
    from app.data_quality.geo_bounds import _flag_no_dept  # RED: not yet created

    # Attempt to construct a spatial_mode GeoSpec — RED if spatial_mode not in GeoSpec
    try:
        spec = GeoSpec(
            dataset="arrets_transport",
            table="arrets_transport",
            geom_col="position",
            dept_expr="",
            spatial_mode=True,  # RED: keyword not accepted by current GeoSpec
        )
    except TypeError as exc:
        raise AssertionError(
            f"GeoSpec must accept spatial_mode=True (D-07). Not yet implemented: {exc}"
        ) from exc

    assert spec.spatial_mode is True, "spatial_mode must be set to True"

    # In spatial mode, calling _flag_no_dept (not _flag_out_of_dept)
    # A result with no_dept=0 must produce no findings (clean spatial check)
    result = AuditResult(
        dataset="arrets_transport",
        table="arrets_transport",
        row_count=500_000,
    )
    _flag_no_dept(result, spec, no_dept=0, with_geom=500_000)

    out_of_dept_findings = [
        f for f in result.findings if "hors departement" in f.title.lower()
    ]
    assert out_of_dept_findings == [], (
        "spatial_mode datasets must not produce out_of_dept findings (N/A). "
        f"findings: {result.findings}"
    )
