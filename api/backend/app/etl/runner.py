"""Pipeline orchestrator with structured logging, error handling, and persistence."""

import gzip
import hashlib
import json
import logging
import re
import shutil
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql

from app.common.matviews import recreate_missing_matviews
from app.config import settings
from app.etl.base import DEPARTEMENTS, BasePipeline, advisory_lock
from app.etl.checkpoint import CheckpointManager, is_logical_file_id
from app.etl.download import check_disk_space, cleanup_download_dir, get_download_dir
from app.etl.errors import (
    DatasetNotHostedError,
    EmptyScopeError,
    FatalInputError,
    SourceWithoutCoverageError,
    transient_cause,
)
from app.etl.publication import (
    Publication,
    dataset_execution_lock,
    maintenance_connection,
    requires_fresh_start,
)
from app.etl.schemas import EtlResult, EtlStageResult
from app.etl.source_archive import (
    SourceArchiveError,
    SourceReplay,
    archive_sources,
    compose_run_id,
    expand_source_paths,
)
from app.scope.model import InstanceScope
from app.scope.store import load_scope_sync

ScopeLoader = Callable[[str], InstanceScope]

# Only previous generations renamed by a swap are ever dropped by name.
_OLD_TABLE_NAME = re.compile(r"[a-z_][a-z0-9_]*_old")

# maintenance_connection() waits 250 ms for the publication lock; readers hold
# it shared, so a busy API can refuse one admission (todo 2026-10-13).
_OLD_DROP_ATTEMPTS = 3
_OLD_DROP_RETRY_S = 1.0


def default_scope_loader(dsn: str) -> InstanceScope:
    """Read the persisted instance scope on a dedicated connection (D-21).

    Production has no scope default: an empty table means nothing is hosted.
    """
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        return load_scope_sync(conn)


class PipelineRunner:
    """Execute a BasePipeline through its 5 stages.

    Each stage is timed and logged.  On failure the runner attempts
    cleanup (drop staging + flat staging) and returns a structured
    EtlResult with ``success=False``.
    """

    def __init__(
        self,
        hooks: list | None = None,
        scope_loader: ScopeLoader | None = None,
    ) -> None:
        self._hooks = hooks or []
        # None resolves default_scope_loader at run time (module lookup), so
        # a test can substitute it without touching every runner it builds.
        self._scope_loader = scope_loader

    def _load_scope(self, pipeline: BasePipeline) -> InstanceScope:
        """Read the scope once, refuse before any journal row or download.

        The scope is read from the application database
        (``BasePipeline.get_sync_dsn()``, i.e. ``settings.database_url``), never
        from the pipeline's own DSN: dry runs must not derive the pipeline DSN,
        and the scope is an application fact. Consequence: a pipeline that
        overrides ``get_sync_dsn`` to target another database is still gated
        by the application database's scope, and a dry run still needs that
        database to be readable.
        """
        loader = self._scope_loader or default_scope_loader
        # The scope belongs to the application database (settings), not to a
        # pipeline's target: dry runs still never derive their pipeline DSN.
        scope = loader(BasePipeline.get_sync_dsn())
        if scope.is_empty:
            raise EmptyScopeError()
        for key in pipeline.scope_dataset_keys:
            if not scope.hosts(key):
                raise DatasetNotHostedError(key)
        # Expected departements = scope ∩ source coverage (catalogue
        # source_coverage, via the pipeline): nothing to download when empty.
        outside = pipeline.departements_outside_source
        if all(dept in outside for dept in scope.departements):
            raise SourceWithoutCoverageError(pipeline.dataset_name)
        pipeline.bind_scope(scope)
        # Departmental pipelines check schema locks on any scope; filtered
        # national files need their references only on a restricted scope.
        if pipeline.supports_department_scope or (
            pipeline.scope_filter_sql is not None and not scope.covers_all_departements
        ):
            pipeline.check_scope_prerequisites(BasePipeline.get_sync_dsn())
        return scope

    def _call_hooks(
        self,
        method: str,
        *args: object,
        log: logging.Logger | None = None,
        skip_required: bool = False,
        required_only: bool = False,
    ) -> list[Exception]:
        """Collect required completion failures; optional hooks stay best-effort."""
        failures = []
        for hook in self._hooks:
            required = (
                method == "on_run_complete"
                and getattr(hook, "required_on_complete", False) is True
            )
            if (required and skip_required) or (required_only and not required):
                continue
            try:
                getattr(hook, method)(*args)
            except Exception as exc:  # noqa: BLE001
                _log = log or logging.getLogger(__name__)
                _log.warning(
                    "Hook %s.%s error (%s)",
                    type(hook).__name__,
                    method,
                    "required completion failed" if required else "ignored",
                    exc_info=True,
                )
                if required:
                    failures.append(exc)
                    if required_only:
                        return failures
        return failures

    def run(
        self,
        pipeline: BasePipeline,
        *,
        dry_run: bool = False,
        source_replay: SourceReplay | None = None,
    ) -> EtlResult:
        """Run all stages and release optional private resources on every exit.

        With *source_replay* (``run_etl --from-archive``, already verified by
        ``load_replay``), the archived files replace ``pipeline.download()``.
        """
        try:
            if dry_run:
                return self._run(pipeline, dry_run=True, source_replay=source_replay)
            with dataset_execution_lock(pipeline.get_sync_dsn(), pipeline.dataset_name):
                return self._run(pipeline, dry_run=False, source_replay=source_replay)
        finally:
            close = getattr(pipeline, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "Pipeline resource cleanup failed", exc_info=True
                    )

    def _run(  # noqa: C901
        self,
        pipeline: BasePipeline,
        *,
        dry_run: bool = False,
        source_replay: SourceReplay | None = None,
    ) -> EtlResult:
        """Orchestrate *pipeline* and return an EtlResult.

        If *dry_run* is True, only download + validate stages run
        (no stage/swap/DB writes).
        """
        log = logging.getLogger(f"etl.{pipeline.dataset_name}")
        # D-21/D-22: the scope is read exactly once, here. A concurrent scope
        # change during this import affects neither filtering nor gates.
        scope = self._load_scope(pipeline)
        stages: list[EtlStageResult] = []
        total_rows = 0
        publication_started = False
        prev_row_count: int | None = None  # populated by _check_row_regression
        t0 = time.perf_counter()
        dsn = "" if dry_run else pipeline.get_sync_dsn()

        # Pre-flight: ETL temp dir disk space check (downloads)
        try:
            check_disk_space(settings.etl_temp_dir)
        except RuntimeError as exc:
            log.error("Pre-flight failed: %s", exc)  # noqa: TRY400
            return EtlResult(
                dataset_name=pipeline.dataset_name,
                stages=stages,
                total_rows_loaded=0,
                total_duration_seconds=time.perf_counter() - t0,
                success=False,
                error=str(exc),
            )

        # Replay admission precedes even the PostgreSQL preflight: it can clean
        # old tables. An invalid generation must not touch any existing candidate.
        files: list[Path] = []
        if source_replay is not None:
            try:
                stages.append(
                    self._run_stage(
                        "restore_sources",
                        lambda: files.extend(
                            pipeline.restore_source_inputs(
                                source_replay, get_download_dir(pipeline.dataset_name)
                            )
                        ),
                        log,
                    )
                )
            except _StageError as exc:
                result = EtlResult(
                    dataset_name=pipeline.dataset_name,
                    stages=stages,
                    total_rows_loaded=0,
                    total_duration_seconds=time.perf_counter() - t0,
                    success=False,
                    error=str(exc),
                )
                result.remember_failure(exc)
                return result

        # Validation-only runs never inspect or alter publication/resume state.
        loaded_files_skip: set[str] = set()
        if not dry_run:
            # Pre-flight: PostgreSQL data dir disk space check (GATE-03)
            # Computes required = prod_size * 2, checks PG data_directory mount,
            # attempts inline _old table cleanup before aborting.
            preflight_disk_err = self._check_preflight_disk(pipeline, dsn, log)
            if preflight_disk_err:
                log.error("GATE-03 preflight disk failed: %s", preflight_disk_err)
                return EtlResult(
                    dataset_name=pipeline.dataset_name,
                    stages=stages,
                    total_rows_loaded=0,
                    total_duration_seconds=time.perf_counter() - t0,
                    success=False,
                    error=preflight_disk_err,
                )

            # Persist run start in etl_runs (best-effort)
            run_id = self._insert_etl_run(dsn, pipeline.dataset_name, log, scope)

            # Notify hooks (disk check, logging, etc.)
            self._call_hooks("on_run_start", pipeline.dataset_name, log=log)

            # -- Resume / fresh-start decision (RESUME-02) -------------------------
            # A crashed run leaves both the checkpoint file and the staging table.
            # Resume is only valid when BOTH exist; any other combination means we
            # start fresh and clean up the leftover (whichever of the two exists).
            checkpoint = CheckpointManager(
                dataset_name=pipeline.dataset_name,
                staging_table=pipeline.staging_table_name,
            )
            staging_exists = self._staging_table_exists(
                dsn, pipeline.staging_table_name
            )
            if pipeline.flat_staging_table_name:
                staging_exists = staging_exists and self._staging_table_exists(
                    dsn, pipeline.flat_staging_table_name
                )
            checkpoint_data = checkpoint.load()
            if requires_fresh_start(dsn, pipeline.dataset_name):
                checkpoint.clear()
                pipeline.drop_staging_table(dsn)
                staging_exists = False
                checkpoint_data = None
                log.warning("Incomplete publication: restarting from fresh staging")
            # DPE restores a source generation while preparing its specialized key.
            # Remove legacy/orphan evidence before that preparation can consult it.
            if checkpoint_data is None or not staging_exists:
                if checkpoint.exists():
                    log.warning(
                        "Missing or incompatible resume evidence; rebuilding staging"
                    )
                    checkpoint.clear()
                checkpoint_data = None
            checkpoint_scope_key = pipeline.checkpoint_scope_key
            checkpoint_identity = pipeline.checkpoint_identity(checkpoint_scope_key)
            if checkpoint_data is not None and (
                checkpoint_data["scope_key"] != checkpoint_scope_key
                or checkpoint_data["identity_key"] != checkpoint_identity
                or checkpoint_data["pending"] is not None
            ):
                log.warning(
                    "Resume identity changed or write outcome uncertain; "
                    "rebuilding staging"
                )
                checkpoint.clear()
                checkpoint_data = None
            resume = staging_exists and checkpoint_data is not None
            if resume:
                loaded_files_skip = set(checkpoint_data["loaded_files"])
            elif staging_exists:
                log.warning("Discarding staging without compatible checkpoint")
        loaded_files_list: list[str] = sorted(loaded_files_skip)
        source_files: list[dict] = []

        try:
            if not dry_run:
                if resume:
                    stock = self._run_stage(
                        "resume_stock",
                        lambda: self._count_staging_rows(dsn, pipeline),
                        log,
                    )
                    stages.append(stock)
                    total_rows = stock.rows_affected or 0
                    if total_rows != checkpoint_data["staging_rows"]:
                        log.warning(
                            "Staging stock differs from checkpoint; rebuilding staging"
                        )
                        checkpoint.clear()
                        loaded_files_skip.clear()
                        loaded_files_list.clear()
                        resume = False
                        total_rows = 0
                if not resume:
                    # Stage 0 -- create staging table (skipped when resuming)
                    stages.append(
                        self._run_stage(
                            "create_staging",
                            lambda: pipeline.create_staging_table(dsn),
                            log,
                        )
                    )
                else:
                    log.info(
                        "RESUME: reusing existing staging table %s",
                        pipeline.staging_table_name,
                    )

            # Stage 1 -- download, or restore the verified archived sources
            if source_replay is None:
                stages.append(
                    self._run_stage(
                        "download",
                        lambda: files.extend(pipeline.download()),
                        log,
                    )
                )
            if not dry_run:
                # Positional: test doubles replace this method with *args.
                self._record_source_files(
                    dsn,
                    run_id,
                    files,
                    get_download_dir(pipeline.dataset_name),
                    log,
                    _archive_objects(pipeline),
                )
                # Plan 02-14 (D-30 amended): the sources are archived before
                # any transformation, hence before the cache purge; a run
                # that cannot archive them fails and publishes nothing.
                if settings.source_archive_dir:
                    stages.append(
                        self._run_stage(
                            "archive_sources",
                            lambda: self._archive_sources(
                                dsn, run_id, pipeline, files, source_replay, log
                            ),
                            log,
                        )
                    )

            if not dry_run:

                def fingerprint_sources() -> None:
                    nonlocal source_files
                    source_files = self._fingerprint_sources(pipeline, files)

                stages.append(
                    self._run_stage("resume_sources", fingerprint_sources, log)
                )
                if resume and source_files != checkpoint_data["source_files"]:
                    log.warning("Source inventory or bytes changed; rebuilding staging")
                    checkpoint.clear()
                    loaded_files_skip.clear()
                    loaded_files_list.clear()
                    resume = False
                    total_rows = 0
                    stages.append(
                        self._run_stage(
                            "create_staging",
                            lambda: pipeline.create_staging_table(dsn),
                            log,
                        )
                    )

                # A resume whose previous run committed the scope filter.
                scope_filtered = bool(
                    resume and checkpoint_data and checkpoint_data.get("scope_filtered")
                )

                def save_checkpoint(pending: dict | None = None) -> None:
                    checkpoint.save(
                        loaded_files_list,
                        scope_key=checkpoint_scope_key,
                        identity_key=checkpoint_identity,
                        source_files=source_files,
                        staging_rows=total_rows,
                        pending=pending,
                        scope_filtered=scope_filtered,
                    )

            # Stages 2-4 -- per-file validate / transform / stage
            failed_files: list[str] = []

            for i, file_path in enumerate(files, 1):
                # Skip files that were already staged in a previous (crashed) run.
                # RESUME-02: downloaded files are retained on failure precisely so
                # the resume can re-validate + re-stage only the missing ones.
                file_id = pipeline.checkpoint_file_id(file_path)
                if file_id in loaded_files_skip:
                    log.info(
                        "RESUME: skipping already-loaded file %d/%d: %s",
                        i,
                        len(files),
                        file_path.name,
                    )
                    continue

                log.info(
                    "Processing file %d/%d: %s",
                    i,
                    len(files),
                    file_path.name,
                )

                stage_started = False
                try:
                    # Always validate
                    stages.append(
                        self._run_stage(
                            f"validate:{file_path.name}",
                            lambda fp=file_path: pipeline.validate_schema(fp),
                            log,
                        )
                    )

                    if dry_run:
                        log.info(
                            "[DRY RUN] Skipping transform/stage for %s",
                            file_path.name,
                        )
                        continue

                    def before_stage(identifier: str = file_id) -> None:
                        nonlocal stage_started
                        # Intent persistence errors must abort before any load.
                        stage_started = True
                        save_checkpoint({"operation": "stage", "file_id": identifier})

                    self._process_file(
                        pipeline, file_path, stages, log, before_stage=before_stage
                    )
                    stock = self._run_stage(
                        "staging_stock",
                        lambda: self._count_staging_rows(dsn, pipeline),
                        log,
                    )
                    stages.append(stock)
                    total_rows = stock.rows_affected or 0
                    loaded_files_list.append(file_id)
                    stages.append(self._run_stage("checkpoint", save_checkpoint, log))

                except _StageError as exc:
                    # A validation-only run cannot hide a rejected schema behind
                    # a disabled/partial import threshold (expected_file_count=0).
                    # A FatalInputError (hosted departement not delivered,
                    # D-122) ends the import instead of being skipped.
                    if (
                        dry_run
                        or stage_started
                        or transient_cause(exc) is not None
                        or isinstance(exc.__cause__, FatalInputError)
                    ):
                        raise
                    failed_files.append(file_path.name)
                    log.warning(
                        "File %s failed (skipped): %s (%d/%d)",
                        file_path.name,
                        exc,
                        len(failed_files),
                        len(files),
                    )
                    # Record failed stage but continue with next file
                    continue

            if not dry_run:
                # Pipeline counts reach the etl_runs steps (plan 02-16).
                stages.extend(
                    EtlStageResult(
                        stage_name=f"count:{name}",
                        duration_seconds=0.0,
                        rows_affected=value,
                        success=True,
                    )
                    for name, value in pipeline.stage_counters().items()
                )

            if failed_files:
                log.warning(
                    "File processing: %d/%d succeeded, %d failed: %s",
                    len(files) - len(failed_files),
                    len(files),
                    len(failed_files),
                    failed_files[:10],
                )

            # Success threshold check: abort swap if too many files failed
            expected = pipeline.expected_file_count
            if expected > 0:
                successful = len(files) - len(failed_files)
                ratio = successful / expected if expected > 0 else 1.0
                threshold = pipeline.min_success_ratio

                log.info(
                    "Success ratio: %d/%d files (%.1f%%), threshold: %.0f%%",
                    successful,
                    expected,
                    ratio * 100,
                    threshold * 100,
                )

                if ratio < threshold:
                    msg = (
                        f"Success ratio {ratio:.1%} ({successful}/{expected}) "
                        f"below threshold {threshold:.0%} -- swap aborted"
                    )
                    log.error(msg)
                    # Don't swap -- treat as pipeline failure
                    if not dry_run:
                        self._safe_drop(pipeline, dsn, log)
                    duration = time.perf_counter() - t0
                    result = EtlResult(
                        dataset_name=pipeline.dataset_name,
                        stages=stages,
                        total_rows_loaded=total_rows,
                        total_duration_seconds=duration,
                        success=False,
                        error=msg,
                    )
                    if not dry_run:
                        self._update_etl_run(dsn, run_id, result, log)
                        self._call_hooks(
                            "on_run_complete", pipeline.dataset_name, result, log=log
                        )
                    return result

            if dry_run:
                log.info("[DRY RUN] Skipping swap — validation passed")
            else:
                # D-22: a national file keeps only the hosted departements,
                # before every gate, so gates compare at equal scope.
                if pipeline.scope_filter_sql is not None:
                    stages.append(
                        self._run_stage(
                            "filter_intent",
                            lambda: save_checkpoint(
                                {"operation": "filter", "file_id": None}
                            ),
                            log,
                        )
                    )
                    already_filtered = scope_filtered
                    filtered = self._run_stage(
                        "scope_filter",
                        lambda: self._apply_scope_filter(
                            dsn, pipeline, scope, log, already_filtered=already_filtered
                        ),
                        log,
                    )
                    stages.append(filtered)
                    scope_filtered = filtered.success
                    stock = self._run_stage(
                        "candidate_stock",
                        lambda: self._count_staging_rows(dsn, pipeline),
                        log,
                    )
                    stages.append(stock)
                    total_rows = stock.rows_affected or 0
                    stages.append(self._run_stage("checkpoint", save_checkpoint, log))
                else:
                    stock = self._run_stage(
                        "candidate_stock",
                        lambda: self._count_staging_rows(dsn, pipeline),
                        log,
                    )
                    stages.append(stock)
                    total_rows = stock.rows_affected or 0

                # Pre-swap safety: row count regression check
                regression_err, prev_row_count = self._check_row_regression(
                    dsn, pipeline, total_rows, log
                )
                if regression_err:
                    self._safe_drop(pipeline, dsn, log)
                    duration = time.perf_counter() - t0
                    result = EtlResult(
                        dataset_name=pipeline.dataset_name,
                        stages=stages,
                        total_rows_loaded=total_rows,
                        total_duration_seconds=duration,
                        success=False,
                        error=regression_err,
                        previous_row_count=prev_row_count,
                    )
                    self._update_etl_run(dsn, run_id, result, log)
                    self._call_hooks(
                        "on_run_complete",
                        pipeline.dataset_name,
                        result,
                        log=log,
                    )
                    return result

                # Pre-swap safety: minimum row count gate (GATE-01)
                # Skipped for flat-staging pipelines (DPE, BAN, Monuments)
                # which set min_staging_rows=0 and check inside swap() instead.
                min_row_err = self._check_min_row_count(pipeline, total_rows, log)
                if min_row_err:
                    self._safe_drop(pipeline, dsn, log)
                    duration = time.perf_counter() - t0
                    result = EtlResult(
                        dataset_name=pipeline.dataset_name,
                        stages=stages,
                        total_rows_loaded=total_rows,
                        total_duration_seconds=duration,
                        success=False,
                        error=min_row_err,
                    )
                    self._update_etl_run(dsn, run_id, result, log)
                    self._call_hooks(
                        "on_run_complete",
                        pipeline.dataset_name,
                        result,
                        log=log,
                    )
                    return result

                # Pre-swap safety: department coverage check
                coverage_err = self._check_department_coverage(dsn, pipeline, log)
                if coverage_err:
                    self._safe_drop(pipeline, dsn, log)
                    duration = time.perf_counter() - t0
                    result = EtlResult(
                        dataset_name=pipeline.dataset_name,
                        stages=stages,
                        total_rows_loaded=total_rows,
                        total_duration_seconds=duration,
                        success=False,
                        error=coverage_err,
                    )
                    self._update_etl_run(dsn, run_id, result, log)
                    self._call_hooks(
                        "on_run_complete",
                        pipeline.dataset_name,
                        result,
                        log=log,
                    )
                    return result

                # Pre-swap: optional data quality validation (warnings only)
                data_warnings = []
                stages.append(
                    self._run_stage(
                        "validate_data",
                        lambda: data_warnings.extend(pipeline.validate_data(dsn)),
                        log,
                    )
                )
                for w in data_warnings:
                    log.warning("Data validation: %s", w)

                # Pre-swap: schema drift check (GATE-02)
                # Skipped for flat-staging pipelines (DPE, BAN, Monuments) that
                # set skip_runner_schema_drift_check=True and call the check
                # inside their swap() after the INSERT SELECT instead.
                if not pipeline.skip_runner_schema_drift_check:
                    drift_err = self._check_schema_drift(pipeline, dsn, log)
                    if drift_err:
                        # abort mode: block swap and return failure
                        self._safe_drop(pipeline, dsn, log)
                        duration = time.perf_counter() - t0
                        result = EtlResult(
                            dataset_name=pipeline.dataset_name,
                            stages=stages,
                            total_rows_loaded=total_rows,
                            total_duration_seconds=duration,
                            success=False,
                            error=drift_err,
                        )
                        self._update_etl_run(dsn, run_id, result, log)
                        self._call_hooks(
                            "on_run_complete",
                            pipeline.dataset_name,
                            result,
                            log=log,
                        )
                        return result

                publication_started = True
                completed = []
                stages.append(
                    self._run_stage(
                        "publication",
                        lambda: completed.append(
                            self._publish(
                                pipeline,
                                dsn,
                                stages,
                                total_rows,
                                prev_row_count,
                                t0,
                                checkpoint,
                                run_id,
                                log,
                            )
                        ),
                        log,
                    )
                )
                return completed[0]

        except _StageError as exc:
            if not dry_run and not publication_started:
                self._safe_drop(pipeline, dsn, log)
            duration = time.perf_counter() - t0
            result = EtlResult(
                dataset_name=pipeline.dataset_name,
                stages=stages,
                total_rows_loaded=total_rows,
                total_duration_seconds=duration,
                success=False,
                error=str(exc),
            )
            result.remember_failure(exc)
            if not dry_run:
                self._update_etl_run(dsn, run_id, result, log)
                self._call_hooks(
                    "on_run_failure",
                    pipeline.dataset_name,
                    result.error or "Unknown",
                    log=log,
                )
            return result

        total_duration = time.perf_counter() - t0
        log.info(
            "Pipeline %s data stages completed: %d rows in %.1fs",
            pipeline.dataset_name,
            total_rows,
            total_duration,
        )

        # Dry runs finish here without journal, locks, hooks or metadata writes.
        return EtlResult(
            dataset_name=pipeline.dataset_name,
            stages=stages,
            total_rows_loaded=total_rows,
            total_duration_seconds=total_duration,
            success=True,
            previous_row_count=prev_row_count,
        )

    def _publish(
        self,
        pipeline,
        dsn,
        stages,
        total_rows,
        previous_count,
        started,
        checkpoint,
        run_id,
        log,
    ):
        self._require_publication_checks()
        result = EtlResult(
            dataset_name=pipeline.dataset_name,
            stages=stages,
            total_rows_loaded=total_rows,
            total_duration_seconds=time.perf_counter() - started,
            success=True,
            previous_row_count=previous_count,
        )
        with Publication(dsn, pipeline) as publication:

            def locked_swap():
                with advisory_lock(dsn, pipeline.production_table_name):
                    pipeline.swap()

            try:
                stages.append(self._run_stage("swap", locked_swap, log))
            finally:
                # Also when the swap refuses the candidate: the counts that
                # refused it (BDNB ffo_divergents, D13) reach the receipt.
                stages.extend(
                    EtlStageResult(
                        stage_name=f"count:{name}",
                        duration_seconds=0.0,
                        rows_affected=value,
                        success=True,
                    )
                    for name, value in pipeline.swap_counters().items()
                )
            # EtlResult copied ``stages`` when it was built (pydantic validates
            # a list into a new one): the swap and its counts reach the
            # receipt (etl_runs) and the caller only when copied in again.
            result.stages = list(stages)
            result._publication_row_count = publication.observe_stock()
            failures = []
            try:
                with publication.required_work() as work:
                    result._publication_work = work
                    failures = self._call_hooks(
                        "on_run_complete",
                        pipeline.dataset_name,
                        result,
                        log=log,
                        required_only=True,
                    )
                    if failures:
                        raise RuntimeError(
                            "Required validation/views failed after publication; "
                            "generation unavailable"
                        ) from failures[0]
                    publication.publish(
                        lambda conn: self._update_dataset_metadata(
                            dsn,
                            pipeline.dataset_name,
                            publication.row_count,
                            time.perf_counter() - started,
                            log,
                            source_period_label=pipeline.source_period_label,
                            replace_source_period_label=(
                                pipeline.supports_millesime_pin
                            ),
                            source_updated_at=pipeline.source_updated_at,
                            effective_departments=(
                                pipeline.expected_department_coverage
                                if pipeline.supports_department_scope
                                else None
                            ),
                            connection=conn,
                        )
                    )
            except Exception:
                from app.etl.hooks import DependentMatviewHook, RequiredMatviewFailure

                # required_work has already rolled back; the owner still holds
                # exclusion. Never let an observability error replace the cause.
                for failure in failures:
                    if isinstance(failure, RequiredMatviewFailure):
                        try:
                            publication.record_required_failure(
                                lambda conn, failure=failure: (
                                    DependentMatviewHook._write_failure(
                                        conn, failure.matview_name, failure.original
                                    )
                                )
                            )
                        except Exception:
                            log.exception("Required view failure could not be recorded")
                raise
            finally:
                result._publication_work = None
        # The publication is committed and its lock released: the previous
        # generation is no longer needed (rollback = replay from the archive).
        self._drop_published_old_tables(pipeline, dsn, log)
        # Optional notifications see only committed publication success.
        result.total_duration_seconds = time.perf_counter() - started
        self._update_etl_run(dsn, run_id, result, log)
        try:
            checkpoint.clear()
            cleanup_download_dir(pipeline.dataset_name)
        except Exception:  # noqa: BLE001
            log.warning("Published data retained; local cleanup failed")
        self._call_hooks(
            "on_run_complete",
            pipeline.dataset_name,
            result,
            log=log,
            skip_required=True,
        )
        return result

    def _require_publication_checks(self) -> None:
        from app.etl.hooks import DependentMatviewHook, ValidationHook

        for required_type in (ValidationHook, DependentMatviewHook):
            if not any(
                isinstance(hook, required_type) and hook.required_on_complete is True
                for hook in self._hooks
            ):
                raise RuntimeError("No required publication checks configured")
        validation_positions = [
            i
            for i, hook in enumerate(self._hooks)
            if isinstance(hook, ValidationHook) and hook.required_on_complete is True
        ]
        view_positions = [
            i
            for i, hook in enumerate(self._hooks)
            if isinstance(hook, DependentMatviewHook)
            and hook.required_on_complete is True
        ]
        if max(validation_positions) > min(view_positions):
            raise RuntimeError("Required validation must precede dependent DDL")

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _check_department_coverage(
        dsn: str,
        pipeline: BasePipeline,
        log: logging.Logger,
    ) -> str | None:
        """Staging holds exactly the configured departements the source covers.

        One rule for every pipeline with a departement dimension, departmental
        download or national file filtered to the scope (D-23): a missing or
        extra departement refuses the swap. The expected set comes from the
        instance scope bound by the runner, never from a fixed threshold.
        """
        if not (
            pipeline.has_department_partitions
            or pipeline.supports_department_scope
            or pipeline.scope_filter_sql is not None
        ):
            return None

        # Flat-staging pipelines (flat -> partitioned) fill the partitioned
        # table only inside swap(): check the flat table before swap.
        staging = pipeline.flat_staging_table_name or pipeline.staging_table_name
        try:
            with psycopg.connect(dsn) as conn:
                return PipelineRunner._scoped_coverage_error(
                    conn, pipeline, staging, log
                )
        except Exception:
            # An unreadable guard must not let a possibly partial table through.
            msg = "Department coverage check unreadable -- swap aborted"
            log.error(msg, exc_info=True)
            return msg

    def _process_file(
        self,
        pipeline: BasePipeline,
        file_path: Path,
        stages: list[EtlStageResult],
        log: logging.Logger,
        *,
        before_stage: Callable[[], None] | None = None,
    ) -> int:
        """Transform and stage a single file. Return row count."""
        # Transform
        transformed: Path | None = None

        def do_transform() -> None:
            nonlocal transformed
            transformed = pipeline.transform(file_path)

        stages.append(
            self._run_stage(
                f"transform:{file_path.name}",
                do_transform,
                log,
            )
        )
        # Intent follows transform: a rejected transform cannot have loaded rows.
        if before_stage is not None:
            stages.append(self._run_stage("stage_intent", before_stage, log))
        # Stage (bulk load)
        stage_result = self._run_stage(
            f"stage:{file_path.name}",
            lambda: pipeline.stage(transformed),  # type: ignore[arg-type]
            log,
        )
        stages.append(stage_result)
        return stage_result.rows_affected or 0

    @staticmethod
    def _count_staging_rows(dsn: str, pipeline: BasePipeline) -> int:
        """Read the real candidate stock; missing tables/query failures abort."""
        table = pipeline.flat_staging_table_name or pipeline.staging_table_name
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            raise RuntimeError("Cannot attest staging stock")
        return row[0]

    @staticmethod
    def _fingerprint_sources(pipeline: BasePipeline, files: list[Path]) -> list[dict]:
        """Fingerprint logical units, including directory-based source batches."""
        entries = []
        identifiers = set()
        for path in files:
            identifier = pipeline.checkpoint_file_id(path)
            if not is_logical_file_id(identifier) or identifier in identifiers:
                raise ValueError(f"Invalid or duplicate source identity: {identifier}")
            identifiers.add(identifier)
            children = expand_source_paths(
                [path], path.parent, _archive_objects(pipeline)
            )
            members = []
            size = 0
            for child in children:
                digest = hashlib.sha256()
                length = 0
                with child.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        length += len(chunk)
                size += length
                members.append(
                    {
                        "path": child.relative_to(path).as_posix()
                        if path.is_dir()
                        else "",
                        "bytes": length,
                        "sha256": digest.hexdigest(),
                    }
                )
            if path.is_dir():
                fingerprint = hashlib.sha256(
                    json.dumps(
                        sorted(members, key=lambda item: item["path"]),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            else:
                fingerprint = members[0]["sha256"]
            entries.append({"id": identifier, "bytes": size, "sha256": fingerprint})
        return sorted(entries, key=lambda item: item["id"])

    @staticmethod
    def _run_stage(
        name: str,
        fn: object,
        log: logging.Logger,
    ) -> EtlStageResult:
        """Execute *fn*, measure time, and return a stage result.

        Raises _StageError so the caller can abort early.
        """
        log.info("Stage [%s] starting", name)
        t0 = time.perf_counter()
        try:
            result = fn()  # type: ignore[operator]
            duration = time.perf_counter() - t0
            rows = result if isinstance(result, int) else None
            log.info("Stage [%s] done in %.2fs", name, duration)
            return EtlStageResult(
                stage_name=name,
                duration_seconds=duration,
                rows_affected=rows,
                success=True,
            )
        except Exception as exc:
            duration = time.perf_counter() - t0
            log.error("Stage [%s] failed after %.2fs: %s", name, duration, exc)  # noqa: TRY400
            raise _StageError(f"Stage '{name}' failed: {exc}") from exc

    @staticmethod
    def _update_dataset_metadata(
        dsn: str,
        dataset_name: str,
        row_count: int,
        duration: float,
        log: logging.Logger,
        source_period_label: str | None = None,
        source_updated_at: datetime | None = None,
        effective_departments: list[str] | None = None,
        connection: psycopg.Connection | None = None,
        replace_source_period_label: bool = False,
    ) -> None:
        """Update success metadata inside the publication transaction when supplied.

        ``last_updated_at`` records a DataCommune import (NOW()). The optional
        upstream facts (``source_period_label``, ``source_updated_at``,
        ``effective_departments``) are only written when the pipeline actually
        provides them; a None value keeps any previously stored authentic value
        via COALESCE and never copies ``last_updated_at`` (PROV-03/D-03).
        ``replace_source_period_label`` (pipelines that can target a vintage,
        plan 02-11): the label is always this run's, None included, so a run
        on the current vintage never inherits a previously pinned label.
        """
        try:
            with (
                nullcontext(connection)
                if connection is not None
                else psycopg.connect(dsn)
            ) as conn:
                conn.execute(
                    """
                    INSERT INTO dataset_metadata
                        (dataset_name, status, last_updated_at, row_count,
                         last_import_duration_seconds, source_period_label,
                         source_updated_at, effective_departments)
                    VALUES (%(name)s, 'disponible', NOW(), %(rows)s, %(dur)s,
                            %(period)s, %(supd)s, %(depts)s)
                    ON CONFLICT (dataset_name) DO UPDATE SET
                        status = 'disponible',
                        last_updated_at = NOW(),
                        row_count = %(rows)s,
                        last_import_duration_seconds = %(dur)s,
                        source_period_label = CASE WHEN %(replace_period)s
                            THEN %(period)s
                            ELSE COALESCE(
                                %(period)s, dataset_metadata.source_period_label)
                            END,
                        source_updated_at = COALESCE(
                            %(supd)s, dataset_metadata.source_updated_at),
                        effective_departments = COALESCE(
                            %(depts)s, dataset_metadata.effective_departments)
                    """,
                    {
                        "name": dataset_name,
                        "rows": row_count,
                        "dur": duration,
                        "period": source_period_label,
                        "replace_period": replace_source_period_label,
                        "supd": source_updated_at,
                        "depts": effective_departments,
                    },
                )
                if connection is None:
                    conn.commit()
            log.info("dataset_metadata updated for %s", dataset_name)
        except Exception:
            log.warning(
                "Failed to update dataset_metadata for %s",
                dataset_name,
                exc_info=True,
            )
            raise

    @staticmethod
    def _insert_etl_run(
        dsn: str,
        dataset_name: str,
        log: logging.Logger,
        scope: InstanceScope | None = None,
    ) -> int | None:
        """Insert a new etl_runs row with status='running'. Return run id.

        ``scope_snapshot`` records the scope read at run start (D-21).
        """
        try:
            with psycopg.connect(dsn) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO etl_runs (dataset_name, status, scope_snapshot)
                    VALUES (%(name)s, 'running', %(scope)s::jsonb)
                    RETURNING id
                    """,
                    {
                        "name": dataset_name,
                        "scope": (
                            json.dumps(scope.as_json()) if scope is not None else None
                        ),
                    },
                )
                row = cur.fetchone()
                conn.commit()
                run_id = row[0] if row else None
                log.info("ETL run started: id=%s", run_id)
        except Exception:  # noqa: BLE001
            log.warning(
                "Failed to insert etl_run for %s (table may not exist yet)",
                dataset_name,
                exc_info=True,
            )
            return None
        if run_id is not None:
            PipelineRunner._close_interrupted_runs(dsn, dataset_name, run_id, log)
        return run_id

    @staticmethod
    def _close_interrupted_runs(
        dsn: str, dataset_name: str, run_id: int, log: logging.Logger
    ) -> None:
        """Close as failed the rows of *dataset_name* a killed run left 'running'.

        The only caller runs under the dataset execution lock, so no other run of
        this dataset is live: a row still 'running' was left by a SIGKILL or a power
        cut and would stay so for ever (audit ETL-12; run 119 of batiments). Its end
        is unknown, so completed_at stays empty. Best-effort, apart from the insert:
        a failure here never costs the new run its own row.
        """
        try:
            with psycopg.connect(dsn) as conn:
                closed = conn.execute(
                    """
                    UPDATE etl_runs SET status = 'failed', error_message = %(err)s
                    WHERE dataset_name = %(name)s AND status = 'running'
                      AND id <> %(id)s
                    RETURNING id
                    """,
                    {
                        "name": dataset_name,
                        "id": run_id,
                        "err": f"interrupted: still running when run {run_id} started",
                    },
                ).fetchall()
                conn.commit()
            if closed:
                log.warning(
                    "Closed %d interrupted run(s) of %s: %s",
                    len(closed),
                    dataset_name,
                    [row[0] for row in closed],
                )
        except Exception:  # noqa: BLE001
            log.warning(
                "Failed to close interrupted runs of %s", dataset_name, exc_info=True
            )

    @classmethod
    def _archive_sources(
        cls,
        dsn: str,
        run_id: int | None,
        pipeline: BasePipeline,
        files: list[Path],
        source_replay: SourceReplay | None,
        log: logging.Logger,
    ) -> None:
        """Archive the run's source files under the run id of its journal row."""
        started_at = cls._read_run_started_at(dsn, run_id) if run_id else None
        if run_id is None or started_at is None:
            # No journal row, no name for the record: never an anonymous archive.
            raise SourceArchiveError("run_id_absent")
        sha = settings.deployment_sha
        archive_root, artifacts = pipeline.archive_source_inputs(files)
        record = archive_sources(
            Path(settings.source_archive_dir),
            pipeline.dataset_name,
            compose_run_id(run_id, started_at),
            artifacts,
            archive_root,
            origin="archive" if source_replay is not None else "telechargement",
            replayed_from=source_replay.run_id if source_replay is not None else None,
            deployment_sha=sha if sha and sha != "unknown" else None,
            min_free_bytes=settings.source_archive_min_free_bytes,
            # After the download (or the restore): what the run knows of the
            # period of these files, kept for a later replay (C30).
            source_period_label=pipeline.source_period_label,
        )
        log.info(
            "Sources archived: %s/runs/%s.json (%d files, %d stored)",
            record["dataset"],
            record["run_id"],
            len(record["files"]),
            sum(1 for f in record["files"] if f["stored"]),
        )

    @staticmethod
    def _read_run_started_at(dsn: str, run_id: int) -> datetime | None:
        """``started_at`` of the run's own etl_runs row (it composes the run id)."""
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT started_at FROM etl_runs WHERE id = %s", (run_id,)
            ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _record_source_files(
        dsn: str,
        run_id: int | None,
        files: list[Path],
        download_dir: Path,
        log: logging.Logger,
        linked_objects: Path | None = None,
    ) -> None:
        """Write name, bytes, SHA-256 (and CSV physical lines) of each downloaded file.

        A downloaded directory is journaled as the files under it, the same
        list the source archive records (plan 02-22). Best-effort like the
        rest of the etl_runs journal: a fingerprint failure is logged, never
        turned into an import failure.
        """
        if run_id is None:
            return
        try:
            entries = [
                _source_file_entry(path)
                for path in expand_source_paths(files, download_dir, linked_objects)
            ]
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    "UPDATE etl_runs SET source_files = %(files)s::jsonb "
                    "WHERE id = %(id)s",
                    {"id": run_id, "files": json.dumps(entries)},
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            log.warning(
                "Failed to record source files for etl_run %s", run_id, exc_info=True
            )

    @staticmethod
    def _apply_scope_filter(
        dsn: str,
        pipeline: BasePipeline,
        scope: InstanceScope,
        log: logging.Logger,
        *,
        already_filtered: bool = False,
    ) -> int:
        """Delete staged rows outside the hosted departements; return the count.

        Table names and expressions come from ``pipeline.scope_filter_sql``
        (code constants); departement codes travel as a bound parameter.
        A restricted scope also removes rows without departement. The full
        scope keeps them (documented rule) but still removes a departement
        outside the hostable list: a collectivity (975, D-123) or a malformed
        source value, which the coverage guard would refuse (first national
        import, 2026-10-29).
        """
        limited = not scope.covers_all_departements
        excluded = 0
        tables = dict(pipeline.scope_filter_sql or {})
        # The table whose departement floors the coverage check reads.
        staging = pipeline.flat_staging_table_name or pipeline.staging_table_name
        primary = staging if staging in tables else next(iter(tables), None)
        with psycopg.connect(dsn) as conn:
            for table, expression in tables.items():
                exists = conn.execute(
                    "SELECT to_regclass(%s) IS NOT NULL", (table,)
                ).fetchone()
                if not exists or not exists[0]:
                    continue
                expr = sql.SQL(expression)
                # Same predicate as the coverage guard on each scope: NULL
                # matches neither "= ANY" nor "NOT (= ANY)".
                outside = (
                    sql.SQL("{expr} IS NULL OR NOT ({expr} = ANY(%s))")
                    if limited
                    else sql.SQL("NOT ({expr} = ANY(%s))")
                ).format(expr=expr)
                # One pass: rows without any departement (no commune code,
                # no polygon) are counted apart from rows outside the scope.
                removed, unattributed = conn.execute(
                    sql.SQL(
                        "WITH removed AS (DELETE FROM {table} WHERE {outside} "
                        "RETURNING {expr} AS dep) "
                        "SELECT count(*), count(*) FILTER "
                        "(WHERE dep IS NULL OR dep = '') FROM removed"
                    ).format(table=sql.Identifier(table), outside=outside, expr=expr),
                    (list(scope.departements),),
                ).fetchone()
                excluded += removed
                log.info(
                    "Scope filter for %s, %s: %d rows removed, of which %d "
                    "without departement",
                    pipeline.dataset_name,
                    table,
                    removed,
                    unattributed,
                )
                if table == primary:
                    # What the national file holds per hosted departement
                    # (261020-fpm): journaled, and read by the floors.
                    kept = dict(
                        conn.execute(
                            sql.SQL(
                                "SELECT {expr}, count(*) FROM {table} GROUP BY 1"
                            ).format(table=sql.Identifier(table), expr=expr)
                        ).fetchall()
                    )
                    # Full scope only: rows without departement stay, and are
                    # not a hosted departement.
                    unattributed_kept = kept.pop(None, 0)
                    pipeline.record_scope_file_rows(kept)
                    log.info(
                        "Scope filter for %s: rows kept per hosted departement %s",
                        pipeline.dataset_name,
                        dict(sorted(kept.items())),
                    )
                    if unattributed_kept:
                        log.info(
                            "Scope filter for %s: %d rows without departement "
                            "kept (full scope)",
                            pipeline.dataset_name,
                            unattributed_kept,
                        )
                    # A departement without reference is now a presence check:
                    # the truncation of the national file is caught on the
                    # whole file, before the filter (04.1-REVIEW WR-01).
                    whole = removed + sum(kept.values()) + unattributed_kept
                    floor = pipeline.national_file_min_rows
                    if already_filtered:
                        log.info(
                            "Scope filter for %s: resumed on a filtered table, "
                            "whole-file check passed before the interruption",
                            pipeline.dataset_name,
                        )
                    elif floor and whole < floor:
                        msg = (
                            f"National file volume: {table} held {whole:,} rows "
                            f"before the scope filter (minimum {floor:,}) "
                            "-- swap aborted"
                        )
                        log.error(msg)
                        raise RuntimeError(msg)
            conn.commit()
        log.info(
            "Scope filter for %s: %d rows outside %s removed",
            pipeline.dataset_name,
            excluded,
            list(scope.departements),
        )
        return excluded

    @staticmethod
    def _update_etl_run(
        dsn: str,
        run_id: int | None,
        result: EtlResult,
        log: logging.Logger,
    ) -> None:
        """Update an etl_runs row with final status (best-effort)."""
        if run_id is None:
            return
        try:
            stages_json = json.dumps([s.model_dump() for s in result.stages])
            status = "success" if result.success else "failed"
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    """
                    UPDATE etl_runs SET
                        completed_at = NOW(),
                        status = %(status)s,
                        total_rows_loaded = %(rows)s,
                        total_duration_seconds = %(dur)s,
                        error_message = %(err)s,
                        stages = %(stages)s::jsonb
                    WHERE id = %(id)s
                    """,
                    {
                        "id": run_id,
                        "status": status,
                        "rows": result.total_rows_loaded,
                        "dur": result.total_duration_seconds,
                        "err": result.error,
                        "stages": stages_json,
                    },
                )
                conn.commit()
            log.info("ETL run %s updated: %s", run_id, status)
        except Exception:  # noqa: BLE001
            log.warning(
                "Failed to update etl_run %s",
                run_id,
                exc_info=True,
            )

    @staticmethod
    def _scoped_coverage_error(
        conn: psycopg.Connection,
        pipeline: BasePipeline,
        staging: str,
        log: logging.Logger,
    ) -> str | None:
        """Every expected departement present in every filtered table (D-23).

        Tables and departement expressions are those of the scope filter
        (code constants); without a filter, those of
        ``coverage_departement_sql``, else the staging table on
        code_departement. On every scope a missing expected departement
        refuses the swap; known absences are declared by the pipeline
        (``departements_outside_source``). On a restricted scope, rows without
        departement are extra (the filter removed them) and, in the primary
        table, each departement must also reach its own floor: one truncated
        departement is refused even when another one fills the total. On the
        full scope the filter removes only departements outside the scope, so
        rows without departement stay.
        """
        tables = (
            dict(pipeline.scope_filter_sql or {})
            or dict(pipeline.coverage_departement_sql or {})
            or {staging: "code_departement"}
        )
        primary = staging if staging in tables else next(iter(tables))
        allowed = list(pipeline.department_scope)
        expected = sorted(set(pipeline.expected_department_coverage))
        limited = not pipeline.scope.covers_all_departements
        for table, dept in tables.items():
            rows = conn.execute(
                f"SELECT DISTINCT {dept} FROM {table} "
                f"WHERE {f'{dept} IS NULL OR ' if limited else ''}"
                f"NOT ({dept} = ANY(%s))",
                (allowed,),
            ).fetchall()
            unexpected = {row[0] for row in rows}
            if unexpected:
                msg = (
                    "Department coverage: staging contains departments "
                    f"outside scoped ETL {sorted(unexpected, key=str)} "
                    f"in {table} -- swap aborted"
                )
                log.error(msg)
                return msg
            table_expected = pipeline.expected_coverage_for_table(table)
            if not table_expected:
                continue
            counts = dict(
                conn.execute(
                    f"SELECT {dept}, count(*) FROM {table} "
                    f"WHERE {dept} = ANY(%s) GROUP BY 1",
                    (table_expected,),
                ).fetchall()
            )
            missing = [code for code in table_expected if code not in counts]
            if missing:
                msg = (
                    "Department coverage: missing scoped departments "
                    f"{missing} in {table} -- swap aborted"
                )
                log.error(msg)
                return msg
            if table != primary or not limited:
                continue
            for code in table_expected:
                floor = pipeline.departement_min_rows(code)
                if counts[code] < floor:
                    msg = (
                        f"Department volume: {code} has {counts[code]:,} rows in "
                        f"{table} (minimum {floor:,}) -- swap aborted"
                    )
                    log.error(msg)
                    return msg
        log.info("Department coverage check passed for scoped ETL: %s", expected or "-")
        return None

    @staticmethod
    def _check_row_regression(
        dsn: str,
        pipeline: BasePipeline,
        new_row_count: int,
        log: logging.Logger,
    ) -> tuple[str | None, int | None]:
        """Compare staging row count vs dataset_metadata.

        Returns:
            (error_message, previous_row_count) — error is None on success,
            previous_row_count is None when no prior count exists.
        """
        max_drop = settings.etl_max_row_drop_pct
        if pipeline.is_department_scope_limited:
            try:
                with psycopg.connect(dsn) as conn:
                    row = conn.execute(
                        f"SELECT count(*) FROM {pipeline.production_table_name} "
                        f"WHERE {pipeline.production_departement_sql} = ANY(%s)",
                        (pipeline.department_scope,),
                    ).fetchone()
            except Exception:
                # Fail closed: a scoped import must not publish without its
                # equal-scope comparison (D-23).
                msg = "Scoped row regression check unreadable -- swap aborted"
                log.error(msg, exc_info=True)
                return msg, None

            prev_count = row[0] if row else 0
            if prev_count == 0:
                log.info(
                    "Scoped row regression check: no previous scoped rows for %s "
                    "-- skipping",
                    pipeline.dataset_name,
                )
                return None, None

            if new_row_count == 0:
                msg = (
                    "Row count regression: scoped staging has 0 rows "
                    f"(production scope has {prev_count}) -- swap aborted"
                )
                log.error(msg)
                return msg, prev_count

            drop_pct = (prev_count - new_row_count) / prev_count
            if drop_pct > max_drop:
                msg = (
                    f"Row count regression: scoped {new_row_count:,} vs "
                    f"{prev_count:,} "
                    f"(drop {drop_pct:.1%} > threshold {max_drop:.0%}) "
                    "-- swap aborted"
                )
                log.error(msg)
                return msg, prev_count

            log.info(
                "Scoped row regression check passed: %s -> %s (delta %+.1f%%)",
                f"{prev_count:,}",
                f"{new_row_count:,}",
                -(drop_pct * 100),
            )
            return None, prev_count

        try:
            with psycopg.connect(dsn) as conn:
                row = conn.execute(
                    "SELECT row_count FROM dataset_metadata WHERE dataset_name = %s",
                    (pipeline.dataset_name,),
                ).fetchone()
        except Exception:  # noqa: BLE001
            log.warning(
                "Row regression check: could not read previous row_count -- skipping",
                exc_info=True,
            )
            return None, None

        if row is None or row[0] is None or row[0] == 0:
            log.info(
                "Row regression check: no previous count for %s -- skipping",
                pipeline.dataset_name,
            )
            return None, None

        prev_count = row[0]
        if new_row_count == 0:
            msg = (
                f"Row count regression: staging has 0 rows "
                f"(production has {prev_count}) -- swap aborted"
            )
            log.error(msg)
            return msg, prev_count

        drop_pct = (prev_count - new_row_count) / prev_count
        if drop_pct > max_drop:
            msg = (
                f"Row count regression: {new_row_count:,} vs {prev_count:,} "
                f"(drop {drop_pct:.1%} > threshold {max_drop:.0%}) -- swap aborted"
            )
            log.error(msg)
            return msg, prev_count

        log.info(
            "Row regression check passed: %s -> %s (delta %+.1f%%)",
            f"{prev_count:,}",
            f"{new_row_count:,}",
            -(drop_pct * 100),
        )
        return None, prev_count

    @staticmethod
    def _safe_drop(
        pipeline: BasePipeline,
        dsn: str,
        log: logging.Logger,
    ) -> None:
        """Best-effort staging-table cleanup after failure."""
        try:
            pipeline.drop_staging_table(dsn)
        except Exception:  # noqa: BLE001
            log.warning(
                "Cleanup failed for %s staging table",
                pipeline.dataset_name,
                exc_info=True,
            )

    @staticmethod
    def _staging_table_exists(dsn: str, staging_table: str) -> bool:
        """Return True if *staging_table* exists in the public schema."""
        try:
            with psycopg.connect(dsn) as conn:
                row = conn.execute(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM pg_tables"
                    "  WHERE schemaname = 'public' AND tablename = %s"
                    ")",
                    (staging_table,),
                ).fetchone()
            return bool(row and row[0])
        except Exception:  # noqa: BLE001
            return False

    # -- quality gate static methods ------------------------------------------

    @staticmethod
    def _check_min_row_count(
        pipeline: BasePipeline,
        total_rows: int,
        log: logging.Logger,
    ) -> str | None:
        """GATE-01: enforce a minimum row count before swap is allowed.

        Returns an error message string if the gate fails, None if it passes.
        A floor of 0 disables the check (default on BasePipeline).
        Flat-staging pipelines (DPE, BAN, Monuments) set min_staging_rows=0
        and instead call the check inside swap() via min_staging_rows_final.
        """
        min_rows = pipeline.min_staging_rows
        if min_rows == 0:
            final_min = pipeline.min_staging_rows_final
            if final_min > 0:
                # GATE-B': not disabled — the absolute floor is enforced
                # inside swap() after the flat -> partitioned INSERT SELECT.
                log.info(
                    "GATE-01 row count check deferred for %s: floor of "
                    "%d rows enforced inside swap() via min_staging_rows_final",
                    pipeline.dataset_name,
                    final_min,
                )
            else:
                log.info(
                    "GATE-01 row count check disabled for %s "
                    "(min_staging_rows=0 explicit opt-out, no final floor)",
                    pipeline.dataset_name,
                )
            return None
        if total_rows < min_rows:
            msg = (
                f"GATE-01 row count failed: staging has {total_rows:,} rows "
                f"(minimum {min_rows:,} required) — swap aborted"
            )
            log.error(msg)
            return msg
        log.info(
            "GATE-01 row count passed for %s: %d >= %d",
            pipeline.dataset_name,
            total_rows,
            min_rows,
        )
        return None

    @staticmethod
    def _check_schema_drift(
        pipeline: BasePipeline,
        dsn: str,
        log: logging.Logger,
    ) -> str | None:
        """GATE-02: detect schema drift between staging and production.

        Compares column names and data types via information_schema.columns.
        Only checks pipeline.staging_table_name (the prod-shaped partitioned
        table), never flat_staging_table_name (intentionally different schema).

        Respects pipeline.schema_drift_mode:
          'abort' -> returns an error string (runner blocks swap)
          'warn'  -> logs WARNING + returns None (swap proceeds)

        For flat-staging pipelines (DPE, BAN, Monuments) this method must
        be called directly from inside swap() via _check_schema_drift_conn()
        rather than from the runner, because the partitioned staging table
        is only populated during swap().  Those pipelines set
        skip_runner_schema_drift_check=True so the runner skips this call.
        """
        staging = pipeline.staging_table_name
        production = pipeline.production_table_name

        col_query = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """
        try:
            with psycopg.connect(dsn) as conn:
                prod_rows = conn.execute(col_query, (production,)).fetchall()
                staging_rows = conn.execute(col_query, (staging,)).fetchall()
        except Exception:  # noqa: BLE001
            log.warning(
                "GATE-02 schema drift check failed to query columns — skipping",
                exc_info=True,
            )
            return None

        prod_map = {row[0]: row[1] for row in prod_rows}
        staging_map = {row[0]: row[1] for row in staging_rows}

        added = set(staging_map) - set(prod_map)
        removed = set(prod_map) - set(staging_map)
        type_changed = {
            name
            for name in set(prod_map) & set(staging_map)
            if prod_map[name] != staging_map[name]
        }

        if not added and not removed and not type_changed:
            log.info(
                "GATE-02 schema drift check passed for %s: no drift detected",
                pipeline.dataset_name,
            )
            return None

        parts = []
        if added:
            parts.append(f"added={sorted(added)}")
        if removed:
            parts.append(f"removed={sorted(removed)}")
        if type_changed:
            parts.append(f"type_changed={sorted(type_changed)}")
        drift_desc = "; ".join(parts)

        msg = (
            f"GATE-02 schema drift detected for {pipeline.dataset_name} "
            f"({pipeline.staging_table_name} vs {production}): {drift_desc}"
        )

        mode = pipeline.schema_drift_mode
        if mode == "abort":
            log.error(msg + " — swap aborted (schema_drift_mode=abort)")
            return msg
        else:
            # warn mode: log and continue
            log.warning(msg + " — swap proceeding (schema_drift_mode=warn)")
            return None

    @staticmethod
    def _check_schema_drift_conn(
        pipeline: BasePipeline,
        dsn: str,
        log: logging.Logger,
    ) -> None:
        """GATE-02 variant for flat-staging pipelines called inside swap().

        Same logic as _check_schema_drift but raises RuntimeError on abort
        mode so that swap() can catch it and abort Phase A.  In warn mode,
        logs only (swap proceeds).

        Call this from inside swap() right after the INSERT SELECT into the
        partitioned staging table and before calling swap_tables().
        """
        err = PipelineRunner._check_schema_drift(pipeline, dsn, log)
        if err:
            # _check_schema_drift already returns None in warn mode and
            # only returns a message in abort mode.
            raise RuntimeError(err)

    @staticmethod
    def _drop_old_tables(
        dsn: str,
        log: logging.Logger,
        names: tuple[str, ...] | list[str] = (),
        *,
        tracked: bool = False,
    ) -> list[str]:
        """Drop previous generations and repair the views in ONE transaction.

        Drops *names* and, with *tracked*, every table of ``etl_old_tables``,
        deleting their registry rows. Rule API-4, shared by every caller: a
        ``DROP ... CASCADE`` takes down any matview still bound to the renamed
        table, so the DROPs, the DELETEs and ``recreate_missing_matviews``
        commit together, and a failed repair rolls everything back and raises.
        Returns the names dropped (``IF EXISTS``); nothing to drop, no commit.
        """
        with maintenance_connection(dsn) as conn:
            targets = list(dict.fromkeys(names))
            if tracked:
                rows = conn.execute(
                    "SELECT id, table_name FROM etl_old_tables ORDER BY id"
                ).fetchall()
                targets.extend(row[1] for row in rows if row[1] not in targets)
            if not targets:
                return []
            refused = [n for n in targets if not _OLD_TABLE_NAME.fullmatch(n)]
            if refused:
                raise ValueError(f"Not a previous-generation table name: {refused}")
            for name in targets:
                conn.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
                conn.execute(
                    "DELETE FROM etl_old_tables WHERE table_name = %s", (name,)
                )
            try:
                recreate_missing_matviews(conn, log)
            except Exception:
                # Explicit and observable: undo the DROPs rather than
                # commit a schema without its matviews.
                conn.rollback()
                raise
            conn.commit()
        return targets

    @staticmethod
    def _drop_published_old_tables(
        pipeline: BasePipeline, dsn: str, log: logging.Logger
    ) -> None:
        """Drop the dataset's previous generation right after its publication.

        Never undoes the publication: on failure the ``_old`` table and its
        registry row stay, for ``cleanup_old_tables`` or ``purge-old-tables``.
        """
        names = pipeline.published_old_tables
        for attempt in range(1, _OLD_DROP_ATTEMPTS + 1):
            try:
                dropped = PipelineRunner._drop_old_tables(dsn, log, names)
            except psycopg.errors.LockNotAvailable:
                if attempt < _OLD_DROP_ATTEMPTS:
                    time.sleep(_OLD_DROP_RETRY_S)
                    continue
                log.warning(
                    "Previous generation kept (publication lock busy): %s", names
                )
                return
            except Exception:  # noqa: BLE001
                log.warning(
                    "Previous generation kept (drop rolled back): %s",
                    names,
                    exc_info=True,
                )
                return
            log.info("Previous generation dropped after publication: %s", dropped)
            return

    @staticmethod
    def _inline_cleanup_old_tables(dsn: str, log: logging.Logger) -> None:
        """Drop ALL tracked _old tables regardless of TTL.

        Used by GATE-03 preflight disk check to free space before aborting.
        Unlike the scheduled cleanup_old_tables() which respects TTL, this
        drops all registered _old tables to recover the maximum possible space.
        Do NOT call this from outside the preflight disk check.

        Same transaction rule as every drop of an ``_old`` table
        (``_drop_old_tables``, API-4). Raises on failure -- an ETL run must
        not start on a schema that could not be put back together.
        """
        try:
            dropped = PipelineRunner._drop_old_tables(dsn, log, tracked=True)
            if not dropped:
                log.info("GATE-03 inline cleanup: no _old tables to drop")
                return
            log.info(
                "GATE-03 inline cleanup: dropped %d _old table(s): %s",
                len(dropped),
                ", ".join(dropped),
            )
        except Exception:
            log.warning(
                "GATE-03 inline cleanup failed — rolled back, nothing dropped",
                exc_info=True,
            )
            raise

    @staticmethod
    def _gate03_free_bytes(
        pg_data_dir: str,
        log: logging.Logger,
    ) -> tuple[int, str] | None:
        """Return (free_bytes, measured_path) for the GATE-03 disk check.

        In containerized deployments the PostgreSQL ``data_directory`` is a
        path inside the db container that the api/scheduler container cannot
        see — ``shutil.disk_usage`` raises FileNotFoundError.  Deliberate
        policy (not an accidentally swallowed exception): fall back to
        measuring ``settings.etl_temp_dir``, which sits on the same
        underlying disk on single-disk hosts (the former prod container).  The gate
        then still bites instead of being silently skipped.

        Any other OS error skips the gate (returns None) with a WARNING.
        """
        try:
            return shutil.disk_usage(pg_data_dir).free, pg_data_dir
        except FileNotFoundError:
            fallback = settings.etl_temp_dir
            log.warning(
                "GATE-03 preflight disk: %s not visible from this container — "
                "measuring %s instead (same-disk assumption)",
                pg_data_dir,
                fallback,
            )
            try:
                return shutil.disk_usage(fallback).free, fallback
            except OSError:
                log.warning(
                    "GATE-03 preflight disk: fallback stat %s failed — skipping",
                    fallback,
                    exc_info=True,
                )
                return None
        except OSError:
            log.warning(
                "GATE-03 preflight disk: could not stat %s — skipping",
                pg_data_dir,
                exc_info=True,
            )
            return None

    @staticmethod
    def _check_preflight_disk(
        pipeline: BasePipeline,
        dsn: str,
        log: logging.Logger,
    ) -> str | None:
        """GATE-03: verify PostgreSQL data directory has enough free space.

        Required space = pg_total_relation_size(production_table) * 2.
        Checks the PostgreSQL data_directory mount (not etl_temp_dir) because
        that is where staging tables reside.

        On insufficient space, attempts an inline drop of all tracked _old
        tables before aborting — they are the largest recoverable space.
        If still short after cleanup, returns an error message.

        Returns None if space is sufficient, error string if not.
        """
        try:
            with psycopg.connect(dsn) as conn:
                row = conn.execute(
                    "SELECT pg_total_relation_size(%s), "
                    "current_setting('data_directory')",
                    (pipeline.production_table_name,),
                ).fetchone()
        except Exception:  # noqa: BLE001
            log.warning(
                "GATE-03 preflight disk: could not query prod table size — skipping",
                exc_info=True,
            )
            return None

        if row is None:
            log.warning(
                "GATE-03 preflight disk: no result from"
                " pg_total_relation_size — skipping"
            )
            return None

        prod_size_bytes: int = row[0] or 0
        pg_data_dir: str = row[1]

        if prod_size_bytes == 0:
            # New dataset with no data yet; no space concern
            log.info(
                "GATE-03 preflight disk: production table %s is empty, skipping check",
                pipeline.production_table_name,
            )
            return None

        required_bytes = prod_size_bytes * 2
        if pipeline.is_department_scope_limited:
            scope_ratio = len(pipeline.department_scope) / len(DEPARTEMENTS)
            required_bytes = max(
                1,
                int(prod_size_bytes * scope_ratio * 2),
            )
            log.info(
                "GATE-03 scoped disk estimate for %s: %.1f%% of production size",
                pipeline.dataset_name,
                scope_ratio * 100,
            )

        stat = PipelineRunner._gate03_free_bytes(pg_data_dir, log)
        if stat is None:
            return None
        free_bytes, measured_path = stat

        if free_bytes >= required_bytes:
            log.info(
                "GATE-03 preflight disk passed for %s: "
                "need %.1f GB, have %.1f GB free at %s",
                pipeline.dataset_name,
                required_bytes / 1e9,
                free_bytes / 1e9,
                measured_path,
            )
            return None

        # Attempt inline cleanup of _old tables to recover space
        log.warning(
            "GATE-03 preflight disk: need %.1f GB but only %.1f GB free at %s — "
            "attempting inline _old table cleanup",
            required_bytes / 1e9,
            free_bytes / 1e9,
            measured_path,
        )
        try:
            PipelineRunner._inline_cleanup_old_tables(dsn, log)
        except Exception as exc:  # noqa: BLE001
            # API-4: the cleanup rolled back because an expected matview could
            # not be rebuilt. Nothing was dropped and no space was freed, so
            # the gate aborts the run explicitly instead of letting a bare
            # traceback cross run().
            msg = (
                "GATE-03 inline cleanup rolled back: a matview could not be "
                f"recreated, run aborted on an unrepaired schema — {exc}"
            )
            log.error(msg)  # noqa: TRY400
            return msg

        # Re-check after cleanup
        stat = PipelineRunner._gate03_free_bytes(pg_data_dir, log)
        if stat is None:
            return None
        free_bytes, measured_path = stat

        if free_bytes >= required_bytes:
            log.info(
                "GATE-03 preflight disk OK after inline cleanup: %.1f GB free at %s",
                free_bytes / 1e9,
                measured_path,
            )
            return None

        msg = (
            f"GATE-03 insufficient disk: need {required_bytes / 1e9:.1f} GB "
            f"(prod x 2), only {free_bytes / 1e9:.1f} GB free at {measured_path}"
        )
        log.error(msg)
        return msg


def _archive_objects(pipeline: BasePipeline) -> Path | None:
    """Objects directory of the dataset in the source archive, if configured.

    Replay links (``SourceReplay.link``) point there; they are the only links
    the source inventory accepts (plan 04.1-05).
    """
    if not settings.source_archive_dir:
        return None
    return Path(settings.source_archive_dir) / pipeline.dataset_name / "objects"


def _source_file_entry(path: Path) -> dict[str, object]:
    """Fingerprint one source file; CSV text or gzip also get a line count."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    entry: dict[str, object] = {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }
    name = path.name.lower()
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rb") as stream:
            lines = sum(1 for _ in stream)
        # Physical lines minus the header line, NOT records: a quoted CSV
        # field may span several lines (plan 04-15, C8; formerly "rows").
        entry["physical_lines"] = max(0, lines - 1)
    return entry


class _StageError(Exception):
    """Internal sentinel -- a stage failed and the pipeline must abort."""
