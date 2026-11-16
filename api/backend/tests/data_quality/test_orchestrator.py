"""Unit tests for data_quality orchestrator: _audit_profile dispatch + dimensions_run.

Covers DQ-CORE-02: orchestrator dispatches to applicable dimension checkers,
populates dimensions_run correctly, merges findings without double row_count.
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from app.data_quality.base import AuditResult, Finding
from app.data_quality.geo_bounds import GeoSpec
from app.data_quality.profiles import (
    DatasetProfile,
    FreshnessSpec,
    NullSpec,
    VolumeSpec,
)
from tests.data_quality.scopes import FULL_FRANCE_SCOPE

# ---------------------------------------------------------------------------
# Helpers — build lightweight profiles without touching DB
# ---------------------------------------------------------------------------


def _make_geo_spec() -> GeoSpec:
    return GeoSpec(
        dataset="test_geo",
        table="test_geo_table",
        geom_col="geom",
        dept_expr="t.code_departement",
    )


def _make_mock_conn_for_volume(row_count: int) -> MagicMock:
    """Return a mock psycopg connection whose cursor returns row_count."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (row_count,)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn


def _make_geo_audit_result(row_count: int = 5000, errors: bool = False) -> AuditResult:
    """Return a pre-built AuditResult as if audit_geo_bounds() returned it."""
    r = AuditResult(dataset="test_geo", table="test_geo_table", row_count=row_count)
    if errors:
        r.findings.append(
            Finding(
                severity="ERROR",
                category="geo",
                title="Hors département",
                description="97% des points hors département.",
            )
        )
    r.stats["hors_departement_pct"] = "97.00%"
    return r


# ---------------------------------------------------------------------------
# Task 1: _audit_profile — geo=None, volume only
# ---------------------------------------------------------------------------


def test_audit_profile_volume_only_dims_run() -> None:
    """Profile with geo=None and volume → dims_run == ['volume'], not 'geo'."""
    from app.data_quality.run import _audit_profile

    profile = DatasetProfile(
        dataset="demographie",
        table="demographie",
        geo=None,
        volume=VolumeSpec(full_france_min=17_000),
    )
    mock_conn = _make_mock_conn_for_volume(34_963)

    _result, dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert dims_run == ["volume"], f"Expected ['volume'], got {dims_run}"
    assert "geo" not in dims_run


def test_audit_profile_geo_and_volume_dims_run() -> None:
    """Profile with geo + volume → dims_run == ['geo', 'volume'] (order preserved)."""
    from app.data_quality.run import _audit_profile

    geo_spec = _make_geo_spec()
    profile = DatasetProfile(
        dataset="equipements",
        table="equipements",
        geo=geo_spec,
        volume=VolumeSpec(full_france_min=1_000),
    )
    # mock_conn for volume (geo is patched separately)
    mock_conn = _make_mock_conn_for_volume(5_000)

    geo_result = _make_geo_audit_result(row_count=5_000, errors=False)
    with (
        patch("app.data_quality.run.audit_geo_bounds", return_value=geo_result),
    ):
        _result, dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert dims_run == ["geo", "volume"], f"Expected ['geo', 'volume'], got {dims_run}"


# ---------------------------------------------------------------------------
# Task 1: findings merging — geo ERROR preserved after volume check
# ---------------------------------------------------------------------------


def test_audit_profile_geo_error_preserved_in_result() -> None:
    """If geo returns an ERROR finding, it must appear in the merged result."""
    from app.data_quality.run import _audit_profile

    geo_spec = _make_geo_spec()
    profile = DatasetProfile(
        dataset="equipements",
        table="equipements",
        geo=geo_spec,
        volume=VolumeSpec(full_france_min=1_000),
    )
    mock_conn = _make_mock_conn_for_volume(5_000)

    geo_result = _make_geo_audit_result(row_count=5_000, errors=True)
    with (
        patch("app.data_quality.run.audit_geo_bounds", return_value=geo_result),
    ):
        result, _dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    geo_errors = [
        f for f in result.findings if f.category == "geo" and f.severity == "ERROR"
    ]
    assert len(geo_errors) >= 1, "Geo ERROR finding was lost after merging with volume"


# ---------------------------------------------------------------------------
# Task 1: pitfall 1 — no double row_count overwrite when geo already set it
# ---------------------------------------------------------------------------


def test_audit_profile_no_double_row_count_when_geo_sets_it() -> None:
    """row_count from geo must NOT be overwritten by volume when geo already set it > 0.

    Both geo and volume do count(*). The orchestrator must not clobber the
    geo row_count with the volume row_count when they differ (e.g. filtered
    subsets). Rule: only overwrite if result.row_count == 0 (pitfall 1).
    """
    from app.data_quality.run import _audit_profile

    geo_spec = _make_geo_spec()
    profile = DatasetProfile(
        dataset="equipements",
        table="equipements",
        geo=geo_spec,
        volume=VolumeSpec(full_france_min=1_000),
    )
    # volume mock returns a different count to detect overwrite
    mock_conn = _make_mock_conn_for_volume(9_999)

    geo_row_count = 5_000
    geo_result = _make_geo_audit_result(row_count=geo_row_count, errors=False)
    with (
        patch("app.data_quality.run.audit_geo_bounds", return_value=geo_result),
    ):
        result, _dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    # row_count should remain the geo value (5_000), not the volume value (9_999)
    assert result.row_count == geo_row_count, (
        f"row_count was overwritten: expected {geo_row_count}, got {result.row_count}"
    )


def test_audit_profile_row_count_from_volume_when_geo_is_none() -> None:
    """When geo=None, row_count comes from check_volume (the only source)."""
    from app.data_quality.run import _audit_profile

    profile = DatasetProfile(
        dataset="demographie",
        table="demographie",
        geo=None,
        volume=VolumeSpec(full_france_min=17_000),
    )
    expected_count = 34_963
    mock_conn = _make_mock_conn_for_volume(expected_count)

    result, _dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert result.row_count == expected_count


# ---------------------------------------------------------------------------
# Task 1: D-05 guard — import check (static, not runtime)
# ---------------------------------------------------------------------------


def test_run_imports_audit_geo_bounds_not_main() -> None:
    """D-05: run.py imports audit_geo_bounds, never 'main' from geo_bounds."""
    import app.data_quality.geo_bounds as geo_bounds_module
    import app.data_quality.run as run_module

    # audit_geo_bounds must be importable from run module namespace
    assert hasattr(run_module, "audit_geo_bounds"), (
        "run.py does not import audit_geo_bounds — required by D-05"
    )
    # The function imported must be the same object as geo_bounds.audit_geo_bounds
    assert run_module.audit_geo_bounds is geo_bounds_module.audit_geo_bounds, (
        "run.py's audit_geo_bounds is not the one from geo_bounds.py"
    )
    # 'main' from geo_bounds must NOT be in run module namespace
    assert not hasattr(run_module, "geo_bounds"), (
        "run.py imported geo_bounds module (should only import audit_geo_bounds)"
    )


# ---------------------------------------------------------------------------
# C2: freshness + nulls dispatch — dimensions_run populated (DQ-UNIV-02/03)
# ---------------------------------------------------------------------------


def test_audit_profile_freshness_and_nulls_in_dims_run() -> None:
    """C2: Profile with freshness+nulls → both appear in dims_run (DQ-UNIV-02/03).

    check_freshness and check_nulls are patched in run's namespace so no DB
    is needed.  Verifies that _audit_profile appends 'freshness' and 'nulls'
    to dims_run when the profile declares both specs.
    """
    from app.data_quality.run import _audit_profile

    profile = DatasetProfile(
        dataset="demographie",
        table="demographie",
        geo=None,
        volume=VolumeSpec(full_france_min=35_000),
        freshness=FreshnessSpec(etl_dataset_name="demographie", max_days=365),
        nulls=NullSpec(
            required_cols=("code_insee", "annee"),
            expected_cols=("population",),
            expected_threshold=0.05,
        ),
    )
    mock_conn = _make_mock_conn_for_volume(34_963)

    with (
        patch("app.data_quality.run.check_freshness", return_value=[]) as mock_fresh,
        patch("app.data_quality.run.check_nulls", return_value=[]) as mock_nulls,
    ):
        _result, dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert "freshness" in dims_run, f"Expected 'freshness' in dims_run, got {dims_run}"
    assert "nulls" in dims_run, f"Expected 'nulls' in dims_run, got {dims_run}"
    mock_fresh.assert_called_once_with(mock_conn, profile)
    mock_nulls.assert_called_once_with(mock_conn, profile, stats=_result.stats)


def test_audit_profile_no_freshness_nulls_when_specs_none() -> None:
    """C2 negative: Profile without freshness/nulls → neither in dims_run (cell '—').

    When a profile declares freshness=None and nulls=None, _audit_profile must
    NOT add 'freshness' or 'nulls' to dims_run — they render as '—' in the matrix.
    """
    from app.data_quality.run import _audit_profile

    profile = DatasetProfile(
        dataset="demographie",
        table="demographie",
        geo=None,
        volume=VolumeSpec(full_france_min=35_000),
        freshness=None,
        nulls=None,
    )
    mock_conn = _make_mock_conn_for_volume(34_963)

    _result, dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert "freshness" not in dims_run, (
        f"'freshness' must not appear in dims_run when spec is None, got {dims_run}"
    )
    assert "nulls" not in dims_run, (
        f"'nulls' must not appear in dims_run when spec is None, got {dims_run}"
    )
