"""Unit tests for scheduler/jobs.py tenacity retry logic.

Tests verify that:
  - TRANSIENT_ERRORS trigger retry (up to 3 attempts)
  - Permanent errors (ValueError, RuntimeError non-advisory) do NOT retry
  - Gate failures (EtlResult.success=False) raise without retry
  - Advisory-lock RuntimeError does NOT retry
  - _StageError wrapping a transient cause is surfaced and retried
  - Exhausted transient errors (3 failures) re-raise the final exception

Plus cleanup_old_tables (API-4): the expected matviews taken down by a
``DROP TABLE ... CASCADE`` are rebuilt inside the SAME transaction, before the
single COMMIT, and a failed rebuild rolls the whole cleanup back.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import psycopg
import pytest
from tenacity import stop_after_attempt, wait_none

from app.common.communes_lookup_ddl import recreate_communes_lookup
from app.common.matviews import EXPECTED_MATVIEWS
from app.commune.matview_ddl import recreate_commune_synthese
from app.etl.runner import _StageError
from app.etl.schemas import EtlResult
from app.scheduler.jobs import (
    TRANSIENT_ERRORS,
    ScheduledPipelineError,
    cleanup_old_tables,
    run_scheduled_pipeline,
    run_scheduled_pipeline_with_retry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATASET = "monuments"


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    """Eliminate tenacity wait delays so tests run instantly."""
    run_scheduled_pipeline_with_retry.retry.wait = wait_none()


@pytest.fixture()
def _mock_lock():
    """Runner doubles do not acquire DB locks; real exclusion is tested separately."""
    pass


@pytest.fixture()
def _mock_pipeline(monkeypatch):
    """Return a MagicMock for PipelineRunner so no real ETL runs."""
    mock_runner_cls = MagicMock()
    monkeypatch.setattr("app.scheduler.jobs.PipelineRunner", mock_runner_cls)
    monkeypatch.setattr("app.scheduler.jobs.build_default_hooks", lambda: [])
    monkeypatch.setattr(
        "app.scheduler.jobs.settings",
        MagicMock(database_url="postgresql+asyncpg://user:pw@localhost/db"),
    )
    return mock_runner_cls


def _make_ok_result() -> EtlResult:
    return EtlResult(
        dataset_name=DATASET,
        stages=[],
        total_rows_loaded=100,
        total_duration_seconds=1.0,
        success=True,
    )


def _make_fail_result(msg: str = "gate failed") -> EtlResult:
    return EtlResult(
        dataset_name=DATASET,
        stages=[],
        total_rows_loaded=0,
        total_duration_seconds=0.5,
        success=False,
        error=msg,
    )


# ---------------------------------------------------------------------------
# TRANSIENT_ERRORS tuple sanity
# ---------------------------------------------------------------------------


def test_transient_errors_tuple():
    assert httpx.TimeoutException in TRANSIENT_ERRORS
    assert httpx.ConnectError in TRANSIENT_ERRORS
    assert httpx.RemoteProtocolError in TRANSIENT_ERRORS
    assert ConnectionResetError in TRANSIENT_ERRORS
    assert psycopg.OperationalError in TRANSIENT_ERRORS
    # Explicitly excluded
    assert ValueError not in TRANSIENT_ERRORS
    assert RuntimeError not in TRANSIENT_ERRORS
    assert OSError not in TRANSIENT_ERRORS


# ---------------------------------------------------------------------------
# retry_with_retry: transient retries
# ---------------------------------------------------------------------------


def test_transient_retried_then_succeeds(_mock_lock, _mock_pipeline, monkeypatch):
    """httpx.TimeoutException on first two calls, success on third."""
    call_count = 0

    def fake_run(pipeline):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.TimeoutException("timeout")
        return _make_ok_result()

    _mock_pipeline.return_value.run.side_effect = fake_run

    # Reset to 3 attempts for this test
    run_scheduled_pipeline_with_retry.retry.stop = stop_after_attempt(3)

    # Should not raise — third attempt succeeds
    run_scheduled_pipeline_with_retry(DATASET)
    assert call_count == 3


def test_transient_connect_error_retried(_mock_lock, _mock_pipeline):
    """httpx.ConnectError triggers retry."""
    call_count = 0

    def fake_run(pipeline):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.ConnectError("connection refused")
        return _make_ok_result()

    _mock_pipeline.return_value.run.side_effect = fake_run
    run_scheduled_pipeline_with_retry.retry.stop = stop_after_attempt(3)

    run_scheduled_pipeline_with_retry(DATASET)
    assert call_count == 2


def test_psycopg_operational_error_retried(_mock_lock, _mock_pipeline):
    """psycopg.OperationalError triggers retry."""
    call_count = 0

    def fake_run(pipeline):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise psycopg.OperationalError("connection reset")
        return _make_ok_result()

    _mock_pipeline.return_value.run.side_effect = fake_run
    run_scheduled_pipeline_with_retry.retry.stop = stop_after_attempt(3)

    run_scheduled_pipeline_with_retry(DATASET)
    assert call_count == 2


def test_transient_exhausted_reraises(_mock_lock, _mock_pipeline):
    """3 consecutive transient errors: final exception re-raised (reraise=True)."""
    _mock_pipeline.return_value.run.side_effect = httpx.TimeoutException("timeout")
    run_scheduled_pipeline_with_retry.retry.stop = stop_after_attempt(3)

    with pytest.raises(httpx.TimeoutException):
        run_scheduled_pipeline_with_retry(DATASET)

    assert _mock_pipeline.return_value.run.call_count == 3


# ---------------------------------------------------------------------------
# run_scheduled_pipeline: permanent errors — no retry (called once)
# ---------------------------------------------------------------------------


def test_value_error_not_retried(_mock_lock, _mock_pipeline):
    """ValueError is a permanent failure — not retried at scheduler level."""
    _mock_pipeline.return_value.run.side_effect = ValueError("bad data")

    # Permanent failure reaches the supervisor without retry.
    with pytest.raises(ScheduledPipelineError):
        run_scheduled_pipeline(DATASET)
    assert _mock_pipeline.return_value.run.call_count == 1


def test_gate_failure_not_retried(_mock_lock, _mock_pipeline):
    """EtlResult.success=False is visible without retry."""
    _mock_pipeline.return_value.run.return_value = _make_fail_result("row regression")

    with pytest.raises(ScheduledPipelineError):
        run_scheduled_pipeline(DATASET)
    assert _mock_pipeline.return_value.run.call_count == 1


def test_gate_failure_not_retried_via_wrapper(_mock_lock, _mock_pipeline):
    """Gate failures don't trigger retry even through the tenacity wrapper."""
    _mock_pipeline.return_value.run.return_value = _make_fail_result("gate failed")
    run_scheduled_pipeline_with_retry.retry.stop = stop_after_attempt(3)

    with pytest.raises(ScheduledPipelineError):
        run_scheduled_pipeline_with_retry(DATASET)
    # Called exactly once — gate failure is structured and non-retryable
    assert _mock_pipeline.return_value.run.call_count == 1


# ---------------------------------------------------------------------------
# Advisory lock: not retried, not propagated
# ---------------------------------------------------------------------------


def test_advisory_lock_not_retried(_mock_lock, _mock_pipeline):
    """RuntimeError with 'advisory lock' in message: logged and swallowed."""
    _mock_pipeline.return_value.run.side_effect = RuntimeError(
        "Advisory lock already held by pid 1234"
    )

    run_scheduled_pipeline(DATASET)  # must not raise
    assert _mock_pipeline.return_value.run.call_count == 1


def test_advisory_lock_case_insensitive(_mock_lock, _mock_pipeline):
    """Case-insensitive check for advisory lock message."""
    _mock_pipeline.return_value.run.side_effect = RuntimeError("ADVISORY LOCK held")

    run_scheduled_pipeline(DATASET)
    assert _mock_pipeline.return_value.run.call_count == 1


def test_non_advisory_runtime_error_visible(_mock_lock, _mock_pipeline):
    """Non-advisory RuntimeError is permanent — visible, not retried."""
    _mock_pipeline.return_value.run.side_effect = RuntimeError("schema check failed")

    with pytest.raises(ScheduledPipelineError):
        run_scheduled_pipeline(DATASET)
    assert _mock_pipeline.return_value.run.call_count == 1


# ---------------------------------------------------------------------------
# _StageError cause walk: transient wrapped in _StageError is retried
# ---------------------------------------------------------------------------


def test_stage_error_wrapping_transient_is_retried(_mock_lock, _mock_pipeline):
    """_StageError(cause=httpx.ConnectError) → __cause__ walk surfaces the transient."""
    call_count = 0

    def fake_run(pipeline):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            original = httpx.ConnectError("connection refused")
            msg = "Stage 'download' failed: connection refused"
            raise _StageError(msg) from original
        return _make_ok_result()

    _mock_pipeline.return_value.run.side_effect = fake_run
    run_scheduled_pipeline_with_retry.retry.stop = stop_after_attempt(3)

    # The cause walk in run_scheduled_pipeline re-raises httpx.ConnectError,
    # which tenacity sees and retries.
    run_scheduled_pipeline_with_retry(DATASET)
    assert call_count == 3


def test_stage_error_wrapping_permanent_not_retried(_mock_lock, _mock_pipeline):
    """_StageError(cause=ValueError) → permanent, not retried."""
    original = ValueError("invalid encoding")
    exc = _StageError("Stage 'validate' failed: invalid encoding")
    exc.__cause__ = original
    _mock_pipeline.return_value.run.side_effect = exc

    with pytest.raises(ScheduledPipelineError):
        run_scheduled_pipeline(DATASET)
    assert _mock_pipeline.return_value.run.call_count == 1


# ---------------------------------------------------------------------------
# Lock not acquired: skip immediately
# ---------------------------------------------------------------------------


def test_lock_not_acquired_skips(monkeypatch, _mock_pipeline):
    """The shared runner refuses before execution; scheduler does not retry."""
    _mock_pipeline.return_value.run.side_effect = RuntimeError("Advisory lock held")
    run_scheduled_pipeline_with_retry(DATASET)
    _mock_pipeline.return_value.run.assert_called_once()


# ---------------------------------------------------------------------------
# cleanup_old_tables: matview repair inside the SAME transaction (API-4)
# ---------------------------------------------------------------------------

_ALL_MATVIEWS = ("v_commune_synthese", "communes_lookup")


def _sole_index(events: list[str], needle: str) -> int:
    """Index of the single event containing ``needle`` (asserts uniqueness)."""
    matches = [i for i, event in enumerate(events) if needle in event]
    assert len(matches) == 1, f"expected exactly one {needle!r} in {events}"
    return matches[0]


def _make_cleanup_conn(
    old_rows: list[tuple[int, str]],
    present_matviews: tuple[str, ...],
) -> tuple[MagicMock, list[str]]:
    """psycopg connection double: ordered event log + canned query results."""
    events: list[str] = []
    conn = MagicMock()

    def _execute(sql, params=None):
        events.append(sql)
        result = MagicMock()
        if "pg_matviews" in sql:
            result.fetchall.return_value = [(name,) for name in present_matviews]
        elif "FROM etl_old_tables" in sql:
            result.fetchall.return_value = list(old_rows)
        else:
            result.fetchall.return_value = []
        return result

    conn.execute.side_effect = _execute
    conn.commit.side_effect = lambda: events.append("COMMIT")
    conn.rollback.side_effect = lambda: events.append("ROLLBACK")
    return conn, events


@pytest.fixture()
def _cleanup_conn(monkeypatch):
    """Install fake settings + psycopg connection for cleanup_old_tables."""

    def _install(old_rows, present_matviews):
        monkeypatch.setattr(
            "app.scheduler.jobs.settings",
            MagicMock(
                database_url="postgresql+asyncpg://u:pw@localhost/db",
                etl_old_table_ttl_hours=1,
            ),
        )
        conn, events = _make_cleanup_conn(old_rows, present_matviews)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=ctx))
        return conn, events

    return _install


@pytest.fixture()
def _recreate_spy(monkeypatch):
    """Replace every canonical DDL rebuilder with a recording double."""

    def _install(events: list[str], failing: str | None = None) -> list[str]:
        recreated: list[str] = []

        def _make(name: str):
            def _fake(conn):
                events.append(f"RECREATE {name}")
                recreated.append(name)
                if failing == name:
                    raise RuntimeError(f"cannot rebuild {name}")

            return _fake

        for name in list(EXPECTED_MATVIEWS):
            monkeypatch.setitem(EXPECTED_MATVIEWS, name, _make(name))
        return recreated

    return _install


def test_expected_matviews_registry_points_at_canonical_ddl():
    """The shared registry wires each name to its canonical recreate_* DDL."""
    assert list(EXPECTED_MATVIEWS) == list(_ALL_MATVIEWS)
    assert EXPECTED_MATVIEWS["v_commune_synthese"] is recreate_commune_synthese
    assert EXPECTED_MATVIEWS["communes_lookup"] is recreate_communes_lookup
    assert "scoring_renov_target" not in EXPECTED_MATVIEWS


def test_cleanup_rebuilds_matview_dropped_by_cascade(_cleanup_conn, _recreate_spy):
    """API-4: a DROP ... CASCADE that took v_commune_synthese is repaired."""
    conn, events = _cleanup_conn(
        [(1, "revenus_old")], ("communes_lookup", "scoring_renov_target")
    )
    recreated = _recreate_spy(events)

    cleanup_old_tables()

    assert recreated == ["v_commune_synthese"]
    conn.rollback.assert_not_called()
    # Exactly ONE commit, and it comes after the rebuild: the DROP, the DELETE
    # and the rebuild share a single transaction.
    assert conn.commit.call_count == 1
    assert events.count("COMMIT") == 1
    drop = _sole_index(events, "DROP TABLE IF EXISTS revenus_old CASCADE")
    probe = _sole_index(events, "pg_matviews")
    rebuild = _sole_index(events, "RECREATE v_commune_synthese")
    assert drop < probe < rebuild < events.index("COMMIT")


def test_cleanup_is_idempotent_when_no_matview_is_missing(_cleanup_conn, _recreate_spy):
    """Healthy schema: one probe, zero DDL."""
    conn, events = _cleanup_conn([(1, "revenus_old")], _ALL_MATVIEWS)
    recreated = _recreate_spy(events)

    cleanup_old_tables()

    assert recreated == []
    assert len([e for e in events if "pg_matviews" in e]) == 1
    assert conn.commit.call_count == 1


def test_cleanup_without_expired_rows_never_probes_pg_matviews(
    _cleanup_conn, _recreate_spy
):
    """Nothing dropped means nothing can have been cascaded away."""
    conn, events = _cleanup_conn([], ())
    recreated = _recreate_spy(events)

    cleanup_old_tables()

    assert recreated == []
    assert not [e for e in events if "pg_matviews" in e]
    conn.commit.assert_not_called()


def test_cleanup_rolls_back_when_a_rebuild_fails(_cleanup_conn, _recreate_spy):
    """Failed rebuild: full ROLLBACK, no commit, exception propagated."""
    conn, events = _cleanup_conn([(1, "revenus_old")], ())
    recreated = _recreate_spy(events, failing="v_commune_synthese")

    with pytest.raises(RuntimeError, match="cannot rebuild v_commune_synthese"):
        cleanup_old_tables()

    assert recreated == ["v_commune_synthese"]
    conn.rollback.assert_called()
    conn.commit.assert_not_called()
    assert "COMMIT" not in events
    # The DELETE was issued but never committed.
    delete = _sole_index(events, "DELETE FROM etl_old_tables")
    assert events.index("ROLLBACK") > delete
