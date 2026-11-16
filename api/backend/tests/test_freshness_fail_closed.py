"""KI-05: a freshness that cannot be computed is never served as healthy.

The schedule-adherence check depends on ``apscheduler`` to read the cron.
Before 04.2-06 any failure there (library absent, unparsable cron, trigger
error) fell back to ``fresh``, so a dataset that missed its cron by days was
served ``fresh``/``sain`` whenever its calendar age was still under the
threshold. The failure must now surface as ``unknown`` freshness with a
``degrade`` pipeline state: distinct from ``sain`` (healthy), from
``stale``/``outdated`` (late, a known fact) and from ``echec`` (failed run).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import pytest

from app.datasets.freshness import compute_freshness
from app.datasets.router import build_dataset_info

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
# Calendar age (9 days) under the 30-day threshold, but a daily cron missed
# for 9 days: the KI-05 case.
_LAST_SUCCESS = _NOW - timedelta(days=9)
_DAILY = "0 3 * * *"


def _break_cron_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    from apscheduler.triggers import cron

    def _boom(*_args, **_kwargs):
        raise RuntimeError("trigger unavailable")

    monkeypatch.setattr(cron.CronTrigger, "from_crontab", _boom)


def _remove_apscheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry in sys.modules makes ``import`` raise ImportError.
    monkeypatch.setitem(sys.modules, "apscheduler.triggers.cron", None)


@pytest.mark.parametrize("breaker", [_break_cron_trigger, _remove_apscheduler])
def test_schedule_check_failure_is_not_healthy(monkeypatch, breaker):
    breaker(monkeypatch)
    fraicheur, etat = compute_freshness(
        last_success_at=_LAST_SUCCESS,
        threshold_days=30,
        cron_schedule=_DAILY,
        last_run_status="success",
        last_run_started_at=_LAST_SUCCESS,
        now=_NOW,
    )
    assert fraicheur != "fresh"
    assert etat != "sain"
    assert (fraicheur, etat) == ("unknown", "degrade")


def test_schedule_check_failure_keeps_a_known_delay(monkeypatch):
    """A calendar delay is a known fact: an unknown schedule does not hide it."""
    _break_cron_trigger(monkeypatch)
    fraicheur, etat = compute_freshness(
        last_success_at=_NOW - timedelta(days=90),
        threshold_days=30,
        cron_schedule=_DAILY,
        now=_NOW,
    )
    assert (fraicheur, etat) == ("outdated", "degrade")


def test_schedule_check_failure_does_not_mask_a_failed_run(monkeypatch):
    _break_cron_trigger(monkeypatch)
    fraicheur, etat = compute_freshness(
        last_success_at=_LAST_SUCCESS,
        threshold_days=30,
        cron_schedule=_DAILY,
        last_run_status="failed",
        last_run_started_at=_NOW - timedelta(days=1),
        now=_NOW,
    )
    assert fraicheur == "unknown"
    assert etat == "echec"


def test_working_schedule_check_still_reports_the_missed_cron():
    """Control: with apscheduler working, the KI-05 case is late, not fresh."""
    fraicheur, etat = compute_freshness(
        last_success_at=_LAST_SUCCESS,
        threshold_days=30,
        cron_schedule=_DAILY,
        last_run_status="success",
        last_run_started_at=_LAST_SUCCESS,
        now=_NOW,
    )
    assert fraicheur in ("stale", "outdated")
    assert etat == "degrade"


def test_datasets_entry_ignores_the_cron_under_manual_updates(monkeypatch):
    """D-12 (2026-11-13): no scheduler runs, so /v1/datasets no longer reads the
    stored cron. A broken cron reader changes nothing: the entry is judged on the
    threshold alone (dvf: 270 days, 9 days old -> fresh). KI-05 stays proved on
    compute_freshness itself above, for callers that still pass a schedule."""
    _break_cron_trigger(monkeypatch)
    info = build_dataset_info(
        {
            "dataset_name": "dvf",
            "status": "disponible",
            "last_updated_at": _LAST_SUCCESS,
            "row_count": 10,
            "last_import_duration_seconds": 1.0,
            "cron_schedule": _DAILY,
            "freshness_threshold_days": 30,
            "last_run_status": "success",
            "last_run_started_at": _LAST_SUCCESS,
            "last_run_completed_at": _LAST_SUCCESS,
            "last_run_duration": 1.0,
            "last_run_rows": 10,
            "last_run_error": None,
            "last_success_at": _LAST_SUCCESS,
        },
        now=_NOW,
    )
    assert info.fraicheur == "fresh"
    assert info.etat_pipeline == "sain"
