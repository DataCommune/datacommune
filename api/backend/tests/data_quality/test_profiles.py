"""Unit tests for data_quality profiles: AuditResult.dimensions_run (D-07)
and DatasetProfile declarative structure (DQ-CORE-01).
"""

import pytest

pytestmark = pytest.mark.unit

from app.data_quality.base import AuditResult
from tests.data_quality.scopes import FULL_FRANCE_SCOPE

# ---------------------------------------------------------------------------
# AuditResult.dimensions_run — D-07
# ---------------------------------------------------------------------------


def test_audit_result_dimensions_run_assignable() -> None:
    """AuditResult is mutable — dimensions_run can be set after construction."""
    r = AuditResult(dataset="t", table="t", row_count=0)
    r.dimensions_run = ["geo", "volume"]
    assert r.dimensions_run == ["geo", "volume"]


def test_audit_result_dimensions_run_default_empty() -> None:
    """AuditResult.dimensions_run defaults to an empty list."""
    r = AuditResult(dataset="t", table="t", row_count=0)
    assert r.dimensions_run == []


# ---------------------------------------------------------------------------
# DatasetProfile declarative structure (DQ-CORE-01) — RED until Task 2
# ---------------------------------------------------------------------------


def test_profiles_has_21_datasets() -> None:
    """PROFILES contains exactly the 21 dataset keys (Phase 25 extended coverage)."""
    from app.data_quality.profiles import PROFILES

    expected = {
        "antennes",
        "ban",
        "batiments",
        "cadastre",
        "connectivite",
        "copropriete",
        "demographie",
        "dpe",
        "dvf",
        "encadrement_loyers",
        "equipements",
        "fiscalite",
        "foncier_pm",
        "loyers",
        "monuments",
        "permis",
        "revenus",
        "scolaire",
        "transport",
        "vacance",
        "zonage_abc",
    }
    assert set(PROFILES.keys()) == expected
    assert len(PROFILES) == 21


def test_demographie_geo_is_none() -> None:
    """demographie has geo=None (non-spatial) and volume is not None."""
    from app.data_quality.profiles import PROFILES

    p = PROFILES["demographie"]
    assert p.geo is None
    assert p.volume is not None


def test_equipements_and_cadastre_geo_from_geo_specs() -> None:
    """equipements and cadastre reuse GEO_SPECS instances (D-09)."""
    from app.data_quality.geo_bounds import GEO_SPECS
    from app.data_quality.profiles import PROFILES

    assert PROFILES["equipements"].geo is GEO_SPECS["equipements"]
    assert PROFILES["cadastre"].geo is GEO_SPECS["cadastre"]


def test_volume_spec_and_dataset_profile_are_frozen() -> None:
    """VolumeSpec and DatasetProfile are frozen dataclasses."""
    from dataclasses import FrozenInstanceError

    from app.data_quality.profiles import DatasetProfile, VolumeSpec

    vs = VolumeSpec(full_france_min=100)
    with pytest.raises(FrozenInstanceError):
        vs.full_france_min = 999  # type: ignore[misc]

    dp = DatasetProfile(dataset="x", table="x")
    with pytest.raises(FrozenInstanceError):
        dp.dataset = "y"  # type: ignore[misc]


def test_check_volume_empty_table() -> None:
    """check_volume: total=0 → 1 Finding ERROR category='volume'."""
    from unittest.mock import MagicMock

    from app.data_quality.profiles import (
        DatasetProfile,
        VolumeSpec,
        check_volume,
    )

    profile = DatasetProfile(
        dataset="test",
        table="test_table",
        volume=VolumeSpec(full_france_min=100),
    )
    # Mock connection returning count=0.
    # Patch settings to full-France (unscoped) so floor == full_france_min.
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (0,)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    total, findings = check_volume(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert total == 0
    assert len(findings) == 1
    assert findings[0].severity == "ERROR"
    assert findings[0].category == "volume"


def test_check_volume_below_floor() -> None:
    """check_volume: total < floor → 1 Finding ERROR category='volume'."""
    from unittest.mock import MagicMock

    from app.data_quality.profiles import (
        DatasetProfile,
        VolumeSpec,
        check_volume,
    )

    profile = DatasetProfile(
        dataset="test",
        table="test_table",
        volume=VolumeSpec(full_france_min=1000),
    )
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (50,)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    total, findings = check_volume(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert total == 50
    assert len(findings) == 1
    assert findings[0].severity == "ERROR"
    assert findings[0].category == "volume"


def test_check_volume_above_floor() -> None:
    """check_volume: total >= floor → 0 findings."""
    from unittest.mock import MagicMock

    from app.data_quality.profiles import (
        DatasetProfile,
        VolumeSpec,
        check_volume,
    )

    profile = DatasetProfile(
        dataset="test",
        table="test_table",
        volume=VolumeSpec(full_france_min=100),
    )
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (5000,)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    total, findings = check_volume(mock_conn, profile, FULL_FRANCE_SCOPE)

    assert total == 5000
    assert findings == []
