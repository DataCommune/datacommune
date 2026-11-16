"""Unit tests for the app.scheduler.run_job CLI dispatch wrapper.

Verify that:
  - `python -m app.scheduler.run_job <job>` calls the corresponding
    jobs.py function exactly once and exits 0
  - Each of the 8 kebab-case keys maps to its correct function
  - `check_missing_runs` is NOT reachable via any key (RISK-3)
  - An unknown job name exits non-zero (argparse choices error) without
    calling any job
  - A job raising an exception propagates to a non-zero process exit
  - The synthetic-probe job propagates its fail-closed exception (HEALTH-02)
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.scheduler import jobs as jobs_module
from app.scheduler import run_job
from app.scheduler import synthetic_probe as synthetic_probe_module

# The exact, locked set of maintenance jobs exposed by the host timers.
EXPECTED_JOB_KEYS = frozenset(
    {
        "refresh-commune-synthese",
        "refresh-communes-lookup",
        "refresh-scoring-renov",
        "cleanup-api-log",
        "check-freshness",
        "cleanup-old-tables",
        "cleanup-orphan-staging",
        "purge-old-tables",
        "synthetic-probe",
    }
)


def test_jobs_dict_has_exactly_nine_entries():
    assert len(run_job.JOBS) == 9


def test_jobs_dict_keys_are_the_exact_locked_set():
    assert set(run_job.JOBS) == EXPECTED_JOB_KEYS


def test_jobs_dict_maps_kebab_keys_to_correct_functions():
    expected = {
        "refresh-commune-synthese": jobs_module.refresh_commune_synthese_view,
        "refresh-communes-lookup": jobs_module.refresh_communes_lookup,
        "refresh-scoring-renov": jobs_module.refresh_scoring_renov_target,
        "cleanup-api-log": jobs_module.cleanup_api_request_log,
        "check-freshness": jobs_module.check_dataset_freshness,
        "cleanup-old-tables": jobs_module.cleanup_old_tables,
        "cleanup-orphan-staging": jobs_module.cleanup_orphan_staging,
        "purge-old-tables": jobs_module.purge_old_tables,
        "synthetic-probe": synthetic_probe_module.run_synthetic_probe,
    }
    assert expected == run_job.JOBS


def test_check_missing_runs_not_reachable_via_any_key():
    assert "check-missing-runs" not in run_job.JOBS
    assert "check_missing_runs" not in run_job.JOBS
    assert jobs_module.check_missing_runs not in run_job.JOBS.values()


@pytest.mark.parametrize("job_key", sorted(run_job.JOBS))
def test_main_dispatches_correct_job_exactly_once(monkeypatch, job_key):
    mock_job = Mock()
    monkeypatch.setitem(run_job.JOBS, job_key, mock_job)
    monkeypatch.setattr("sys.argv", ["run_job", job_key])

    exit_code = run_job.main()

    assert exit_code == 0
    mock_job.assert_called_once_with()


def test_main_unknown_job_name_exits_nonzero_without_calling_any_job(monkeypatch):
    mocks = {key: Mock() for key in run_job.JOBS}
    for key, mock_job in mocks.items():
        monkeypatch.setitem(run_job.JOBS, key, mock_job)
    monkeypatch.setattr("sys.argv", ["run_job", "not-a-real-job"])

    with pytest.raises(SystemExit) as exc_info:
        run_job.main()

    assert exc_info.value.code != 0
    for mock_job in mocks.values():
        mock_job.assert_not_called()


def test_main_job_exception_propagates_for_nonzero_process_exit(monkeypatch):
    failing_job = Mock(side_effect=RuntimeError("boom"))
    monkeypatch.setitem(run_job.JOBS, "cleanup-old-tables", failing_job)
    monkeypatch.setattr("sys.argv", ["run_job", "cleanup-old-tables"])

    with pytest.raises(RuntimeError, match="boom"):
        run_job.main()

    failing_job.assert_called_once_with()


def test_synthetic_probe_fail_closed_exception_propagates(monkeypatch):
    # A probe failure (fail-closed ProbeError) must traverse run_job so the
    # process exits non-zero and systemd marks the unit failed (HEALTH-02).
    failing_probe = Mock(
        side_effect=synthetic_probe_module.ProbeFailed([]),
    )
    monkeypatch.setitem(run_job.JOBS, "synthetic-probe", failing_probe)
    monkeypatch.setattr("sys.argv", ["run_job", "synthetic-probe"])

    with pytest.raises(synthetic_probe_module.ProbeError):
        run_job.main()

    failing_probe.assert_called_once_with()
