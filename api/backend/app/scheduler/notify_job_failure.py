"""Systemd ``OnFailure`` hook: alert when a datacommune-job unit fails at OS level.

Second line of defence behind the application-level alerting. If a scheduled
job crashes before or outside its own alert path (e.g. the container is down, a
module fails to import), the matching ``datacommune-job-alert@<key>.service``
runs this module so an operator is still notified. Only the opaque job key is
ever sent — never a private input.
"""

from __future__ import annotations

import logging
import re
import sys

from app.config import settings

log = logging.getLogger("scheduler.notify_job_failure")

# Job keys are opaque unit-instance names; refuse anything else so a stray
# argument can never smuggle a private value into an alert.
_OPAQUE_JOB_KEY = re.compile(r"^[A-Za-z0-9._:-]+$")


def notify_job_failure(job_key: str) -> bool:
    """Send a sanitized systemd-level failure alert. Returns delivery success.

    Fail-closed: with no alert channel configured there is nothing to deliver,
    which is reported as a failure (non-zero exit) rather than a silent success.
    """
    if not _OPAQUE_JOB_KEY.match(job_key):
        job_key = "unknown"
    if not settings.alert_urls:
        log.error("job_failure_no_alert_channel", extra={"job_key": job_key})
        return False

    import apprise

    ap = apprise.Apprise()
    for url in settings.alert_urls:
        ap.add(url)
    delivered = ap.notify(
        title=f"DataCommune job failed: {job_key}",
        body=f"systemd unit datacommune-job@{job_key} exited non-zero.",
        notify_type=apprise.NotifyType.FAILURE,
    )
    return bool(delivered)


def main() -> int:
    job_key = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    return 0 if notify_job_failure(job_key) else 1


if __name__ == "__main__":
    sys.exit(main())
