"""Shared freshness computation for datasets endpoint and health check.

Combines calendar-age freshness (existing logic) with schedule-adherence
checking: if a dataset missed its last scheduled run by more than
``grace_days``, it is considered stale even if the calendar threshold
says "fresh".

Review #1 finding #13: etat_pipeline now derived from last_success_at +
last_run_status so a failed run past its cron window degrades the status
even when the prior success is recent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.config import settings
from app.scheduler.config import FRESHNESS_THRESHOLDS, QUARANTINED_DATASETS

logger = logging.getLogger(__name__)

# "unknown": the schedule-adherence check could not run (KI-05). It ranks
# above "fresh" so it is never served healthy, and below the calendar delays,
# which are known facts an unreadable schedule must not hide.
_SEVERITY = {"fresh": 0, "unknown": 1, "stale": 2, "outdated": 3}


def _now() -> datetime:
    """Return the current UTC time. Patchable seam for testing."""
    return datetime.now(UTC)


def manual_update_threshold(
    dataset_name: str, stored_threshold_days: int | None
) -> int | None:
    """Freshness threshold served for a dataset under manual updates (D-12).

    The thresholds of ``app.scheduler.config`` win over the value stored in
    ``dataset_metadata``: the stored one dates from the scheduler and travels
    with every published base, so changing it would need a migration. A
    quarantined source makes no freshness promise (None), whatever its row
    says. A dataset missing from the table keeps its stored value.
    """
    if dataset_name in QUARANTINED_DATASETS:
        return None
    return FRESHNESS_THRESHOLDS.get(dataset_name, stored_threshold_days)


def compute_freshness(
    last_success_at: datetime | None,
    threshold_days: int | None,
    cron_schedule: str | None = None,
    last_run_status: str | None = None,
    last_run_started_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[str | None, Literal["sain", "degrade", "echec"]]:
    """Return (fraicheur, etat_pipeline) for a dataset.

    Args:
        last_success_at: Timestamp of the most recent successful ETL run
            (from etl_runs WHERE status='success'). Replaces the old
            ``last_updated`` (dataset_metadata.last_updated_at) to ensure
            freshness is derived from the actual pipeline outcome.
        threshold_days: Freshness SLA in calendar days. None = quarantined.
        cron_schedule: Cron expression for schedule-adherence checking.
        last_run_status: Status of the most recent run (any status).
        last_run_started_at: started_at of the most recent run (any status).
        now: Reference timestamp for freshness computation. Defaults to the
            current UTC time via _now(). Pass an explicit value in tests to
            make assertions date-independent.

    Returns:
        (fraicheur, etat_pipeline) where:
          - fraicheur is 'fresh' | 'unknown' | 'stale' | 'outdated' | None;
            'unknown' means the schedule check failed (never 'fresh');
            None means no threshold (quarantined, no promise)
          - etat_pipeline is 'sain' | 'degrade' | 'echec'
    """
    if now is None:
        now = _now()

    if threshold_days is None:
        return (None, "sain")

    # Calendar-based check (original logic, now driven by last_success_at)
    if last_success_at is None:
        calendar = "outdated"
    else:
        threshold = timedelta(days=threshold_days)
        age = now - last_success_at
        if age < threshold:
            calendar = "fresh"
        elif age < threshold * 2:
            calendar = "stale"
        else:
            calendar = "outdated"

    # Schedule-adherence check
    schedule = _check_schedule_adherence(last_success_at, cron_schedule, now=now)

    # fraicheur = worst of calendar and schedule adherence
    fraicheur: str = max(calendar, schedule, key=lambda s: _SEVERITY[s])

    # etat_pipeline: checks for failed run past the last success
    etat_pipeline = _compute_etat_pipeline(
        fraicheur=fraicheur,
        last_run_status=last_run_status,
        last_run_started_at=last_run_started_at,
        last_success_at=last_success_at,
        cron_schedule=cron_schedule,
        now=now,
    )

    return (fraicheur, etat_pipeline)


def _compute_etat_pipeline(
    fraicheur: str,
    last_run_status: str | None,
    last_run_started_at: datetime | None,
    last_success_at: datetime | None,
    cron_schedule: str | None,
    now: datetime,
) -> Literal["sain", "degrade", "echec"]:
    """Derive etat_pipeline from run state and freshness.

    Logic:
    - echec: last run failed AND started AFTER the last success AND the run
      is past the expected cron window (i.e. a new cycle started + failed).
    - degrade: fraicheur is unknown, stale or outdated (freshness not
      established, or data aging, even if no explicit failure recorded).
    - sain: everything looks healthy.
    """
    # Check for a confirmed failure past the last success point
    if (
        last_run_status == "failed"
        and last_run_started_at is not None
        and last_run_started_at > (last_success_at or datetime.min.replace(tzinfo=UTC))
        and _is_past_cron_window(last_run_started_at, cron_schedule, now=now)
    ):
        return "echec"

    # Degrade on unverifiable, stale or outdated freshness
    if fraicheur in ("unknown", "stale", "outdated"):
        return "degrade"

    return "sain"


def _is_past_cron_window(
    run_started_at: datetime,
    cron_schedule: str | None,
    now: datetime,
) -> bool:
    """Return True if the given run started after the last expected fire time.

    When cron_schedule is None we conservatively return True (the failed run
    is visible regardless — no schedule means no cron window to hide behind).
    """
    if not cron_schedule:
        return True

    try:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(cron_schedule)
        # Get the fire time that was expected before now
        # next_fire AFTER run_started_at tells us: if next_fire <= now,
        # a new cycle has started and the failure is definitely past-window.
        next_fire = trigger.get_next_fire_time(None, run_started_at)
        if next_fire is None:
            return True
        return now >= next_fire
    except Exception:  # noqa: BLE001
        logger.debug("_is_past_cron_window: schedule parse failed", exc_info=True)
        return True  # fail open: surface the failure


def _check_schedule_adherence(
    last_updated: datetime | None,
    cron_schedule: str | None,
    now: datetime,
) -> str:
    """Check if the dataset was updated after its last scheduled run.

    Returns 'fresh' if schedule check is not applicable, and 'unknown' if
    it applies but cannot be computed (apscheduler missing, unparsable cron,
    trigger error): an uncomputable check is never reported healthy (KI-05).
    """
    if not cron_schedule or last_updated is None:
        return "fresh"

    grace_days = settings.etl_schedule_grace_days

    try:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(cron_schedule)
        # Next expected run AFTER the last update
        next_run = trigger.get_next_fire_time(None, last_updated)
        if next_run is None:
            return "fresh"

        grace = timedelta(days=grace_days)
        if now <= next_run + grace:
            return "fresh"  # not yet due or still within grace
        if now <= next_run + (grace * 2):
            return "stale"  # missed schedule beyond grace
        return "outdated"  # missed schedule beyond two grace windows
    except Exception:  # noqa: BLE001
        logger.debug("Schedule adherence check failed", exc_info=True)
        return "unknown"  # fail closed: never served as fresh
