"""Reproduce OPS-05/06 with the real runner and scheduler entrypoints."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from tenacity import wait_none

from app.etl.errors import transient_cause
from app.etl.runner import PipelineRunner, _StageError
from app.etl.schemas import EtlResult
from app.scheduler import jobs, run_job
from tests.etl.test_runner import FakePipeline, _patch_all


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    # FakePipeline is synthetic: its name is not a catalogue key.
    host_test_datasets("test_ds")


@pytest.mark.parametrize("failure", ["timeout_once", "timeout_always", "business"])
def test_real_runner_retry_contract(monkeypatch, tmp_path, failure):
    pipeline = FakePipeline()
    source = tmp_path / "synthetic.csv"
    source.write_bytes(b"id\nsynthetic\n")
    error = (
        httpx.ReadTimeout("synthetic timeout")
        if failure != "business"
        else ValueError("synthetic invalid source")
    )
    download = Mock(
        side_effect=[error, [source]] if failure == "timeout_once" else error
    )
    monkeypatch.setattr(pipeline, "download", download)
    monkeypatch.setattr(pipeline, "drop_staging_table", Mock())
    monkeypatch.setitem(jobs.PIPELINE_MAP, "cor19", lambda: pipeline)
    monkeypatch.setattr(jobs, "build_default_hooks", lambda: [])
    monkeypatch.setattr(
        jobs.run_scheduled_pipeline_with_retry.retry, "wait", wait_none()
    )
    with _patch_all(staging_exists=False, checkpoint_data=None):
        if failure == "timeout_once":
            jobs.run_scheduled_pipeline_with_retry("cor19")
            assert download.call_count == 2
        else:
            expected = (
                httpx.ReadTimeout
                if failure == "timeout_always"
                else jobs.ScheduledPipelineError
            )
            with pytest.raises(expected):
                jobs.run_scheduled_pipeline_with_retry("cor19")
            assert download.call_count == (3 if failure == "timeout_always" else 1)


@pytest.fixture()
def freshness(monkeypatch):
    now = datetime(2026, 9, 8, tzinfo=UTC)
    conn = MagicMock()
    conn.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: [("ban", now)]),
        SimpleNamespace(fetchall=lambda: [("ban",)]),
    ]
    connect = MagicMock()
    connect.return_value.__enter__.return_value = conn
    monkeypatch.setattr(jobs.psycopg, "connect", connect)
    monkeypatch.setattr(jobs, "FRESHNESS_THRESHOLDS", {"ban": 30})
    monkeypatch.setattr(jobs, "SCHEDULES", {})
    notifier = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules,
        "apprise",
        SimpleNamespace(
            Apprise=lambda: notifier, NotifyType=SimpleNamespace(WARNING="warning")
        ),
    )
    return conn, notifier


@pytest.mark.parametrize("state", ["fresh", "stale", "outdated"])
@pytest.mark.parametrize("notification", ["none", "sent", "false", "exception"])
def test_freshness_verdict_independent_of_notification(
    monkeypatch, freshness, state, notification, caplog
):
    _, notifier = freshness
    monkeypatch.setattr(jobs, "compute_freshness", lambda *_a, **_kw: (state, "sain"))
    monkeypatch.setattr(
        jobs.settings,
        "alert_urls",
        [] if notification == "none" else ["synthetic://not-sent"],
    )
    notifier.notify.return_value = notification != "false"
    if notification == "exception":
        notifier.notify.side_effect = RuntimeError("synthetic notification failure")
    report = jobs.check_dataset_freshness()
    assert report.status == ("healthy" if state == "fresh" else "degraded")
    assert report.checked == 1
    expected_notification = (
        "not_needed"
        if state == "fresh"
        else {
            "none": "not_configured",
            "sent": "sent",
            "false": "failed",
            "exception": "failed",
        }[notification]
    )
    assert report.notification == expected_notification
    if state != "fresh":
        assert "all 1 checked datasets are fresh" not in caplog.text


@pytest.mark.parametrize("failed_query", [0, 1])
def test_freshness_sql_failure_cli_is_nonzero(monkeypatch, freshness, failed_query):
    conn, notifier = freshness
    first = SimpleNamespace(fetchall=lambda: [("ban", datetime.now(UTC))])
    conn.execute.side_effect = (
        [RuntimeError("synthetic SQL failure")]
        if failed_query == 0
        else [first, RuntimeError("synthetic SQL failure")]
    )
    monkeypatch.setattr("sys.argv", ["run_job", "check-freshness"])
    assert run_job.main() == 2
    notifier.notify.assert_not_called()


@pytest.mark.parametrize("state,expected_exit", [("fresh", 0), ("stale", 1)])
def test_freshness_cli_uses_verdict(monkeypatch, freshness, state, expected_exit):
    monkeypatch.setattr(jobs, "compute_freshness", lambda *_a, **_kw: (state, "sain"))
    monkeypatch.setattr(jobs.settings, "alert_urls", [])
    monkeypatch.setattr("sys.argv", ["run_job", "check-freshness"])
    assert run_job.main() == expected_exit


def test_cleanup_sql_failure_reaches_supervisor(monkeypatch):
    monkeypatch.setattr(
        jobs.psycopg, "connect", Mock(side_effect=RuntimeError("synthetic SQL failure"))
    )
    monkeypatch.setattr("sys.argv", ["run_job", "cleanup-api-log"])
    with pytest.raises(RuntimeError):
        run_job.main()


def test_failure_cause_is_private_and_cycle_safe():
    cause = httpx.ReadTimeout("synthetic-private-marker")
    wrapper = _StageError("download failed")
    wrapper.__cause__ = cause
    result = EtlResult(
        dataset_name="synthetic",
        success=False,
        error="download failed",
        stages=[],
        total_rows_loaded=0,
        total_duration_seconds=0,
    )
    result.remember_failure(wrapper)
    assert transient_cause(result.failure_cause) is cause
    assert "synthetic-private-marker" not in result.model_dump_json()
    assert "failure_cause" not in str(result.model_json_schema())
    cycle = ValueError("permanent")
    cycle.__cause__ = cycle
    assert transient_cause(cycle) is None


@pytest.mark.parametrize("stage", ["validate_schema", "transform", "stage", "swap"])
def test_real_runner_file_transient_is_not_swallowed(monkeypatch, tmp_path, stage):
    pipeline = FakePipeline()
    source = tmp_path / "synthetic.csv"
    source.write_bytes(b"id\nsynthetic\n")
    pipeline._files = [source]
    method = getattr(pipeline, stage)
    calls = []

    def fail_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic timeout")
        return method(*args)

    monkeypatch.setattr(pipeline, stage, fail_once)
    monkeypatch.setattr(pipeline, "drop_staging_table", Mock())
    monkeypatch.setitem(jobs.PIPELINE_MAP, "cor19", lambda: pipeline)
    monkeypatch.setattr(jobs, "build_default_hooks", lambda: [])
    monkeypatch.setattr(
        jobs.run_scheduled_pipeline_with_retry.retry, "wait", wait_none()
    )
    with _patch_all(staging_exists=False, checkpoint_data=None):
        jobs.run_scheduled_pipeline_with_retry("cor19")
    assert len(calls) == 2


def test_failed_stage_keeps_typed_cause_in_real_runner(monkeypatch):
    pipeline = FakePipeline()
    cause = httpx.ReadTimeout("synthetic timeout")
    monkeypatch.setattr(pipeline, "download", Mock(side_effect=cause))
    monkeypatch.setattr(pipeline, "drop_staging_table", Mock())
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = PipelineRunner().run(pipeline)
    assert not result.success
    assert transient_cause(result.failure_cause) is cause
