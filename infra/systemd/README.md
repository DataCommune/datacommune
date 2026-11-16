# DataCommune maintenance timers — systemd (Phase 37, OPS-01)

Replaced the in-container APScheduler for the D-04 maintenance jobs with
host-level `systemd` timers — no second container, no duplicated prod secrets
(D-01, T-37-03). The scheduler container was already off in prod, and the
process itself was removed from the repository in plan 01-05 (D-05): these
timers are now the only scheduling path. Each job runs via
`docker exec` into the already-running API container, invoking the Plan 01
CLI wrapper: `python -m app.scheduler.run_job <job-key>`.

**This directory is IaC only.** Host application on LXC 275 is performed
under supervision in Plan 05 — nothing here mutates a running host.

---

## Files

| File | Role |
|------|------|
| `datacommune-job@.service` | Parameterized oneshot unit. `%i` = job key, passed to `run_job.py`. |
| `datacommune-job@<key>.timer` | One per job (7 total). Schedules the matching `.service` instance. |

## The 7 available timers

`<key>` MUST match a key in `api/backend/app/scheduler/run_job.py`'s `JOBS` dict exactly.

| Timer instance (`<key>`) | Cadence (`OnCalendar=`) | Cadence d'origine (ancien `scheduler/main.py`, retire en 01-05) |
|---------------------------|-------------------------|----------------------------------------|
| `refresh-commune-synthese` | `*-*-* 06:00:00` | `0 6 * * *` |
| `refresh-communes-lookup` | `*-*-* 06:10:00` | `10 6 * * *` |
| `cleanup-old-tables` | `hourly` | `0 * * * *` |
| `cleanup-orphan-staging` | `*-*-* 01:00:00` | `0 1 * * *` |
| `check-freshness` | `*-*-* 09:00:00` | `0 9 * * *` |
| `cleanup-api-log` | `Sun *-*-* 01:00:00` | `0 1 * * 0` |
| `synthetic-probe` | `*-*-* 08:30:00` + `RandomizedDelaySec=1800` | — (Plan 46-01, HEALTH-02) |

`synthetic-probe` (HEALTH-02, Plan 46-01) replays the private golden case
against the live `/v1/resolve` contract and raises fail-closed on any D-03
regression → non-zero exit → `systemd` marks the unit failed. It requires a
host-managed fixture and an API key; **its activation is gated behind the
supervised checkpoint 46-01-03** (see `api/docs/monitoring-runbook.md` §5) and
must NOT be enabled by the generic install loop below until then.

`refresh-scoring-renov` is quarantined: its CLI entrypoint deliberately returns
exit code 2 before connecting to the database. Its retired timer is no longer
shipped or enabled by this installation. Existing hosts require the explicit
retirement procedure below; removing an IaC file does not stop a deployed timer.

The scheduler's ETL-cron-audit job (RISK-3, see Plan 01 SUMMARY) is
deliberately excluded here: it reads the ETL cron table, which is
intentionally empty on this scoped instance and would produce a guaranteed
daily false-positive alert. It has no timer in this directory and no key in
`run_job.py`'s `JOBS` dict.

---

## Install (LXC 275, Plan 05 — supervised)

```bash
# 1. Copy all units to the systemd unit directory
sudo cp infra/systemd/datacommune-job@.service /etc/systemd/system/
sudo cp infra/systemd/datacommune-job@*.timer /etc/systemd/system/

# 2. Reload systemd's unit cache
sudo systemctl daemon-reload

# 3. Enable + start the 6 ordinary maintenance timers
sudo systemctl enable --now datacommune-job@refresh-commune-synthese.timer
sudo systemctl enable --now datacommune-job@refresh-communes-lookup.timer
sudo systemctl enable --now datacommune-job@cleanup-old-tables.timer
sudo systemctl enable --now datacommune-job@cleanup-orphan-staging.timer
sudo systemctl enable --now datacommune-job@check-freshness.timer
sudo systemctl enable --now datacommune-job@cleanup-api-log.timer
```

## Verify

```bash
# Six ordinary timers; synthetic-probe only when separately qualified
systemctl list-timers 'datacommune-job@*'

# Job output (journald, no explicit StandardOutput/StandardError directive —
# each oneshot's stdout/stderr goes to the unit's journal by default)
journalctl -u 'datacommune-job@*' --since today

# Single job's most recent run
journalctl -u 'datacommune-job@refresh-commune-synthese.service' -n 50
```

## Retire the installed scoring timer at corrective cutover

Run only on the intended application host, coordinated with the release. First
check that the service is inactive; stopping its timer does not stop a job that
has already started. If it is running, coordinate its completion before cutover.

```bash
sudo systemctl show datacommune-job@refresh-scoring-renov.service \
    --property=ActiveState,SubState,Result,ExecMainStatus
sudo systemctl disable --now datacommune-job@refresh-scoring-renov.timer
sudo systemctl show datacommune-job@refresh-scoring-renov.timer \
    --property=UnitFileState,ActiveState,SubState,NextElapseUSecRealtime,LastTriggerUSec
```

Expected: `UnitFileState=disabled`, `ActiveState=inactive`, `SubState=dead`,
no next trigger, and no new invocation. Retain the previous last-trigger and
service result as history. Do not start the quarantined job as a smoke test or
clear its historical failure state to manufacture success. The installed file
can remain disabled; no shared unit template or other timer needs modification.

The in-container scheduler also omitted the quarantined `permis` and
`encadrement_loyers` ETL jobs and removed their persisted APScheduler entries
before resuming. That process was removed in plan 01-05, so no persisted
jobstore is written any more and no such reconciliation runs; a host that still
carries rows in the old jobstore table keeps them inert. This directory does not
manage host timers for these datasets, and no payment timer is defined here.

## Catch-up semantics (`Persistent=true`)

Every timer sets `Persistent=true`: if the LXC is down when a timer would
have fired (reboot, maintenance window), systemd runs the missed job once at
the next boot instead of silently skipping it. This matters most for the
low-frequency jobs (`cleanup-api-log` is weekly).

## Account-data backup

The account-data backup no longer runs on the production host: the hypervisor pulls an archive
encrypted there by `infra/public/accounts-dump.sh`, keeps it on its USB disk, copies it to
the VPS and restores it daily into a throw-away database (plan 05-01). Units and
installation: the private hypervisor scripts (`dc-accounts-backup.{service,timer}`, D-140).

## Manual one-off run

```bash
sudo systemctl start datacommune-job@refresh-commune-synthese.service
```

Runs immediately, independent of the timer schedule — useful for testing a
single job without waiting for its next `OnCalendar=` trigger.
