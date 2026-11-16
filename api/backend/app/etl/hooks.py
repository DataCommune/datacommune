"""ETL lifecycle hooks for alerting and monitoring.

Hooks are injected into PipelineRunner and called at key lifecycle points
without modifying individual pipeline code.
"""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any, ClassVar, Protocol

import psycopg

from app.common.communes_lookup_ddl import recreate_communes_lookup
from app.commune.matview_ddl import recreate_commune_synthese
from app.demographie.backfill import backfill_superficie_densite
from app.etl.publication import PublicationWork
from app.etl.schemas import EtlResult

log = logging.getLogger(__name__)


def _backfill_demographie_geometry_and_recreate_commune_synthese(
    conn: psycopg.Connection[Any],
) -> None:
    """Compose the D-07..D-09 surface/densite backfill with the
    v_commune_synthese rebuild into one logical operation sharing the
    caller's transaction. Publication lends its lock-owning connection;
    legacy calls open and commit one connection per matview entry.

    Order matters (43-PATTERNS.md landmine #7): refreshing the view before
    the backfill would publish stale superficie/densite. The backfill's own
    precondition repeat (``BackfillPreconditionError``) blocks the whole
    thing -- including the rebuild -- before any write, propagating up
    through ``on_run_complete``'s ``except Exception: ... raise`` so the
    caller's connection rolls back and no partial state is committed.
    """
    backfill_superficie_densite(conn)
    recreate_commune_synthese(conn)


class EtlHook(Protocol):
    """Protocol for ETL pipeline lifecycle callbacks."""

    def on_run_start(self, dataset_name: str) -> None: ...
    def on_run_complete(self, dataset_name: str, result: EtlResult) -> None: ...
    def on_run_failure(self, dataset_name: str, error: str) -> None: ...


class AlertHook:
    """Send Telegram notification via apprise on ETL failure.

    Includes cooldown to prevent alert floods when the same dataset
    fails repeatedly (e.g. during retry loops).
    """

    def __init__(self, alert_urls: list[str], cooldown_minutes: int = 60) -> None:
        self._urls = alert_urls
        self._cooldown_seconds = cooldown_minutes * 60
        self._last_alert: dict[str, float] = {}

    def on_run_start(self, dataset_name: str) -> None:
        pass  # no notification on start

    def on_run_complete(self, dataset_name: str, result: EtlResult) -> None:
        if not result.success:
            self._notify(
                dataset_name,
                title=f"ETL {dataset_name} FAILED",
                body=(
                    f"Error: {result.error}\n"
                    f"Duration: {result.total_duration_seconds:.0f}s"
                ),
            )

    def on_run_failure(self, dataset_name: str, error: str) -> None:
        self._notify(
            dataset_name,
            title=f"ETL {dataset_name} CRASHED",
            body=f"Unhandled error: {error}",
        )

    def send_alert(self, key: str, *, title: str, body: str) -> None:
        """Send a cooldown-deduplicated alert for ``key`` (public entry).

        Shared choke point for non-ETL callers routing to the same channel
        (decision 1, audit 2026-08-21: upstream schema-drift alerts).
        """
        self._notify(key, title=title, body=body)

    @property
    def enabled(self) -> bool:
        """False when no Apprise URL is configured (safe no-op mode)."""
        return bool(self._urls)

    def _notify(self, dataset_name: str, *, title: str, body: str) -> None:
        if not self._urls:
            return

        # Cooldown check: skip if last alert for this dataset was recent
        now = time.monotonic()
        last = self._last_alert.get(dataset_name, 0.0)
        if now - last < self._cooldown_seconds:
            log.debug(
                "Alert suppressed for %s (cooldown %ds)",
                dataset_name,
                self._cooldown_seconds,
            )
            return

        import apprise

        ap = apprise.Apprise()
        for url in self._urls:
            ap.add(url)
        ap.notify(title=title, body=body, notify_type=apprise.NotifyType.FAILURE)
        self._last_alert[dataset_name] = now
        log.info("Alert sent for %s: %s", dataset_name, title)


class DiskCheckHook:
    """Alert when free disk space drops below threshold.

    Checks disk usage on ``on_run_start`` so the alert fires *before*
    the pipeline tries to write data.  Deduplicates to one alert per hour.
    """

    _last_disk_alert: float = 0.0  # class-level, shared across instances

    def __init__(
        self,
        alert_urls: list[str],
        threshold_pct: float,
        path: str,
    ) -> None:
        self._urls = alert_urls
        self._threshold_pct = threshold_pct
        self._path = path

    def on_run_start(self, dataset_name: str) -> None:
        try:
            usage = shutil.disk_usage(self._path)
        except OSError:
            log.warning("Cannot check disk usage at %s", self._path)
            return

        free_pct = (usage.free / usage.total) * 100
        if free_pct >= self._threshold_pct:
            return

        # Dedup: one alert per hour max
        now = time.monotonic()
        if now - DiskCheckHook._last_disk_alert < 3600:
            return

        if not self._urls:
            return

        import apprise

        ap = apprise.Apprise()
        for url in self._urls:
            ap.add(url)
        free_gb = usage.free / (1024**3)
        ap.notify(
            title="DISK SPACE LOW",
            body=(
                f"Free: {free_pct:.1f}% ({free_gb:.1f} GB) at {self._path}\n"
                f"Threshold: {self._threshold_pct:.0f}%"
            ),
            notify_type=apprise.NotifyType.WARNING,
        )
        DiskCheckHook._last_disk_alert = now
        log.warning(
            "Disk space low: %.1f%% free at %s (threshold %.0f%%)",
            free_pct,
            self._path,
            self._threshold_pct,
        )

    def on_run_complete(self, dataset_name: str, result: EtlResult) -> None:
        pass  # no-op

    def on_run_failure(self, dataset_name: str, error: str) -> None:
        pass  # no-op


def _validated_publication_work(
    dataset_name: str, result: EtlResult
) -> PublicationWork | None:
    """Bind the hook route to the live candidate before validation or DDL."""
    if result.dataset_name != dataset_name:
        raise RuntimeError("Hook result dataset differs from its route")
    work = result._publication_work
    if work is not None:
        if not isinstance(work, PublicationWork):
            raise RuntimeError("Invalid publication work context")
        candidate = work.assert_current(work.connection)
        if candidate.dataset_name != dataset_name:
            raise RuntimeError("Hook dataset differs from the publication candidate")
    return work


class ValidationHook:
    """Require declared/DDL NULL policy before success metadata is published.

    Optional NULL rates remain WARN. Existing pipeline semantic gates run before
    swap; this hook does not claim an exhaustive domain/source certification.
    """

    required_on_complete: ClassVar[bool] = True

    def on_run_start(self, dataset_name: str) -> None:
        pass

    def on_run_complete(self, dataset_name: str, result: EtlResult) -> None:
        from app.config import settings
        from app.etl.publication_validation import validate_publication
        from app.etl.validation import persist_validation_run

        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

        if result.success:
            _validated_publication_work(dataset_name, result)
            from app.etl.registry import registry

            pipeline_cls = registry.get(dataset_name)
            if pipeline_cls is None:
                raise LookupError(f"No ETL pipeline registered for {dataset_name}")
            pipeline = pipeline_cls.__new__(pipeline_cls)
            validate_publication(
                dsn,
                dataset_name,
                pipeline.production_table_name,
                row_count_before=result.previous_row_count,
                row_count_after=(
                    result._publication_row_count
                    if result._publication_row_count is not None
                    else result.total_rows_loaded
                ),
            )
        else:
            # Check if this was a row regression block
            if result.error and "Row count regression" in result.error:
                try:
                    persist_validation_run(
                        dsn,
                        dataset_name,
                        row_count_before=result.previous_row_count,
                        row_count_after=result.total_rows_loaded,
                        null_rate_findings=[],
                        passed=False,
                        blocked_swap=True,
                        notes=result.error,
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "ValidationHook: failed to persist"
                        " blocked_swap for %s (ignored)",
                        dataset_name,
                        exc_info=True,
                    )

    def on_run_failure(self, dataset_name: str, error: str) -> None:
        pass


class RequiredMatviewFailure(RuntimeError):  # noqa: N818 — typed private outcome
    """Carry private failure context until the owner's DDL transaction rolls back."""

    def __init__(self, matview_name: str, original: Exception):
        super().__init__("Required dependent view rebuild failed")
        self.matview_name = matview_name
        self.original = original


class DependentMatviewHook:
    """Rebind materialized views after source-table rename swaps."""

    required_on_complete: ClassVar[bool] = True

    _DEPENDENCIES: ClassVar[
        dict[
            str,
            tuple[tuple[str, Callable[[psycopg.Connection[Any]], None]], ...],
        ]
    ] = {
        "ban": (("communes_lookup", recreate_communes_lookup),),
        "dpe": (("v_commune_synthese", recreate_commune_synthese),),
        # No scoring publication while COR-09 remains quarantined. Cadastre
        # also has no authoritative demographic geometry fallback (D-07).
        "dvf": (("v_commune_synthese", recreate_commune_synthese),),
        "demographie": (
            (
                "v_commune_synthese",
                _backfill_demographie_geometry_and_recreate_commune_synthese,
            ),
        ),
        "revenus": (("v_commune_synthese", recreate_commune_synthese),),
        "zonage_abc": (("v_commune_synthese", recreate_commune_synthese),),
        "vacance": (("v_commune_synthese", recreate_commune_synthese),),
        "fiscalite": (("v_commune_synthese", recreate_commune_synthese),),
        "loyers": (("v_commune_synthese", recreate_commune_synthese),),
        "equipements": (("v_commune_synthese", recreate_commune_synthese),),
        "scolaire": (("v_commune_synthese", recreate_commune_synthese),),
        "monuments": (("v_commune_synthese", recreate_commune_synthese),),
        "foncier_pm": (("v_commune_synthese", recreate_commune_synthese),),
        "connectivite": (("v_commune_synthese", recreate_commune_synthese),),
    }

    def on_run_start(self, dataset_name: str) -> None:
        pass

    def on_run_complete(self, dataset_name: str, result: EtlResult) -> None:
        if not result.success:
            return

        work = _validated_publication_work(dataset_name, result)
        refreshers = self._DEPENDENCIES.get(dataset_name, ())
        if not refreshers:
            return

        from app.config import settings

        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        for matview_name, recreate in refreshers:
            started = time.monotonic()
            if work is not None:
                work.assert_current(work.connection)
            try:
                with (
                    nullcontext(work.connection)
                    if work is not None
                    else psycopg.connect(dsn)
                ) as conn:
                    recreate(conn)
                    duration = time.monotonic() - started
                    conn.execute(
                        """
                        INSERT INTO matview_metadata (
                            matview_name,
                            last_refresh_at,
                            last_refresh_duration_seconds,
                            last_refresh_status,
                            last_error
                        )
                        VALUES (%s, NOW(), %s, 'success', NULL)
                        ON CONFLICT (matview_name) DO UPDATE SET
                            last_refresh_at = EXCLUDED.last_refresh_at,
                            last_refresh_duration_seconds =
                                EXCLUDED.last_refresh_duration_seconds,
                            last_refresh_status = 'success',
                            last_error = NULL
                        """,
                        (matview_name, duration),
                    )
                    if work is None:
                        conn.commit()
                log.info(
                    "Rebuilt dependent matview %s after %s ETL",
                    matview_name,
                    dataset_name,
                )
            except Exception as exc:
                if work is not None:
                    # Recording on another connection here can wait on our own
                    # uncommitted metadata. The runner records only after rollback.
                    raise RequiredMatviewFailure(matview_name, exc) from exc
                self._record_failure(dsn, matview_name, exc)
                raise

    def on_run_failure(self, dataset_name: str, error: str) -> None:
        pass

    @staticmethod
    def _write_failure(conn, matview_name: str, exc: Exception) -> None:
        conn.execute(
            """
            INSERT INTO matview_metadata
                (matview_name,last_refresh_status,last_error)
            VALUES (%s,'failed',%s)
            ON CONFLICT (matview_name) DO UPDATE SET
                last_refresh_status='failed',last_error=EXCLUDED.last_error
            """,
            (matview_name, str(exc)[:500]),
        )

    @staticmethod
    def _record_failure(dsn: str, matview_name: str, exc: Exception) -> None:
        try:
            with psycopg.connect(dsn) as conn:
                DependentMatviewHook._write_failure(conn, matview_name, exc)
                conn.commit()
        except Exception:
            log.exception(
                "Failed to record dependent matview error for %s",
                matview_name,
            )


def build_default_hooks() -> list:
    """Build the standard hook list for ETL pipeline execution.

    Used by both scheduler/jobs.py and run_etl.py to ensure consistent
    hook configuration across scheduled and manual ETL runs.
    """
    from app.config import settings

    return [
        AlertHook(settings.alert_urls, settings.alert_cooldown_minutes),
        DiskCheckHook(
            settings.alert_urls,
            settings.disk_alert_threshold_pct,
            settings.etl_temp_dir,
        ),
        ValidationHook(),
        DependentMatviewHook(),
    ]
