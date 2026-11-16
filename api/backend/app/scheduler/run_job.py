"""Thin CLI entry point for manual/systemd-triggered maintenance jobs.

Usage: python -m app.scheduler.run_job <job-name>
Calls existing app.scheduler.jobs functions directly — no APScheduler,
no event loop (all target functions are sync/psycopg). Intended to be
invoked from systemd timers via `docker exec ... python -m
app.scheduler.run_job <job-name>` (Plan 02, D-02).

`check_missing_runs` is deliberately excluded (RISK-3): it reads the
ETL cron table which is intentionally empty on this instance, which
would produce a guaranteed daily false-positive alert.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.common.unqualified_surfaces import SurfaceQuarantinedError, unavailable
from app.scheduler.jobs import (
    check_dataset_freshness,
    cleanup_api_request_log,
    cleanup_old_tables,
    cleanup_orphan_staging,
    purge_old_tables,
    refresh_commune_synthese_view,
    refresh_communes_lookup,
    refresh_scoring_renov_target,
)
from app.scheduler.monitoring import FreshnessMonitoringError, FreshnessReport
from app.scheduler.synthetic_probe import run_synthetic_probe

# Locked set of maintenance jobs — one host timer per key (infra/systemd/),
# except `purge-old-tables`, run by hand (plan 04.1-04): it drops every
# previous generation at once, without TTL.
# `synthetic-probe` (HEALTH-02, Plan 46-01) replays the private golden case
# daily and raises fail-closed on any regression, so a failure produces a
# non-zero exit and systemd marks the unit failed.
JOBS = {
    "refresh-commune-synthese": refresh_commune_synthese_view,
    "refresh-communes-lookup": refresh_communes_lookup,
    "refresh-scoring-renov": refresh_scoring_renov_target,
    "cleanup-api-log": cleanup_api_request_log,
    "check-freshness": check_dataset_freshness,
    "cleanup-old-tables": cleanup_old_tables,
    "cleanup-orphan-staging": cleanup_orphan_staging,
    "purge-old-tables": purge_old_tables,
    "synthetic-probe": run_synthetic_probe,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_job")
    parser.add_argument("job", choices=sorted(JOBS))
    args = parser.parse_args()
    try:
        result = JOBS[args.job]()
    except SurfaceQuarantinedError as exc:
        print(unavailable(exc.surface).model_dump_json())
        return 2
    except FreshnessMonitoringError:
        print(json.dumps({"status": "unavailable", "notification": "not_attempted"}))
        return 2
    if isinstance(result, FreshnessReport):
        print(result.model_dump_json())
        return result.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
