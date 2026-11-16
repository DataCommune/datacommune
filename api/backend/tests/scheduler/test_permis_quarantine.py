"""COR-07: direct scheduler retries cannot bypass quarantine.

The companion test for PERSISTED jobs (`test_startup_purges_persisted_permis_
before_dispatch`) was removed with the APScheduler process in plan 01-05: it
drove `app.scheduler.main.main()` through a jobstore double, and there is no
jobstore and no persisted job any more. The guard below is NOT scheduler-
specific — it covers the `jobs.py` entrypoints, which `run_job` and the systemd
timers still reach — so it stays.
"""

from unittest.mock import Mock

import pytest

from app.scheduler import jobs


@pytest.mark.parametrize(
    "entrypoint", [jobs.run_scheduled_pipeline, jobs.run_scheduled_pipeline_with_retry]
)
@pytest.mark.parametrize("dataset", ["permis", "encadrement_loyers"])
def test_quarantined_invocation_stops_before_lock_or_pipeline(
    monkeypatch, entrypoint, dataset
):
    lock = Mock(side_effect=AssertionError("quarantine must not access the DB"))
    pipeline = Mock(side_effect=AssertionError("quarantine must not build a pipeline"))
    monkeypatch.setattr("app.etl.publication.try_acquire_lock", lock)
    monkeypatch.setitem(jobs.PIPELINE_MAP, dataset, pipeline)
    entrypoint(dataset)
    lock.assert_not_called()
    pipeline.assert_not_called()
