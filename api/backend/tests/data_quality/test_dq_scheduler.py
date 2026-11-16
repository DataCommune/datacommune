"""RED stub tests for scheduler job wiring (DQ-INTEG-02).

Wave 0 — RED stubs. Fail at EXECUTION by ImportError on symbol not yet created
in Wave 1:
  - app.scheduler.jobs.run_data_quality_daily

Collection stays GREEN because the import is inside each test body, never at
module top-level.

Contract fixed here for plan 26-04:
- DQ-INTEG-02: run_data_quality_daily() -> None
  - Does NOT raise when subprocess.run returns exit code 1 (expected red, D-09)
  - Does NOT raise when subprocess.run returns exit code 0 (green)
  - Logs a warning when subprocess.run returns exit code 2 (unexpected)
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# DQ-INTEG-02 — run_data_quality_daily no-raise on exit codes
# ---------------------------------------------------------------------------


def test_dq_job_no_raise_on_exit_1() -> None:
    """exit=1 (expected red: Lambert-93 datasets) → run_data_quality_daily() no-raise.

    D-09: logging only, scheduler must not surface the error.
    """
    from app.scheduler.jobs import run_data_quality_daily  # RED: not yet created

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = ""

    with patch("app.scheduler.jobs.subprocess.run", return_value=mock_result):
        # Must not raise CalledProcessError or any other exception
        run_data_quality_daily()


def test_dq_job_no_raise_on_exit_0() -> None:
    """exit=0 (all green) → run_data_quality_daily() no-raise."""
    from app.scheduler.jobs import run_data_quality_daily  # RED: not yet created

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with patch("app.scheduler.jobs.subprocess.run", return_value=mock_result):
        run_data_quality_daily()


def test_dq_job_logs_warning_on_exit_2() -> None:
    """exit=2 (unexpected error / unknown profile) → log.warning called (D-09).

    exit=2 means the runner hit an unknown profile (bug) — log warning but
    still do not raise.
    """
    from app.scheduler.jobs import run_data_quality_daily  # RED: not yet created

    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stderr = "Unknown profiles: ['bad_dataset']"

    with (
        patch("app.scheduler.jobs.subprocess.run", return_value=mock_result),
        patch("app.scheduler.jobs.log") as mock_log,
    ):
        run_data_quality_daily()

    # D-09: unexpected exit must be logged as warning, not raised
    mock_log.warning.assert_called_once()
    call_args = str(mock_log.warning.call_args)
    assert "2" in call_args or "exit" in call_args.lower(), (
        f"log.warning should mention exit code 2, got: {call_args}"
    )
