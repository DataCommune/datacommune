"""Scheduled pipeline job with advisory lock protection and hook integration."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import psycopg
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_incrementing,
)

from app.common.communes_lookup_ddl import recreate_communes_lookup
from app.common.matviews import recreate_missing_matviews
from app.common.unqualified_surfaces import refuse
from app.commune.matview_ddl import recreate_commune_synthese
from app.config import settings
from app.datasets.freshness import compute_freshness
from app.etl.errors import (
    TRANSIENT_ERRORS,
    DatasetNotHostedError,
    EmptyScopeError,
    SourceWithoutCoverageError,
    transient_cause,
)
from app.etl.hooks import build_default_hooks
from app.etl.publication import maintenance_connection
from app.etl.runner import PipelineRunner
from app.etl_antennes.pipeline import AntennesPipeline
from app.etl_arcep.pipeline import ArcepPipeline
from app.etl_ban.pipeline import BanPipeline
from app.etl_bdtopo.pipeline import BdTopoPipeline
from app.etl_cadastre.pipeline import CadastrePipeline
from app.etl_copropriete.pipeline import CoproprietePipeline
from app.etl_demographie.pipeline import DemographiePipeline
from app.etl_dpe.pipeline import DpePipeline
from app.etl_dvf.pipeline import DvfPipeline
from app.etl_encadrement_loyers.pipeline import EncadrementLoyersPipeline
from app.etl_equipements.pipeline import EquipementsPipeline
from app.etl_fiscalite.pipeline import FiscalitePipeline
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_majic_pm.pipeline import MajicPmPipeline
from app.etl_monuments.pipeline import MonumentsPipeline
from app.etl_permis.pipeline import PermisPipeline
from app.etl_revenus.pipeline import RevenusPipeline
from app.etl_rnb_liens.pipeline import RnbLiensPipeline
from app.etl_scolaire.pipeline import ScolairePipeline
from app.etl_transport.pipeline import TransportPipeline
from app.etl_vacance.pipeline import VacancePipeline
from app.etl_zonage_abc.pipeline import ZonageAbcPipeline
from app.scheduler.config import FRESHNESS_THRESHOLDS, QUARANTINED_DATASETS, SCHEDULES
from app.scheduler.locks import _dataset_lock_id
from app.scheduler.monitoring import FreshnessMonitoringError, FreshnessReport

log = logging.getLogger("scheduler.jobs")


class ScheduledPipelineError(RuntimeError):
    """Terminal ETL failure visible to the scheduler, with no source payload."""


# Same mapping as run_etl.py -- maps CLI names to pipeline classes
PIPELINE_MAP: dict[str, type] = {
    "monuments": MonumentsPipeline,
    "loyers": LoyersPipeline,
    "transports": TransportPipeline,
    "scolaire": ScolairePipeline,
    "dvf": DvfPipeline,
    "dpe": DpePipeline,
    "ban": BanPipeline,
    "cadastre": CadastrePipeline,
    "batiments": BdTopoPipeline,
    "connectivite": ArcepPipeline,
    "encadrement_loyers": EncadrementLoyersPipeline,
    "fiscalite": FiscalitePipeline,
    "zonage_abc": ZonageAbcPipeline,
    "revenus": RevenusPipeline,
    "demographie": DemographiePipeline,
    "copropriete": CoproprietePipeline,
    "permis": PermisPipeline,
    "antennes": AntennesPipeline,
    "equipements": EquipementsPipeline,
    "vacance": VacancePipeline,
    "foncier_pm": MajicPmPipeline,
    "rnb_liens": RnbLiensPipeline,
}


def run_scheduled_pipeline(dataset_name: str) -> None:  # noqa: C901
    """Execute a single ETL pipeline with advisory lock protection.

    The shared PipelineRunner acquires the dataset advisory lock so CLI and
    scheduled runs obey the same exclusion rule through publication completion.
    Hooks provide failure alerting and disk-space monitoring.

    Exception semantics (used by the tenacity wrapper below):
      - Transient errors (TRANSIENT_ERRORS) are re-raised so tenacity retries.
        _StageError wrappers are unwrapped via __cause__ chain walk first.
      - Advisory-lock-held RuntimeError is logged and swallowed (no retry).
      - Unsuccessful results preserve their typed cause for transient retries.
      - Permanent failures raise ScheduledPipelineError without retry.
    """
    if dataset_name in QUARANTINED_DATASETS:
        log.warning("Scheduled pipeline %s blocked: source quarantined", dataset_name)
        return
    try:
        pipeline_cls = PIPELINE_MAP.get(dataset_name)
        if pipeline_cls is None:
            log.error("Unknown dataset: %s", dataset_name)
            return

        pipeline = pipeline_cls()
        runner = PipelineRunner(hooks=build_default_hooks())
        result = runner.run(pipeline)

        if result.success:
            log.info(
                "Pipeline %s completed: %d rows in %.1fs",
                dataset_name,
                result.total_rows_loaded,
                result.total_duration_seconds,
            )
        else:
            cause = transient_cause(result.failure_cause)
            if cause is not None:
                raise cause
            raise ScheduledPipelineError(f"Pipeline {dataset_name} failed")

    except DatasetNotHostedError:
        # Outside the instance scope: neither downloaded nor imported (D-22).
        log.warning("Scheduled pipeline %s skipped: dataset not hosted", dataset_name)
        return
    except EmptyScopeError:
        log.warning("Scheduled pipeline %s skipped: empty instance scope", dataset_name)
        return
    except SourceWithoutCoverageError:
        # The source publishes no hosted departement: nothing to import.
        log.warning(
            "Scheduled pipeline %s skipped: source without coverage", dataset_name
        )
        return
    except Exception as exc:  # noqa: BLE001
        cause = transient_cause(exc)
        if cause is not None:
            # Do not log exception messages: upstream payloads can contain URLs.
            log.warning(
                "Transient error in pipeline %s (%s)",
                dataset_name,
                type(cause).__name__,
            )
            raise cause from None

        if isinstance(exc, RuntimeError) and "advisory lock" in str(exc).lower():
            log.info("Pipeline %s skipped: advisory lock held", dataset_name)
            return

        log.error(  # noqa: TRY400
            "Pipeline %s failed (non-retryable: %s)", dataset_name, type(exc).__name__
        )
        raise ScheduledPipelineError(f"Pipeline {dataset_name} failed") from None


@retry(
    retry=retry_if_exception_type(TRANSIENT_ERRORS),
    stop=stop_after_attempt(3),
    wait=wait_incrementing(start=60, increment=60, max=120),
    reraise=True,
)
def run_scheduled_pipeline_with_retry(dataset_name: str) -> None:
    """Scheduler entrypoint: wraps run_scheduled_pipeline with transient retry.

    Retries on TRANSIENT_ERRORS only (network timeouts, broken connections,
    psycopg.OperationalError). Backoff: 60s before attempt 2, 120s before
    attempt 3. Total = 3 attempts. reraise=True ensures the final failure
    is visible in scheduler logs.

    On retry, the existing checkpoint/staging checks select a valid resume or
    a fresh run. A caught stage failure drops staging, so that case restarts.
    """
    run_scheduled_pipeline(dataset_name)


def cleanup_api_request_log() -> None:
    """Delete api_request_log rows older than 7 days.

    Runs as a weekly scheduled job to keep the monitoring table small.
    Uses a sync psycopg connection (matches the BlockingScheduler pattern).
    """
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        with psycopg.connect(dsn) as conn:
            result = conn.execute(
                "DELETE FROM api_request_log WHERE ts < NOW() - INTERVAL '7 days'"
            )
            deleted = result.rowcount
            conn.commit()
        log.info("api_request_log cleanup: deleted %d rows older than 7 days", deleted)
    except Exception:  # noqa: BLE001
        log.error("api_request_log cleanup failed")  # noqa: TRY400
        raise RuntimeError("api_request_log cleanup unavailable") from None
    # Same weekly pass: error reports past their retention (migration 114).
    purge_signalements()


def purge_signalements() -> int:
    """Delete the error reports older than their retention; return the count.

    The route purges on each write; this pass keeps the promise when nobody
    writes. A base where migration 114 is not applied has nothing to purge.
    """
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        with psycopg.connect(dsn) as conn:
            present = conn.execute(
                "SELECT to_regclass('public.signalements') IS NOT NULL"
            ).fetchone()
            if present is None or not present[0]:
                return 0
            deleted = conn.execute(
                "DELETE FROM signalements "
                "WHERE created_at < now() - make_interval(days => %s)",
                (settings.signalement_conservation_jours,),
            ).rowcount
            conn.commit()
    except Exception:  # noqa: BLE001
        log.error("signalements purge failed")  # noqa: TRY400
        raise RuntimeError("signalements purge unavailable") from None
    log.info("signalements purge: deleted %d rows past retention", deleted)
    return deleted


def refresh_commune_synthese_view() -> None:
    """Rebuild the v_commune_synthese materialized view (DROP + CREATE).

    Runs as a daily scheduled job (06:00 UTC), after the ETL window, to
    keep the commune synthesis view up-to-date when source datasets change.
    Uses a sync psycopg connection (matches the BlockingScheduler pattern).

    DROP + CREATE rather than REFRESH: the ETL swaps tables by renaming,
    which leaves the matview bound (by OID) to the stale ``_old`` table, so
    REFRESH would recompute from stale data. See app.commune.matview_ddl.

    Writes last_refresh_at + last_refresh_status to matview_metadata on
    both success and failure paths (review #1 finding #14).
    """
    _refresh_matview("v_commune_synthese", recreate_commune_synthese)


def _refresh_matview(
    matview: str, recreate: Callable[[psycopg.Connection], None]
) -> None:
    """Keep exclusion across rollback and failure/success metadata recording."""
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    t0 = time.monotonic()
    # An admission timeout must not enter the failure-metadata handler.
    with maintenance_connection(dsn) as conn:
        try:
            recreate(conn)
            duration = time.monotonic() - t0
            conn.execute(
                "UPDATE matview_metadata "
                "SET last_refresh_at = NOW(), "
                "    last_refresh_duration_seconds = %s, "
                "    last_refresh_status = 'success', "
                "    last_error = NULL "
                "WHERE matview_name = %s",
                (duration, matview),
            )
            conn.commit()
            log.info("%s refresh completed successfully (%.1fs)", matview, duration)
        except Exception as exc:
            conn.rollback()
            try:
                conn.execute(
                    "UPDATE matview_metadata "
                    "SET last_refresh_status = 'failed', "
                    "    last_error = %s "
                    "WHERE matview_name = %s",
                    (str(exc)[:500], matview),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                log.exception(
                    "%s refresh: failed to record failure in matview_metadata", matview
                )
            log.exception("%s refresh failed", matview)
            raise


def refresh_communes_lookup() -> None:
    """Rebuild communes_lookup against the current adresses table.

    Runs as a daily scheduled job (06:10 UTC).

    DROP + CREATE is required because the BAN ETL swaps tables by rename.
    REFRESH would remain bound to the stale ``adresses_old`` table by OID.

    Writes last_refresh_at + last_refresh_status to matview_metadata on
    both success and failure paths (review #1 finding #14).
    """
    _refresh_matview("communes_lookup", recreate_communes_lookup)


def refresh_scoring_renov_target() -> None:
    """Deprecated maintenance entrypoint: refuse before connection or metadata."""
    refuse("scoring_renov")


def initialize_matview_metadata() -> None:
    """Refresh matviews whose freshness metadata has never been initialized."""
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "SELECT matview_name FROM matview_metadata "
                "WHERE last_refresh_at IS NULL "
                "ORDER BY matview_name"
            ).fetchall()
    except Exception:
        log.exception("Failed to read uninitialized matview metadata")
        return

    refreshers = {
        "communes_lookup": refresh_communes_lookup,
        "v_commune_synthese": refresh_commune_synthese_view,
    }
    for (matview_name,) in rows:
        refresher = refreshers.get(matview_name)
        if refresher is None:
            log.warning("No startup refresher registered for %s", matview_name)
            continue
        try:
            log.info("Initializing freshness metadata for %s", matview_name)
            refresher()
        except Exception:
            # Individual refresh functions already record failure details.
            log.exception("Startup refresh failed for %s", matview_name)


def cleanup_old_tables() -> None:
    """Drop _old tables that exceeded their TTL (default 1h).

    Runs hourly to garbage-collect old production tables kept
    for short-window rollback after ETL swaps (SWAP-02).

    DROP, DELETE and matview repair share ONE transaction with a single
    COMMIT (API-4). A matview follows its source table through the ETL
    rename, so ``DROP TABLE <table>_old CASCADE`` takes it down; rebuilding
    it before the commit means readers never see a missing relation.

    If a rebuild fails, the transaction is rolled back whole and the error is
    re-raised: no DROP is committed, the registry keeps its rows, and the next
    hourly tick retries the exact same work. The disk space is deliberately
    not reclaimed in that case -- a missing matview on a public surface is
    worse than an _old table waiting one more hour.
    """
    ttl = settings.etl_old_table_ttl_hours
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        with maintenance_connection(dsn) as conn:
            rows = conn.execute(
                "SELECT id, table_name FROM etl_old_tables "
                "WHERE created_at < NOW() - make_interval(hours => %s)",
                (ttl,),
            ).fetchall()
            if not rows:
                return
            for row_id, table_name in rows:
                conn.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
                conn.execute("DELETE FROM etl_old_tables WHERE id = %s", (row_id,))
                log.info("Dropped expired old table %s (>%dh)", table_name, ttl)
            try:
                recreate_missing_matviews(conn, log)
            except Exception:
                # Explicit and observable: undo the DROPs rather than commit a
                # schema without its matviews.
                conn.rollback()
                raise
            conn.commit()
    except Exception:
        log.exception("Old table cleanup failed — rolled back, will retry next tick")
        raise


def purge_old_tables() -> list[str]:
    """Drop every previous generation now, without TTL (``purge-old-tables``).

    Targets every table of ``etl_old_tables`` plus the ``*_old`` names each
    pipeline declares (``published_old_tables``), which also catches the
    untracked leftovers of the code before plan 04.1-04 (e.g.
    ``mutation_parcelles_old``). Since that plan a publication drops its own
    ``_old`` table; this command clears what older runs left.

    Same transaction rule as every ``_old`` drop (API-4): a failed matview
    repair rolls the whole purge back and raises, so the command fails loudly.
    """
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    declared: list[str] = []
    for dataset_name, pipeline_cls in PIPELINE_MAP.items():
        try:
            declared.extend(pipeline_cls().published_old_tables)
        except Exception:  # noqa: BLE001
            log.warning("Could not instantiate %s for _old names", dataset_name)
    dropped = PipelineRunner._drop_old_tables(dsn, log, declared, tracked=True)
    log.info("Purged %d previous generation(s): %s", len(dropped), dropped)
    return dropped


def cleanup_orphan_staging() -> None:
    """Drop orphan *_staging* tables and sequences (CLEAN-01, CLEAN-02).

    Runs at 01:00 UTC daily — exactly 1 hour before the ETL window opens
    at 02:00 UTC. At 01:00 UTC no ETL pipeline is running, so advisory
    locks are guaranteed released and any *_staging* table found is safe
    to drop (it was left behind by a prior crash or kill).

    Safety rails:
      - Only touches tables in `public` schema whose name matches
        `*_staging*` or `_*_staging_flat` patterns (ETL naming convention).
      - Skips any table in `etl_old_tables` (tracked rollback tables).
      - Double-checks the dataset's advisory lock via pg_try_advisory_lock
        (belt-and-suspenders for the case where cron drifts or a manual
        run is launched right at 01:00 UTC).
      - For orphan sequences: only drops sequences with `_staging` in the
        name AND no pg_depend deptype='a' ownership — this explicitly
        excludes post-swap production sequences (Phase 9 pitfall).
    """
    # PIPELINE_MAP is a module-level name in this same file — reference it
    # directly. DO NOT write `from app.scheduler.jobs import PIPELINE_MAP`
    # (that would be a self-import and will fail at runtime).
    # _dataset_lock_id is imported at module level from app.scheduler.locks
    # alongside release_lock and try_acquire_lock (see line 33).

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # Build reverse map: staging_table_name -> dataset_name
    # (instantiating each pipeline class to read its staging_table_name property)
    staging_to_dataset: dict[str, str] = {}
    for dataset_name, pipeline_cls in PIPELINE_MAP.items():
        try:
            pipeline = pipeline_cls()
            staging_to_dataset[pipeline.staging_table_name] = dataset_name
            if pipeline.flat_staging_table_name:
                staging_to_dataset[pipeline.flat_staging_table_name] = dataset_name
        except Exception:  # noqa: BLE001
            log.warning("Could not instantiate %s for staging map", dataset_name)

    dropped_tables: list[str] = []
    dropped_sequences: list[str] = []

    try:
        with psycopg.connect(dsn) as conn:
            # --- CLEAN-01: orphan staging tables ---
            rows = conn.execute(
                """
                SELECT t.tablename
                FROM pg_tables t
                WHERE t.schemaname = 'public'
                  AND (
                    t.tablename LIKE '%\\_staging' ESCAPE '\\'
                    OR t.tablename LIKE '%\\_staging\\_%' ESCAPE '\\'
                    OR t.tablename LIKE '\\_%\\_staging\\_flat' ESCAPE '\\'
                  )
                  AND t.tablename NOT IN (
                    SELECT table_name FROM etl_old_tables
                  )
                """
            ).fetchall()

            for (tablename,) in rows:
                # Try to match this staging name (or a partition of it) back to a
                # dataset. Partition names look like `foncier_pm_staging_75` — strip
                # the _XX suffix to get back to the parent staging name.
                parent_staging = _derive_parent_staging(tablename, staging_to_dataset)
                dataset_name = staging_to_dataset.get(parent_staging)

                if dataset_name is None:
                    # Unknown naming pattern — skip for safety, log for investigation.
                    log.warning(
                        "Orphan staging cleanup: skipping %s (no dataset mapping)",
                        tablename,
                    )
                    continue

                # Advisory lock check: if we can acquire, no one else holds it.
                lock_id = _dataset_lock_id(dataset_name)
                acquired = conn.execute(
                    "SELECT pg_try_advisory_lock(%s)", (lock_id,)
                ).fetchone()[0]

                if not acquired:
                    log.info(
                        "Orphan staging cleanup: skipping %s (lock held by active run)",
                        tablename,
                    )
                    continue
                try:
                    conn.execute(f"DROP TABLE IF EXISTS {tablename} CASCADE")
                    dropped_tables.append(tablename)
                    log.info("Dropped orphan staging table %s", tablename)
                finally:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))

            # --- CLEAN-02: orphan sequences ---
            seq_rows = conn.execute(
                """
                SELECT s.relname
                FROM pg_class s
                JOIN pg_namespace n ON n.oid = s.relnamespace
                WHERE s.relkind = 'S'
                  AND n.nspname = 'public'
                  AND s.relname LIKE '%\\_staging%' ESCAPE '\\'
                  AND NOT EXISTS (
                    SELECT 1 FROM pg_depend d
                    WHERE d.objid = s.oid AND d.deptype = 'a'
                  )
                """
            ).fetchall()

            for (seqname,) in seq_rows:
                conn.execute(f"DROP SEQUENCE IF EXISTS {seqname} CASCADE")
                dropped_sequences.append(seqname)
                log.info("Dropped orphan sequence %s", seqname)

            conn.commit()

        if dropped_tables or dropped_sequences:
            log.info(
                "Orphan staging cleanup complete: %d tables, %d sequences",
                len(dropped_tables),
                len(dropped_sequences),
            )
        else:
            log.info("Orphan staging cleanup: nothing to drop")

    except Exception:
        log.exception("Orphan staging cleanup failed")


def _derive_parent_staging(
    table_name: str,
    staging_to_dataset: dict[str, str],
) -> str:
    """Map a potentially partitioned staging table name back to its parent.

    Examples:
      foncier_pm_staging_75 -> foncier_pm_staging
      _ban_staging_flat     -> _ban_staging_flat (already parent)
      dpe_staging_default   -> dpe_staging
    """
    # Exact match first
    if table_name in staging_to_dataset:
        return table_name
    # Strip _<dept> or _default suffix: the rightmost underscore-segment
    # is the partition key if the prefix matches a known parent.
    if "_" in table_name:
        parent = table_name.rsplit("_", 1)[0]
        if parent in staging_to_dataset:
            return parent
    return table_name  # unknown, will be skipped by caller


def run_all_validators() -> None:
    """Run all registered Phase 16 validators and check drift.

    Scheduled daily at 08:00 UTC (outside the ETL window 02:00-05:00).
    Uses sync psycopg — matches the BlockingScheduler pattern.
    """
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # Import validators to trigger @register_validator decorators
    from app.validation.base import registry
    from app.validation.drift import detect_drift
    from app.validation.validators import ban, cadastre, dpe, dvf  # noqa: F401

    validators = registry.get_all()
    log.info("Running %d validators", len(validators))

    for name, validator_cls in validators.items():
        try:
            validator = validator_cls()
            result = validator.run(dsn)
            status = "PASS" if result.passed else "FAIL"
            log.info(
                "Validator %s: %s (local=%d, source=%s)",
                name,
                status,
                result.local_row_count,
                result.source_row_count or "N/A",
            )
        except Exception:
            log.exception("Validator %s failed", name)
            continue

        # Drift detection per dataset
        try:
            drift = detect_drift(dsn, name)
            if drift:
                log.warning(
                    "Drift detected for %s: %.1f%% %s (prev=%d, current=%d)",
                    name,
                    drift["drift_pct"],
                    drift["direction"],
                    drift["previous_count"],
                    drift["current_count"],
                )
        except Exception:
            log.exception("Drift detection failed for %s", name)

    log.info("All validators completed")


def run_data_quality_daily() -> None:
    """Run generic data-quality audit daily. Log exit code, do NOT raise (D-09).

    Scheduled daily at 07:00 UTC (créneau libre between matviews 06:10 and
    validators 08:00).

    out_dir: settings.dq_out_dir (default /tmp/audit-dq, overridable via env
    DQ_OUT_DIR — always writable in container, no repository mount required).

    Exit code 1 (rouge) is expected for known-bad datasets (e.g. equipements
    Lambert-93). D-09: the job MUST NOT raise on exit=1 — it logs and persists.
    Exit >= 2 = unexpected bug — log warning but still do not raise.
    """
    out_dir = settings.dq_out_dir
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["python", "-m", "app.data_quality.run", "-o", out_dir],
        cwd="/app",
        capture_output=True,
        text=True,
        check=False,  # CRITICAL: never check=True — exit=1 is expected (D-09)
    )
    log.info("[dq] daily run exit=%d out_dir=%s", result.returncode, out_dir)
    if result.returncode not in (0, 1):
        log.warning(
            "[dq] daily run unexpected exit=%d stderr=%.500s",
            result.returncode,
            result.stderr,
        )
    # D-09: never raise — exit=1 attendu (equipements Lambert) ne doit pas
    # faire échouer le job chaque nuit.


def check_dataset_freshness() -> FreshnessReport:  # noqa: C901
    """Check freshness of all datasets and alert on stale/outdated ones.

    Runs daily at 09:00 UTC (after ETL window 02:00-05:00 to avoid false
    positives). Queries etl_runs for last successful run per dataset, then
    calls compute_freshness() with each dataset's threshold and schedule.

    Skips datasets with:
    - freshness_threshold_days=None (quarantined, e.g. permis)
    - no run history at all (first deploy — avoids false positive on boot)
    """
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "SELECT dataset_name, MAX(completed_at) AS last_success "
                "FROM etl_runs WHERE status = 'success' "
                "GROUP BY dataset_name"
            ).fetchall()
    except Exception:  # noqa: BLE001
        log.error("check_dataset_freshness: failed to query etl_runs")  # noqa: TRY400
        raise FreshnessMonitoringError("Freshness check unavailable") from None

    last_success: dict[str, datetime | None] = {row[0]: row[1] for row in rows}
    # Track datasets that have ANY run history (success or not) to avoid
    # false positives on first deploy (Pitfall 4).
    try:
        with psycopg.connect(dsn) as conn:
            ran_rows = conn.execute(
                "SELECT DISTINCT dataset_name FROM etl_runs"
            ).fetchall()
    except Exception:  # noqa: BLE001
        log.error("check_dataset_freshness: failed to query run history")  # noqa: TRY400
        raise FreshnessMonitoringError("Freshness check unavailable") from None
    datasets_with_history: set[str] = {row[0] for row in ran_rows}

    alerts: list[str] = []
    checked = 0
    for dataset, threshold_days in FRESHNESS_THRESHOLDS.items():
        # Skip quarantined datasets (threshold=None)
        if threshold_days is None:
            continue
        # Skip datasets with no run history — first deploy guard
        if dataset not in datasets_with_history:
            continue

        checked += 1
        last = last_success.get(dataset)
        fraicheur, _etat = compute_freshness(
            last,
            threshold_days,
            cron_schedule=None,  # D-12: manual updates, same policy as /v1/health
        )
        if fraicheur in ("stale", "outdated"):
            last_str = last.isoformat() if last else "never"
            alerts.append(f"  {dataset}: {fraicheur} (last success: {last_str})")

    report = FreshnessReport(
        status="degraded" if alerts else "healthy",
        checked=checked,
        alerts=alerts,
        notification="not_configured" if alerts else "not_needed",
    )
    if alerts:
        log.warning(
            "check_dataset_freshness: %d of %d checked datasets are stale/outdated",
            len(alerts),
            checked,
        )
        if settings.alert_urls:
            try:
                import apprise

                ap = apprise.Apprise()
                for url in settings.alert_urls:
                    ap.add(url)
                sent = ap.notify(
                    title="Dataset freshness alert",
                    body="Stale or outdated datasets:\n" + "\n".join(alerts),
                    notify_type=apprise.NotifyType.WARNING,
                )
                report.notification = "sent" if sent else "failed"
            except Exception:  # noqa: BLE001
                report.notification = "failed"
            log.warning("Freshness notification: %s", report.notification)
    else:
        log.info("check_dataset_freshness: all %d checked datasets are fresh", checked)
    return report


def check_missing_runs() -> None:
    """Detect ETL pipelines that missed their scheduled run beyond grace period.

    Computes the next expected fire time after the last run, then checks if
    now > next_fire_time + grace_days.

    Skips datasets with no run history at all (first deploy guard).

    CURRENTLY UNREFERENCED (plan 01-05). Its only caller was the APScheduler
    process, which registered it daily at 09:30 UTC; that process was removed.
    It is deliberately NOT exposed through ``run_job`` (RISK-3: the ETL cron
    table is intentionally empty on this instance, so a timer would produce a
    guaranteed daily false positive — see ``run_job.py`` and the guard in
    ``tests/scheduler/test_run_job.py``). Kept, not deleted: D-05 removes the
    scheduler, not the missed-run detection, which is rewired when a scheduler
    is chosen in phase 2 or 5.
    """
    from datetime import UTC, datetime, timedelta

    from apscheduler.triggers.cron import CronTrigger

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "SELECT dataset_name, MAX(started_at) AS last_run "
                "FROM etl_runs "
                "GROUP BY dataset_name"
            ).fetchall()
    except Exception:
        log.exception("check_missing_runs: failed to query etl_runs")
        return

    last_run_map: dict[str, object] = {row[0]: row[1] for row in rows}
    now = datetime.now(UTC)
    grace = timedelta(days=settings.etl_schedule_grace_days)

    alerts: list[str] = []
    for dataset, cron_expr in SCHEDULES.items():
        last_run = last_run_map.get(dataset)
        # Skip datasets with no run history (first deploy guard)
        if last_run is None:
            continue

        try:
            trigger = CronTrigger.from_crontab(cron_expr)
            next_fire = trigger.get_next_fire_time(None, last_run)
            if next_fire is None:
                continue
            if now > next_fire + grace:
                alerts.append(
                    f"  {dataset}: last run {last_run.isoformat()}, "
                    f"expected by {next_fire.isoformat()}"
                )
        except Exception:  # noqa: BLE001
            log.debug("check_missing_runs: schedule parse failed for %s", dataset)

    if alerts and settings.alert_urls:
        import apprise

        ap = apprise.Apprise()
        for url in settings.alert_urls:
            ap.add(url)
        ap.notify(
            title="Missing ETL runs",
            body="Pipelines that missed their schedule:\n" + "\n".join(alerts),
            notify_type=apprise.NotifyType.WARNING,
        )
        log.warning("Missing run alert sent for %d datasets", len(alerts))
    else:
        log.info("check_missing_runs: no missing runs detected")
