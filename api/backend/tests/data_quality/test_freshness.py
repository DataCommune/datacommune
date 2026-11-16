"""Stub tests for freshness dimension (DQ-UNIV-02, D-05).

Wave 0 — RED stubs. These tests fail at EXECUTION by ImportError on
`check_freshness` and `FreshnessSpec` (not yet created in profiles.py).
Collection stays GREEN because the imports are inside each test body.

Freshness contract (D-05):
- Source: etl_runs.last_success_at (MAX completed_at WHERE status='success')
- Mechanic: WARN if age > freshness_max_days
- Date-independent: _now() seam is patchable in profiles.py
- WARN-only: freshness never produces ERROR (D-05b)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_freshness_warn_when_too_old() -> None:
    """WARN produced when last_success_at is older than max_days.

    Verifies that a 100-day-old last run exceeds a 90-day threshold
    and produces exactly 1 Finding with severity=WARN, category=freshness.
    """
    from app.data_quality.profiles import (  # RED: not yet created
        DatasetProfile,
        FreshnessSpec,
        check_freshness,
    )

    fake_now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    last_success = fake_now - timedelta(days=100)  # 100 days old — exceeds 90-day limit

    profile = DatasetProfile(
        dataset="equipements",
        table="equipements",
        freshness=FreshnessSpec(
            etl_dataset_name="equipements",
            max_days=90,
        ),
    )

    # check_freshness uses conn.execute(...).fetchone() pattern (direct execute)
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (last_success,)

    with patch("app.data_quality.profiles._now", return_value=fake_now):
        findings = check_freshness(mock_conn, profile)

    assert len(findings) == 1, (
        f"Expected 1 WARN finding for stale data, got {len(findings)}"
    )
    assert findings[0].severity == "WARN", (
        f"Freshness must never produce ERROR (D-05b), got {findings[0].severity}"
    )
    assert findings[0].category == "freshness", (
        f"Expected category='freshness', got '{findings[0].category}'"
    )


def test_freshness_green_when_fresh() -> None:
    """No findings when last_success_at is within max_days."""
    from app.data_quality.profiles import (  # RED: not yet created
        DatasetProfile,
        FreshnessSpec,
        check_freshness,
    )

    fake_now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    # 10 days old — well within 90-day limit
    last_success = fake_now - timedelta(days=10)

    profile = DatasetProfile(
        dataset="equipements",
        table="equipements",
        freshness=FreshnessSpec(
            etl_dataset_name="equipements",
            max_days=90,
        ),
    )

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (last_success,)

    with patch("app.data_quality.profiles._now", return_value=fake_now):
        findings = check_freshness(mock_conn, profile)

    assert findings == [], (
        f"Expected 0 findings for fresh data (10 days < 90-day limit), got {findings}"
    )


def test_transport_freshness_uses_plural_dataset_name() -> None:
    """Regression (P38 D-04): PROFILES["transport"] must key freshness on

    'transports' (plural) — the value `pipeline.dataset_name` actually writes
    to `etl_runs.dataset_name` (via `PIPELINE_MAP["transports"]`,
    `scheduler/jobs.py`, and `etl_transport/pipeline.py`'s `dataset_name`
    property). The singular `"transport"` made `check_freshness` permanently
    blind: `SELECT MAX(completed_at) ... WHERE dataset_name='transport'`
    never matches any row, so the check always WARNs "aucun succès ETL"
    regardless of how recently the ETL actually ran.
    """
    from app.data_quality.profiles import PROFILES

    assert PROFILES["transport"].freshness is not None
    assert PROFILES["transport"].freshness.etl_dataset_name == "transports", (
        "transport freshness must key on 'transports' (plural) — the value "
        "written to etl_runs.dataset_name by etl_transport/pipeline.py"
    )


def test_freshness_warn_when_no_run() -> None:
    """WARN produced when etl_runs has no successful run (NULL / no row).

    A dataset that has never been successfully run is stale by definition.
    """
    from app.data_quality.profiles import (  # RED: not yet created
        DatasetProfile,
        FreshnessSpec,
        check_freshness,
    )

    fake_now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)

    profile = DatasetProfile(
        dataset="demographie",
        table="demographie",
        freshness=FreshnessSpec(
            etl_dataset_name="demographie",
            max_days=365,
        ),
    )

    mock_conn = MagicMock()
    # fetchone returns None: no successful run found in etl_runs
    mock_conn.execute.return_value.fetchone.return_value = None

    with patch("app.data_quality.profiles._now", return_value=fake_now):
        findings = check_freshness(mock_conn, profile)

    assert len(findings) == 1, (
        f"Expected 1 WARN finding for never-run dataset, got {len(findings)}"
    )
    assert findings[0].severity == "WARN", (
        f"Never-run freshness must be WARN (D-05b), got {findings[0].severity}"
    )
    assert findings[0].category == "freshness"
