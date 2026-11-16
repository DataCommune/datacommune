"""Stub tests for nulls dimension (DQ-UNIV-03, D-04).

Wave 0 — RED stubs. These tests fail at EXECUTION by ImportError on
`check_nulls` and `NullSpec` (not yet created in profiles.py). Collection
stays GREEN because the imports are inside each test body.

Nulls 2-passes contract (D-04):
- required_cols: exact COUNT scan (no TABLESAMPLE) — any null → ERROR
- expected_cols: check_null_rates reused (TABLESAMPLE 1% above 1M rows)
  threshold exceeded → WARN

Rationale for exact scan on required: TABLESAMPLE 1% on 2.7M rows would
sample ~27k rows. A single null in required_cols (1 / 2_722_677 = 0.00004%)
would be invisible to sampling but must be caught (D-04).
"""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def test_required_nulls_exact_scan_catches_rare_null() -> None:
    """1 null in a required_col on 2.7M rows → ERROR (exact scan D-04).

    TABLESAMPLE 1% would sample ~27k rows and likely miss this null.
    The required pass uses COUNT(*) WHERE col IS NULL — exact, no sampling.
    """
    from app.data_quality.profiles import (  # RED: not yet created
        DatasetProfile,
        NullSpec,
        check_nulls,
    )

    profile = DatasetProfile(
        dataset="equipements",
        table="equipements",
        nulls=NullSpec(
            required_cols=("type_equipement", "code_commune", "annee"),
            expected_cols=("code_departement",),
            expected_threshold=0.05,
        ),
    )

    mock_conn = MagicMock()

    # Simulate exact count: type_equipement has 1 null on 2_722_677 rows
    def mock_execute_side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_upper = (query if isinstance(query, str) else query.as_string()).upper()
        # Required pass: COUNT(*) WHERE col IS NULL
        if "IS NULL" in query_upper:
            mock_result.fetchone.return_value = (2_722_677, 1)  # 1 rare null
        else:
            mock_result.fetchone.return_value = (100, 0)
        return mock_result

    mock_conn.execute.side_effect = mock_execute_side_effect

    findings = check_nulls(mock_conn, profile)

    required_errors = [
        f for f in findings if f.severity == "ERROR" and f.category == "nulls"
    ]
    assert len(required_errors) >= 1, (
        "Rare null in required_col must produce ERROR via exact scan (D-04). "
        "TABLESAMPLE would miss this — the required pass must use COUNT(*) "
        "without sampling."
    )


def test_expected_nulls_warn_above_threshold() -> None:
    """expected_col with null rate > threshold → WARN (not ERROR, D-04)."""
    from app.data_quality.profiles import (  # RED: not yet created
        DatasetProfile,
        NullSpec,
        check_nulls,
    )

    profile = DatasetProfile(
        dataset="cadastre",
        table="parcelles",
        nulls=NullSpec(
            required_cols=("code_departement", "id_parcelle"),
            expected_cols=("commune", "geometrie"),
            expected_threshold=0.05,
        ),
    )

    mock_conn = MagicMock()

    def mock_execute_side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_upper = (query if isinstance(query, str) else query.as_string()).upper()
        if "IS NULL" in query_upper:
            # required pass: 0 nulls
            mock_result.fetchone.return_value = (100, 0)
        else:
            # expected pass: returns a count that implies >5% null rate
            # check_nulls is free to call check_null_rates or direct SQL
            mock_result.fetchone.return_value = (100, 0)
        return mock_result

    mock_conn.execute.side_effect = mock_execute_side_effect

    # Inject a mock for check_null_rates to simulate 10% null rate on geometrie
    import unittest.mock

    flagged_cols = [{"column": "geometrie", "null_rate": 0.10}]
    with unittest.mock.patch(
        "app.data_quality.profiles.check_null_rates", return_value=flagged_cols
    ):
        findings = check_nulls(mock_conn, profile)

    expected_warns = [
        f for f in findings if f.severity == "WARN" and f.category == "nulls"
    ]
    assert len(expected_warns) >= 1, (
        "expected_col geometrie at 10% null rate (above 5% threshold) "
        f"must produce WARN. Findings: {findings}"
    )


def test_nulls_all_clean_green() -> None:
    """0 nulls in required + expected rate under threshold → no findings."""
    from app.data_quality.profiles import (  # RED: not yet created
        DatasetProfile,
        NullSpec,
        check_nulls,
    )

    profile = DatasetProfile(
        dataset="demographie",
        table="demographie",
        nulls=NullSpec(
            required_cols=("code_insee", "annee"),
            expected_cols=("population",),
            expected_threshold=0.05,
        ),
    )

    mock_conn = MagicMock()

    def mock_execute_side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (100, 0)  # 0 nulls everywhere
        return mock_result

    mock_conn.execute.side_effect = mock_execute_side_effect

    import unittest.mock

    # No columns flagged by check_null_rates — all under threshold
    with unittest.mock.patch(
        "app.data_quality.profiles.check_null_rates", return_value=[]
    ):
        findings = check_nulls(mock_conn, profile)

    assert findings == [], (
        f"Expected 0 findings when no nulls in required and expected rates clean, "
        f"got {findings}"
    )
