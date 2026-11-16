"""RED stub tests for business hook (DQ-BIZ-01).

Wave 0 — RED stubs. Fail at EXECUTION by ImportError/AttributeError on symbols
not yet created in Wave 1:
  - app.data_quality.business.dvf_prix_m2.check_dvf_prix_m2
  - PROFILES["dvf"].business_hook field on DatasetProfile

Collection stays GREEN because all imports of those symbols are inside each test
body, never at module top-level.

Contract fixed here for plan 26-01:
- DQ-BIZ-01: check_dvf_prix_m2(conn, profile) -> list[Finding]
  - Returns 1 Finding(severity="WARN", category="business") when bad rows > 0
  - Returns [] when bad rows == 0
- DQ-BIZ-01: PROFILES["dvf"].business_hook is not None
- DQ-BIZ-01: _audit_profile dispatches business_hook + appends "business" to dims_run
"""

from unittest.mock import MagicMock

import pytest

from tests.data_quality.scopes import FULL_FRANCE_SCOPE

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# DQ-BIZ-01 — check_dvf_prix_m2 unit (mock conn)
# ---------------------------------------------------------------------------


def test_dvf_prix_m2_warn() -> None:
    """Mock conn returns bad=5 → 1 Finding(severity="WARN", category="business")."""
    from app.data_quality.business.dvf_prix_m2 import check_dvf_prix_m2
    from app.data_quality.profiles import DatasetProfile

    profile = DatasetProfile(dataset="dvf", table="mutations")
    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (5,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_dvf_prix_m2(mock_conn, profile)

    assert len(findings) == 1, (
        f"Expected 1 Finding when bad=5, got {len(findings)}: {findings}"
    )
    assert findings[0].severity == "WARN", (
        f"Expected severity='WARN' (D-03), got '{findings[0].severity}'"
    )
    assert findings[0].category == "business", (
        f"Expected category='business', got '{findings[0].category}'"
    )


def test_dvf_prix_m2_green() -> None:
    """Mock conn returns bad=0 → empty list [] (green, no findings)."""
    from app.data_quality.business.dvf_prix_m2 import check_dvf_prix_m2
    from app.data_quality.profiles import DatasetProfile

    profile = DatasetProfile(dataset="dvf", table="mutations")
    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_dvf_prix_m2(mock_conn, profile)

    assert findings == [], f"Expected [] when bad=0, got {findings}"


# ---------------------------------------------------------------------------
# DQ-BIZ-01 — profile registration
# ---------------------------------------------------------------------------


def test_dvf_profile_has_hook() -> None:
    """PROFILES["dvf"].business_hook is not None (D-02: one concrete hook)."""
    from app.data_quality.profiles import PROFILES

    dvf = PROFILES["dvf"]
    assert dvf.business_hook is not None, (
        "PROFILES['dvf'].business_hook must be set (DQ-BIZ-01 / D-02). "
        "Add business_hook=check_dvf_prix_m2 to the dvf DatasetProfile entry."
    )


# ---------------------------------------------------------------------------
# DQ-BIZ-01 — dispatch in _audit_profile
# ---------------------------------------------------------------------------


def test_business_hook_dispatched_in_audit_profile() -> None:
    """_audit_profile calls business_hook once; 'business' in dims_run."""
    from app.data_quality.profiles import DatasetProfile, VolumeSpec
    from app.data_quality.run import _audit_profile

    mock_hook = MagicMock(return_value=[])
    profile = DatasetProfile(
        dataset="dvf",
        table="mutations",
        volume=VolumeSpec(full_france_min=1_000),
        business_hook=mock_hook,  # RED: field not yet on DatasetProfile
    )

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (5_000,)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    _result, dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)

    mock_hook.assert_called_once_with(mock_conn, profile)
    assert "business" in dims_run, (
        f"Expected 'business' in dims_run after hook dispatch, got {dims_run}"
    )
