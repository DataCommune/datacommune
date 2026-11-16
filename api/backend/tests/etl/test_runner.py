"""Unit tests for PipelineRunner checkpoint / resume logic.

Covers the five resume decision cases:
  1. No checkpoint, no staging -> fresh run, create_staging_table called
  2. Both exist (checkpoint + staging) -> resume, create_staging_table NOT called,
     files in loaded set skipped
  3. Checkpoint exists but staging missing -> checkpoint cleared, fresh run
  4. Staging exists but no checkpoint -> staging dropped, fresh run
  5. Successful run -> checkpoint.clear() called, cleanup_download_dir called

Tests do NOT connect to a real database.  All psycopg / DB calls are mocked.
"""

import typing
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.etl.base import BasePipeline
from app.etl.runner import PipelineRunner

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    # FakePipeline is synthetic: its name is not a catalogue key.
    host_test_datasets("test_ds")


# ---------------------------------------------------------------------------
# Minimal test pipeline
# ---------------------------------------------------------------------------


class FakePipeline(BasePipeline):
    """Concrete pipeline with no side-effects for runner tests."""

    _files: typing.ClassVar[list[Path]] = []

    @property
    def dataset_name(self) -> str:
        return "test_ds"

    @property
    def expected_columns(self) -> list[str]:
        return ["id"]

    @property
    def staging_table_name(self) -> str:
        return "test_ds_staging"

    @property
    def production_table_name(self) -> str:
        return "test_ds"

    @property
    def has_department_partitions(self) -> bool:
        return False

    @property
    def min_success_ratio(self) -> float:
        return 0.0  # no threshold check

    @property
    def min_staging_rows(self) -> int:
        return 0  # no gate-01

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        return True  # no gate-02

    def download(self) -> list[Path]:
        return list(self._files)

    def validate_schema(self, file_path: Path) -> None:
        pass

    def transform(self, file_path: Path) -> Path:
        return file_path

    def stage(self, file_path: Path) -> int:
        return 10

    def swap(self) -> None:
        pass

    def create_staging_table(self, dsn: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner_mocks(
    *,
    staging_exists: bool,
    checkpoint_data: dict | None,
    files: list[str] | None = None,
) -> dict:
    """Return a dict of patches that fake all external calls in PipelineRunner.run().

    Returned patches:
      - check_disk_space: no-op
      - _check_preflight_disk: returns None (success)
      - _insert_etl_run: returns 1
      - _update_etl_run: no-op
      - _record_source_files: no-op
      - _update_dataset_metadata: no-op
      - _staging_table_exists: returns staging_exists
      - CheckpointManager.load: returns checkpoint_data
      - CheckpointManager.save: no-op spy
      - CheckpointManager.clear: no-op spy
      - pipeline.create_staging_table: no-op spy
      - pipeline.drop_staging_table: no-op spy
      - cleanup_download_dir: no-op spy
    """
    file_paths = [Path(f) for f in (files or [])]
    return {
        "file_paths": file_paths,
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline():
    p = FakePipeline()
    p._files = []
    return p


@pytest.fixture
def runner():
    return PipelineRunner()


def _patch_all(  # noqa: C901
    *,
    staging_exists: bool,
    checkpoint_data: dict | None,
    file_paths: list[Path] | None = None,
):
    """Context-manager factory — patches everything for a clean runner test."""

    class Patches:
        def __init__(self):
            self._patches = []
            self.mocks = {}
            self.checkpoint = deepcopy(checkpoint_data)
            self.stock = len((checkpoint_data or {}).get("loaded_files", [])) * 10

        def _add(self, target, **kwargs):
            p = patch(target, **kwargs)
            self._patches.append(p)
            return p

        def __enter__(self):  # noqa: C901
            # Old test dictionaries describe valid resume states, not legacy JSON.
            # The separate resume contract suite proves v1 rejection on disk.
            if self.checkpoint is not None:
                key = self.checkpoint.setdefault("scope_key", "")
                self.checkpoint.update(
                    version=2,
                    identity_key=FakePipeline().checkpoint_identity(key),
                    pending=None,
                    staging_rows=self.stock,
                )
            fingerprint = PipelineRunner._fingerprint_sources
            process_file = PipelineRunner._process_file

            def source_evidence(pipeline, files):
                evidence = fingerprint(pipeline, files)
                if self.checkpoint is not None:
                    self.checkpoint.setdefault("source_files", evidence)
                return evidence

            def process_and_count(runner, pipeline, *args, **kwargs):
                affected = process_file(runner, pipeline, *args, **kwargs)
                self.stock += affected
                return affected

            self.mocks["source_evidence"] = self._add(
                "app.etl.runner.PipelineRunner._fingerprint_sources",
                side_effect=source_evidence,
            ).start()
            self.mocks["stock"] = self._add(
                "app.etl.runner.PipelineRunner._count_staging_rows",
                side_effect=lambda *args: self.stock,
            ).start()
            self.mocks["process_file"] = self._add(
                "app.etl.runner.PipelineRunner._process_file",
                autospec=True,
                side_effect=process_and_count,
            ).start()
            # External side-effects
            self.mocks["check_disk_space"] = self._add(
                "app.etl.runner.check_disk_space"
            ).start()
            self.mocks["check_preflight_disk"] = self._add(
                "app.etl.runner.PipelineRunner._check_preflight_disk",
                return_value=None,
            ).start()
            self.mocks["insert_etl_run"] = self._add(
                "app.etl.runner.PipelineRunner._insert_etl_run",
                return_value=1,
            ).start()
            self.mocks["update_etl_run"] = self._add(
                "app.etl.runner.PipelineRunner._update_etl_run"
            ).start()
            self.mocks["record_source_files"] = self._add(
                "app.etl.runner.PipelineRunner._record_source_files"
            ).start()
            self.mocks["update_metadata"] = self._add(
                "app.etl.runner.PipelineRunner._update_dataset_metadata"
            ).start()
            # Checkpoint helpers
            self.mocks["staging_exists"] = self._add(
                "app.etl.runner.PipelineRunner._staging_table_exists",
                return_value=staging_exists,
            ).start()
            self.mocks["checkpoint_load"] = self._add(
                "app.etl.checkpoint.CheckpointManager.load",
                return_value=self.checkpoint,
            ).start()
            self.mocks["checkpoint_save"] = self._add(
                "app.etl.checkpoint.CheckpointManager.save"
            ).start()
            self.mocks["checkpoint_clear"] = self._add(
                "app.etl.checkpoint.CheckpointManager.clear"
            ).start()
            # DB-touching helpers
            self.mocks["row_regression"] = self._add(
                "app.etl.runner.PipelineRunner._check_row_regression",
                return_value=(None, None),
            ).start()
            self.mocks["department_coverage"] = self._add(
                "app.etl.runner.PipelineRunner._check_department_coverage",
                return_value=None,
            ).start()
            # The scope filter runs on every scope (the full one included,
            # 04.2-03); its SQL is covered by tests/integration.
            self.mocks["scope_filter"] = self._add(
                "app.etl.runner.PipelineRunner._apply_scope_filter",
                return_value=0,
            ).start()
            self.mocks["cleanup_dir"] = self._add(
                "app.etl.runner.cleanup_download_dir"
            ).start()
            # Post-publication drop of the previous generation (real PG tests
            # restore it, see test_swap_without_rewrite.py).
            self.mocks["drop_published_old"] = self._add(
                "app.etl.runner.PipelineRunner._drop_published_old_tables"
            ).start()
            # advisory_lock opens a real psycopg connection; replace with a
            # no-op context manager so the swap stage reaches pipeline.swap().
            from contextlib import contextmanager

            @contextmanager
            def _noop_lock(_dsn, _table):
                yield

            self.mocks["advisory_lock"] = self._add(
                "app.etl.runner.advisory_lock", new=_noop_lock
            ).start()
            self.mocks["dataset_execution_lock"] = self._add(
                "app.etl.runner.dataset_execution_lock",
                new=MagicMock(side_effect=_noop_lock),
            ).start()

            class PublicationDouble:
                """Unit-only state machine; PG tests prove real transaction effects."""

                def __init__(self, _dsn, pipeline):
                    self.pipeline = pipeline
                    self.row_count = 0
                    self.active = False
                    self.state = "pending"
                    self.staged_publication = False

                def __enter__(self):
                    return self

                def observe_stock(self):
                    self.row_count = len(self.pipeline._files) * 10
                    return self.row_count

                def publish(self, update_metadata):
                    assert self.active and self.state == "pending"
                    update_metadata(None)
                    self.staged_publication = True

                @contextmanager
                def required_work(self):
                    assert not self.active
                    self.active = True
                    try:
                        yield self
                        assert self.staged_publication
                        self.state = "published"
                    except BaseException:
                        self.staged_publication = False
                        raise
                    finally:
                        self.active = False

                def __exit__(self, exc_type, *_args):
                    if exc_type is not None:
                        self.state = "failed"

            self.mocks["requires_fresh_start"] = self._add(
                "app.etl.runner.requires_fresh_start", return_value=False
            ).start()
            self.mocks["require_publication_checks"] = self._add(
                "app.etl.runner.PipelineRunner._require_publication_checks"
            ).start()
            self.mocks["publication"] = self._add(
                "app.etl.runner.Publication",
                new=MagicMock(side_effect=PublicationDouble),
            ).start()
            return self.mocks

        def __exit__(self, *args):
            for p in self._patches:
                p.stop()

    return Patches()


class TestFreshStartBothMissing:
    """Case 1: no checkpoint, no staging -> fresh run."""

    def test_create_staging_called(self, pipeline, runner):
        with _patch_all(staging_exists=False, checkpoint_data=None):
            pipeline.create_staging_table = MagicMock()
            pipeline._files = []
            runner.run(pipeline)
            pipeline.create_staging_table.assert_called_once()

    def test_checkpoint_save_not_called_for_empty_files(self, pipeline, runner):
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            pipeline._files = []
            runner.run(pipeline)
            mocks["checkpoint_save"].assert_not_called()


class TestResumeBothPresent:
    """Case 2: checkpoint + staging both exist -> resume."""

    def test_create_staging_not_called(self, pipeline, runner, tmp_path):
        checkpoint_data = {
            "dataset_name": "test_ds",
            "staging_table": "test_ds_staging",
            "loaded_files": ["already.csv"],
        }
        f1 = tmp_path / "already.csv"
        f1.touch()
        f2 = tmp_path / "new.csv"
        f2.touch()
        pipeline._files = [f1, f2]

        with _patch_all(
            staging_exists=True,
            checkpoint_data=checkpoint_data,
            file_paths=[f1, f2],
        ):
            pipeline.create_staging_table = MagicMock()
            runner.run(pipeline)
            pipeline.create_staging_table.assert_not_called()

    def test_already_loaded_file_skipped(self, pipeline, runner, tmp_path):
        checkpoint_data = {
            "dataset_name": "test_ds",
            "staging_table": "test_ds_staging",
            "loaded_files": ["already.csv"],
        }
        f_skip = tmp_path / "already.csv"
        f_skip.touch()
        f_new = tmp_path / "new.csv"
        f_new.touch()
        pipeline._files = [f_skip, f_new]

        with _patch_all(
            staging_exists=True,
            checkpoint_data=checkpoint_data,
        ):
            pipeline.stage = MagicMock(return_value=5)
            runner.run(pipeline)
            # stage() should only be called for f_new
            assert pipeline.stage.call_count == 1
            called_arg = pipeline.stage.call_args[0][0]
            assert called_arg.name == "new.csv"

    def test_checkpoint_saved_for_new_file(self, pipeline, runner, tmp_path):
        checkpoint_data = {
            "dataset_name": "test_ds",
            "staging_table": "test_ds_staging",
            "loaded_files": [],
        }
        f_new = tmp_path / "new.csv"
        f_new.touch()
        pipeline._files = [f_new]

        with _patch_all(staging_exists=True, checkpoint_data=checkpoint_data) as mocks:
            runner.run(pipeline)
            mocks["checkpoint_save"].assert_called()


class TestOrphanCheckpointNoStaging:
    """Case 3: checkpoint exists but staging gone -> checkpoint cleared, fresh run."""

    def test_checkpoint_cleared(self, pipeline, runner):
        checkpoint_data = {
            "dataset_name": "test_ds",
            "staging_table": "test_ds_staging",
            "loaded_files": ["a.csv"],
        }
        with _patch_all(staging_exists=False, checkpoint_data=checkpoint_data) as mocks:
            pipeline._files = []
            runner.run(pipeline)
            mocks["checkpoint_clear"].assert_called()

    def test_create_staging_called_fresh(self, pipeline, runner):
        checkpoint_data = {
            "dataset_name": "test_ds",
            "staging_table": "test_ds_staging",
            "loaded_files": ["a.csv"],
        }
        with _patch_all(staging_exists=False, checkpoint_data=checkpoint_data):
            pipeline.create_staging_table = MagicMock()
            pipeline._files = []
            runner.run(pipeline)
            pipeline.create_staging_table.assert_called_once()


class TestOrphanStagingNoCheckpoint:
    """Case 4: staging exists but no checkpoint -> staging dropped, fresh run."""

    def test_incompatible_staging_is_recreated(self, pipeline, runner):
        with _patch_all(staging_exists=True, checkpoint_data=None):
            pipeline._files = []
            pipeline.create_staging_table = MagicMock()
            runner.run(pipeline)
            pipeline.create_staging_table.assert_called_once()

    def test_create_staging_called_after_drop(self, pipeline, runner):
        with _patch_all(staging_exists=True, checkpoint_data=None):
            pipeline.create_staging_table = MagicMock()
            pipeline._files = []
            runner.run(pipeline)
            pipeline.create_staging_table.assert_called_once()


class TestSuccessPathClearsCheckpoint:
    """Case 5: successful run -> checkpoint cleared, cleanup_download_dir called."""

    def test_checkpoint_cleared_on_success(self, pipeline, runner, tmp_path):
        f = tmp_path / "a.csv"
        f.touch()
        pipeline._files = [f]

        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            pipeline.swap = MagicMock()
            runner.run(pipeline)
            mocks["checkpoint_clear"].assert_called_once()

    def test_checkpoint_cleared_before_cleanup(self, pipeline, runner, tmp_path):
        """checkpoint.clear() must be called before cleanup_download_dir."""
        call_order = []

        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            mocks["checkpoint_clear"].side_effect = lambda: call_order.append("clear")
            mocks["cleanup_dir"].side_effect = lambda _: call_order.append("cleanup")
            pipeline._files = []
            runner.run(pipeline)
            assert call_order.index("clear") < call_order.index("cleanup")

    def test_cleanup_download_dir_called_on_success(self, pipeline, runner):
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            pipeline._files = []
            runner.run(pipeline)
            mocks["cleanup_dir"].assert_called_once_with(pipeline.dataset_name)


class TestStageCounters:
    """Plan 02-16: named pipeline counts reach the steps of the etl_runs row."""

    def test_counters_are_published_as_steps(self, pipeline, runner, tmp_path):
        f = tmp_path / "a.csv"
        f.touch()
        pipeline._files = [f]
        pipeline.stage_counters = lambda: {"documents_sans_geometrie": 3}
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            result = runner.run(pipeline)
        assert result.success, result.error
        steps = [s for s in result.stages if s.stage_name.startswith("count:")]
        assert [(s.stage_name, s.rows_affected, s.success) for s in steps] == [
            ("count:documents_sans_geometrie", 3, True)
        ]
        journal = mocks["update_etl_run"].call_args.args[2]
        assert steps[0] in journal.stages

    def test_swap_counters_reach_the_receipt(self, pipeline, runner, tmp_path):
        """04.7-19: the counts computed by swap() are journaled with the swap
        stage, in the result and in the etl_runs row (they were dropped: the
        result held a copy of the stages taken before the swap)."""
        f = tmp_path / "a.csv"
        f.touch()
        pipeline._files = [f]
        pipeline.swap_counters = lambda: {"ffo_divergents": 0, "publiees_44": 7}
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            result = runner.run(pipeline)
        assert result.success, result.error
        names = [s.stage_name for s in result.stages]
        assert names.index("swap") < names.index("count:ffo_divergents")
        steps = {
            s.stage_name: s.rows_affected
            for s in mocks["update_etl_run"].call_args.args[2].stages
            if s.stage_name.startswith("count:")
        }
        assert steps == {"count:ffo_divergents": 0, "count:publiees_44": 7}

    def test_counts_of_a_refused_swap_reach_the_receipt(
        self, pipeline, runner, tmp_path
    ):
        """04.7-19 (D13): a swap that refuses its candidate still journals the
        counts that refused it; the failure is unchanged."""
        f = tmp_path / "a.csv"
        f.touch()
        pipeline._files = [f]
        pipeline.swap = MagicMock(side_effect=RuntimeError("ffo_divergents"))
        pipeline.swap_counters = lambda: {"ffo_divergents": 1}
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            result = runner.run(pipeline)
        assert not result.success
        assert "ffo_divergents" in result.error
        journal = mocks["update_etl_run"].call_args.args[2]
        steps = [
            (s.stage_name, s.rows_affected)
            for s in journal.stages
            if s.stage_name.startswith("count:")
        ]
        assert steps == [("count:ffo_divergents", 1)]
        assert "swap" not in [s.stage_name for s in journal.stages]

    def test_no_counter_no_step(self, pipeline, runner, tmp_path):
        f = tmp_path / "a.csv"
        f.touch()
        pipeline._files = [f]
        with _patch_all(staging_exists=False, checkpoint_data=None):
            result = runner.run(pipeline)
        assert not [s for s in result.stages if s.stage_name.startswith("count:")]


# ===========================================================================
# Quality Gate unit tests (GATE-01, GATE-02, GATE-03)
# These tests exercise the static methods directly, without a DB connection.
# ===========================================================================


class TestSourceFactsPersistence:
    """Task 44-02-01: optional upstream facts flow to _update_dataset_metadata.

    A pipeline without an override must persist None upstream facts (no current
    timestamp substituted for source_updated_at). A department-scoped pipeline
    forwards its expected coverage; a national/non-scoped one forwards None.
    """

    def test_base_pipeline_source_facts_default_none(self):
        """BasePipeline exposes optional upstream facts defaulting to None."""
        p = FakePipeline()
        assert p.source_period_label is None
        assert p.source_updated_at is None

    def test_runner_passes_none_upstream_facts_by_default(
        self, pipeline, runner, tmp_path
    ):
        """No override -> upstream facts persisted as None; no NOW() substitute."""
        f = tmp_path / "a.csv"
        f.touch()
        pipeline._files = [f]
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            pipeline.swap = MagicMock()
            runner.run(pipeline)
            mocks["update_metadata"].assert_called_once()
            _args, kwargs = mocks["update_metadata"].call_args
            assert "source_period_label" in kwargs
            assert "source_updated_at" in kwargs
            assert "effective_departments" in kwargs
            assert kwargs["source_period_label"] is None
            assert kwargs["source_updated_at"] is None
            assert kwargs["effective_departments"] is None

    def test_runner_forwards_scoped_departments(self, runner, tmp_path):
        """A scope-aware pipeline forwards its expected department coverage."""

        class ScopedPipeline(FakePipeline):
            @property
            def supports_department_scope(self) -> bool:
                return True

            @property
            def expected_department_coverage(self) -> list[str]:
                return ["44", "85"]

        p = ScopedPipeline()
        p._files = []
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            runner.run(p)
            _args, kwargs = mocks["update_metadata"].call_args
            assert kwargs.get("effective_departments") == ["44", "85"]

    def test_runner_forwards_none_departments_for_national(
        self, pipeline, runner, tmp_path
    ):
        """A non-scoped (national) pipeline forwards None effective_departments."""
        pipeline._files = []
        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            runner.run(pipeline)
            _args, kwargs = mocks["update_metadata"].call_args
            assert "effective_departments" in kwargs
            assert kwargs["effective_departments"] is None


class TestGate01MinRowCount:
    """GATE-01: _check_min_row_count static method."""

    def _make_pipeline(self, min_rows: int) -> FakePipeline:
        p = FakePipeline()
        p.__class__ = type(
            "DynPipeline",
            (FakePipeline,),
            {"min_staging_rows": property(lambda self: min_rows)},
        )
        return p

    def test_disabled_returns_none(self):
        """min_staging_rows=0 disables the check (explicit opt-out)."""
        p = self._make_pipeline(0)
        import logging

        log = logging.getLogger("test")
        result = PipelineRunner._check_min_row_count(p, 0, log)
        assert result is None

    def test_deferred_when_final_floor_set(self):
        """min_staging_rows=0 + min_staging_rows_final>0 -> deferred, not failed.

        Flat-staging pipelines (DPE, BAN, Monuments) enforce the floor inside
        swap(); the runner-level check must pass regardless of flat row count.
        """
        import logging

        p = FakePipeline()
        p.__class__ = type(
            "FlatPipeline",
            (FakePipeline,),
            {
                "min_staging_rows": property(lambda self: 0),
                "min_staging_rows_final": property(lambda self: 500),
            },
        )
        log = logging.getLogger("test")
        result = PipelineRunner._check_min_row_count(p, 0, log)
        assert result is None

    def test_base_default_applies_fallback_floor(self):
        """BasePipeline default floor = settings.etl_min_staging_rows (GATE-B').

        A pipeline that defines no floor of its own must not be able to swap
        a quasi-empty staging table.
        """
        import logging

        from app.config import settings

        p = FakePipeline()
        p.__class__ = type(
            "DefaultFloorPipeline",
            (FakePipeline,),
            {"min_staging_rows": BasePipeline.min_staging_rows},
        )
        assert p.min_staging_rows == settings.etl_min_staging_rows

        log = logging.getLogger("test")
        # Quasi-empty staging is blocked...
        result = PipelineRunner._check_min_row_count(p, 10, log)
        assert result is not None
        assert "swap aborted" in result.lower()
        # ...while a real load passes.
        assert (
            PipelineRunner._check_min_row_count(p, settings.etl_min_staging_rows, log)
            is None
        )

    def test_pass_returns_none(self):
        """staging rows >= floor -> no error."""
        p = self._make_pipeline(1_000)
        import logging

        log = logging.getLogger("test")
        result = PipelineRunner._check_min_row_count(p, 2_000, log)
        assert result is None

    def test_exact_floor_returns_none(self):
        """staging rows == floor -> no error (boundary)."""
        p = self._make_pipeline(1_000)
        import logging

        log = logging.getLogger("test")
        result = PipelineRunner._check_min_row_count(p, 1_000, log)
        assert result is None

    def test_fail_returns_error_string(self):
        """staging rows < floor -> error string naming observed vs expected."""
        p = self._make_pipeline(1_000)
        import logging

        log = logging.getLogger("test")
        result = PipelineRunner._check_min_row_count(p, 100, log)
        assert result is not None
        assert "100" in result
        assert "1,000" in result
        assert "swap aborted" in result.lower()

    def test_zero_staging_rows_fails(self):
        """Zero rows with non-zero floor must fail."""
        p = self._make_pipeline(500)
        import logging

        log = logging.getLogger("test")
        result = PipelineRunner._check_min_row_count(p, 0, log)
        assert result is not None


class TestGate02SchemaDrift:
    """GATE-02: _check_schema_drift static method."""

    def _make_pipeline(self, mode: str = "warn") -> FakePipeline:
        p = FakePipeline()
        p.__class__ = type(
            "DynPipeline",
            (FakePipeline,),
            {"schema_drift_mode": property(lambda self: mode)},
        )
        return p

    def _mock_conn_columns(self, prod_cols, staging_cols):
        """Return a mock psycopg connection that serves two column queries."""
        from unittest.mock import MagicMock

        conn = MagicMock()
        # First call: production, second call: staging
        conn.execute.return_value.fetchall.side_effect = [prod_cols, staging_cols]
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=conn)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_no_drift_returns_none(self):
        """Identical schemas produce no error."""
        import logging

        log = logging.getLogger("test")
        cols = [("id", "integer"), ("name", "text")]
        p = self._make_pipeline("warn")
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchall.side_effect = [cols, cols]
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            result = PipelineRunner._check_schema_drift(p, "dsn://test", log)
        assert result is None

    def test_removed_column_warn_mode_returns_none(self):
        """Removed column in warn mode -> logs WARNING, returns None (swap proceeds)."""
        import logging

        log = logging.getLogger("test")
        prod_cols = [("id", "integer"), ("name", "text"), ("extra", "text")]
        staging_cols = [("id", "integer"), ("name", "text")]  # extra removed
        p = self._make_pipeline("warn")
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchall.side_effect = [prod_cols, staging_cols]
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            result = PipelineRunner._check_schema_drift(p, "dsn://test", log)
        assert result is None

    def test_removed_column_abort_mode_returns_error(self):
        """Removed column in abort mode -> returns error string (swap blocked)."""
        import logging

        log = logging.getLogger("test")
        prod_cols = [("id", "integer"), ("name", "text"), ("extra", "text")]
        staging_cols = [("id", "integer"), ("name", "text")]  # extra removed
        p = self._make_pipeline("abort")
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchall.side_effect = [prod_cols, staging_cols]
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            result = PipelineRunner._check_schema_drift(p, "dsn://test", log)
        assert result is not None
        assert "removed" in result
        assert "extra" in result

    def test_added_column_warn_mode_returns_none(self):
        """Added column in warn mode -> returns None (tolerated, swap proceeds)."""
        import logging

        log = logging.getLogger("test")
        prod_cols = [("id", "integer")]
        staging_cols = [("id", "integer"), ("new_col", "text")]  # new col added
        p = self._make_pipeline("warn")
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchall.side_effect = [prod_cols, staging_cols]
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            result = PipelineRunner._check_schema_drift(p, "dsn://test", log)
        assert result is None

    def test_type_changed_abort_mode_returns_error(self):
        """Type changed in abort mode -> error string."""
        import logging

        log = logging.getLogger("test")
        prod_cols = [("id", "integer"), ("value", "integer")]
        staging_cols = [("id", "integer"), ("value", "text")]  # type changed
        p = self._make_pipeline("abort")
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchall.side_effect = [prod_cols, staging_cols]
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            result = PipelineRunner._check_schema_drift(p, "dsn://test", log)
        assert result is not None
        assert "type_changed" in result

    def test_db_error_returns_none(self):
        """DB query failure in drift check -> skip (return None, not abort)."""
        import logging

        log = logging.getLogger("test")
        p = self._make_pipeline("abort")
        with patch("psycopg.connect", side_effect=Exception("conn failed")):
            result = PipelineRunner._check_schema_drift(p, "dsn://test", log)
        assert result is None


class TestGate03PreflightDisk:
    """GATE-03: _check_preflight_disk static method."""

    def _make_pipeline(self) -> FakePipeline:
        return FakePipeline()

    def test_enough_free_space_returns_none(self):
        """Free space >= required -> gate passes."""
        import logging

        log = logging.getLogger("test")
        p = self._make_pipeline()
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            # prod_size = 1 GB, pg_data_dir = /tmp
            conn.execute.return_value.fetchone.return_value = (1_000_000_000, "/tmp")
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage") as mock_du:
                # 10 GB free > 2 GB required
                mock_du.return_value = type("DU", (), {"free": 10_000_000_000})()
                result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is None

    def test_no_free_space_returns_error(self):
        """Free space < required after cleanup attempt -> error string."""
        import logging

        log = logging.getLogger("test")
        p = self._make_pipeline()
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            # prod_size = 10 GB
            conn.execute.return_value.fetchone.return_value = (
                10_000_000_000,
                "/var/lib/postgresql",
            )
            # etl_old_tables query returns empty (no tables to clean)
            conn.execute.return_value.fetchall.return_value = []
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage") as mock_du:
                # Only 1 GB free < 20 GB required
                mock_du.return_value = type("DU", (), {"free": 1_000_000_000})()
                result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is not None
        assert "insufficient disk" in result.lower() or "Insufficient disk" in result
        assert "GB" in result

    def test_cleanup_frees_enough_space_returns_none(self):
        """Cleanup frees enough space -> gate passes after cleanup."""
        import logging

        log = logging.getLogger("test")
        p = self._make_pipeline()
        call_count = {"n": 0}

        def disk_usage_side_effect(path):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First check: not enough
                return type("DU", (), {"free": 500_000_000})()
            else:
                # After cleanup: enough
                return type("DU", (), {"free": 10_000_000_000})()

        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            # prod_size = 1 GB
            conn.execute.return_value.fetchone.return_value = (
                1_000_000_000,
                "/var/lib/postgresql",
            )
            conn.execute.return_value.fetchall.return_value = [(1, "some_table_old")]
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage", side_effect=disk_usage_side_effect):
                result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is None

    def test_empty_prod_table_skips_check(self):
        """prod_size=0 (empty/new table) -> gate is skipped."""
        import logging

        log = logging.getLogger("test")
        p = self._make_pipeline()
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = (0, "/var/lib/postgresql")
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage") as mock_du:
                result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is None
        mock_du.assert_not_called()

    def test_pg_dir_not_found_falls_back_to_etl_temp_dir(self):
        """FileNotFoundError on data_directory -> conscious fallback, gate passes.

        Containerized deployments: the db container's data_directory path is
        not visible from the api container. The gate must measure
        settings.etl_temp_dir instead of being silently skipped.
        """
        import logging

        from app.config import settings

        log = logging.getLogger("test")
        p = self._make_pipeline()
        measured: list[str] = []

        def disk_usage_side_effect(path):
            if path == "/var/lib/postgresql/data":
                raise FileNotFoundError(path)
            measured.append(path)
            return type("DU", (), {"free": 10_000_000_000})()

        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            # prod_size = 1 GB -> required 2 GB < 10 GB free on fallback
            conn.execute.return_value.fetchone.return_value = (
                1_000_000_000,
                "/var/lib/postgresql/data",
            )
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage", side_effect=disk_usage_side_effect):
                result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is None
        assert measured == [settings.etl_temp_dir]

    def test_pg_dir_not_found_fallback_still_blocks(self):
        """The fallback measurement still aborts when space is insufficient."""
        import logging

        from app.config import settings

        log = logging.getLogger("test")
        p = self._make_pipeline()

        def disk_usage_side_effect(path):
            if path == "/var/lib/postgresql/data":
                raise FileNotFoundError(path)
            # 1 GB free on the fallback mount < 20 GB required
            return type("DU", (), {"free": 1_000_000_000})()

        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            # prod_size = 10 GB -> required 20 GB
            conn.execute.return_value.fetchone.return_value = (
                10_000_000_000,
                "/var/lib/postgresql/data",
            )
            # etl_old_tables query returns empty (no tables to clean)
            conn.execute.return_value.fetchall.return_value = []
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage", side_effect=disk_usage_side_effect):
                result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is not None
        assert "insufficient disk" in result.lower()
        assert settings.etl_temp_dir in result

    def test_fallback_stat_failure_skips_gate(self):
        """Both data_directory and fallback unreadable -> skip gate (None)."""
        import logging

        log = logging.getLogger("test")
        p = self._make_pipeline()

        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = (
                1_000_000_000,
                "/var/lib/postgresql/data",
            )
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage", side_effect=FileNotFoundError("gone")):
                result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is None

    def test_db_error_returns_none(self):
        """DB query failure -> skip gate (return None, not abort)."""
        import logging

        log = logging.getLogger("test")
        p = self._make_pipeline()
        with patch("psycopg.connect", side_effect=Exception("conn failed")):
            result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)
        assert result is None


class TestGate01DpeSwapIntegration:
    """GATE-01 for DPE flat-staging: RuntimeError in swap() when final rows < min."""

    def test_dpe_min_staging_rows_is_zero(self):
        """DPE runner-level check must be disabled (returns 0)."""
        from app.etl_dpe.pipeline import DpePipeline

        p = DpePipeline()
        assert p.min_staging_rows == 0

    def test_dpe_min_staging_rows_final_scales_to_measured_scope(
        self, bind_departements
    ):
        """80 % of the producer count of each departement of the scope
        (plan 04.1-17), no linear share of a national floor."""
        from app.etl_dpe.pipeline import DpePipeline
        from app.scope.producer_counts import PRODUCER_COUNTS

        bind_departements(("44", "85"), DpePipeline)
        p = DpePipeline()
        assert p.department_scope == ["44", "85"]
        comptes = PRODUCER_COUNTS["dpe"]["lignes_importees"]
        assert p.min_staging_rows_final == int(comptes["44"] * 0.8) + int(
            comptes["85"] * 0.8
        )

    def test_dpe_skip_runner_schema_drift_check(self):
        """DPE must skip runner-level schema drift check."""
        from app.etl_dpe.pipeline import DpePipeline

        p = DpePipeline()
        assert p.skip_runner_schema_drift_check is True


class TestInlineCleanupMatviewRepair:
    """API-4: the GATE-03 inline twin repairs matviews in its own transaction."""

    @staticmethod
    def _make_conn(
        old_rows: list[tuple[int, str]],
        present_matviews: tuple[str, ...],
    ) -> tuple[MagicMock, list[str]]:
        """psycopg connection double: ordered event log + canned results."""
        events: list[str] = []
        conn = MagicMock()

        def _execute(sql, params=None):
            events.append(sql)
            result = MagicMock()
            if "pg_matviews" in sql:
                result.fetchall.return_value = [(n,) for n in present_matviews]
            elif "FROM etl_old_tables" in sql:
                result.fetchall.return_value = list(old_rows)
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute
        conn.commit.side_effect = lambda: events.append("COMMIT")
        conn.rollback.side_effect = lambda: events.append("ROLLBACK")
        return conn, events

    @staticmethod
    def _spy_recreate(
        monkeypatch,
        events: list[str],
        failing: str | None = None,
    ) -> list[str]:
        """Replace every canonical DDL rebuilder with a recording double."""
        from app.common.matviews import EXPECTED_MATVIEWS

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

    def test_rebuilds_missing_matview_before_the_single_commit(self, monkeypatch):
        """communes_lookup cascaded away by the adresses_old drop is rebuilt."""
        import logging

        log = logging.getLogger("test")
        conn, events = self._make_conn(
            [(1, "adresses_old")], ("v_commune_synthese", "scoring_renov_target")
        )
        recreated = self._spy_recreate(monkeypatch, events)

        with patch("psycopg.connect") as mock_connect:
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            PipelineRunner._inline_cleanup_old_tables("dsn://test", log)

        assert recreated == ["communes_lookup"]
        assert conn.commit.call_count == 1
        assert events.index("RECREATE communes_lookup") < events.index("COMMIT")
        conn.rollback.assert_not_called()

    def test_no_old_table_means_no_pg_matviews_probe(self, monkeypatch):
        """Nothing dropped, nothing cascaded: no probe, no DDL, no commit."""
        import logging

        log = logging.getLogger("test")
        conn, events = self._make_conn([], ())
        recreated = self._spy_recreate(monkeypatch, events)

        with patch("psycopg.connect") as mock_connect:
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            PipelineRunner._inline_cleanup_old_tables("dsn://test", log)

        assert recreated == []
        assert not [e for e in events if "pg_matviews" in e]
        conn.commit.assert_not_called()

    def test_failed_rebuild_rolls_back_and_propagates(self, monkeypatch):
        """Failed rebuild: ROLLBACK, no commit, exception propagated."""
        import logging

        log = logging.getLogger("test")
        conn, events = self._make_conn([(1, "revenus_old")], ())
        recreated = self._spy_recreate(
            monkeypatch, events, failing="v_commune_synthese"
        )

        with patch("psycopg.connect") as mock_connect:
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(RuntimeError, match="cannot rebuild v_commune"):
                PipelineRunner._inline_cleanup_old_tables("dsn://test", log)

        assert recreated == ["v_commune_synthese"]
        conn.rollback.assert_called()
        conn.commit.assert_not_called()
        assert "COMMIT" not in events

    def test_preflight_disk_aborts_the_run_when_cleanup_is_rolled_back(self):
        """GATE-03 returns a message -> run() aborts with success=False."""
        import logging

        log = logging.getLogger("test")
        p = FakePipeline()
        with patch("psycopg.connect") as mock_connect:
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = (
                10_000_000_000,
                "/var/lib/postgresql",
            )
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            with patch("shutil.disk_usage") as mock_du:
                mock_du.return_value = type("DU", (), {"free": 1_000_000_000})()
                with patch.object(
                    PipelineRunner,
                    "_inline_cleanup_old_tables",
                    side_effect=RuntimeError("cannot rebuild v_commune_synthese"),
                ):
                    result = PipelineRunner._check_preflight_disk(p, "dsn://test", log)

        assert result is not None
        assert "GATE-03" in result
        assert "matview" in result.lower()


@pytest.mark.parametrize("dry_run", [True, False])
def test_runner_closes_optional_resources_on_success(
    pipeline, runner, dry_run, tmp_path
):
    source = tmp_path / "part.csv"
    source.write_bytes(b"id\n1\n")
    pipeline._files = [source]
    pipeline.close = MagicMock()
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = runner.run(pipeline, dry_run=dry_run)
    assert result.success
    pipeline.close.assert_called_once_with()


def test_runner_closes_optional_resources_without_masking_failure(pipeline, runner):
    pipeline.download = MagicMock(side_effect=ValueError("source rejected"))
    pipeline.close = MagicMock(side_effect=RuntimeError("cleanup failure"))
    pipeline.drop_staging_table = MagicMock()
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = runner.run(pipeline, dry_run=True)
    assert not result.success
    assert "source rejected" in result.error
    pipeline.close.assert_called_once_with()


def test_runner_closes_resources_on_preflight_exception(pipeline, runner):
    pipeline.get_sync_dsn = MagicMock(side_effect=ValueError("bad configuration"))
    pipeline.close = MagicMock()
    with pytest.raises(ValueError, match="bad configuration"):
        runner.run(pipeline)
    pipeline.close.assert_called_once_with()


class RequiredCompletionHook:
    required_on_complete = True

    def __init__(self, fail=True):
        self.fail = fail
        self.calls = []

    def on_run_start(self, dataset):
        pass

    def on_run_complete(self, dataset, result):
        self.calls.append("complete")
        if self.fail:
            raise RuntimeError("private database diagnostic must not enter result")

    def on_run_failure(self, dataset, error):
        self.calls.append("failure")


def test_required_rebuild_failure_is_persisted_after_successful_swap(pipeline):
    hook = RequiredCompletionHook()
    with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
        result = PipelineRunner([hook]).run(pipeline)
    assert result.success is False
    assert "after publication" in result.error
    assert "private database" not in result.error
    assert any(stage.stage_name == "swap" and stage.success for stage in result.stages)
    assert hook.calls == ["complete", "failure"]
    assert mocks["update_etl_run"].call_args.args[2].success is False
    mocks["checkpoint_clear"].assert_not_called()


def test_required_rebuild_is_not_executed_on_dry_run(pipeline):
    hook = RequiredCompletionHook()
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = PipelineRunner([hook]).run(pipeline, dry_run=True)
    assert result.success is True
    assert hook.calls == []


def test_optional_completion_failure_remains_best_effort(pipeline):
    hook = RequiredCompletionHook()
    hook.required_on_complete = False
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = PipelineRunner([hook]).run(pipeline)
    assert result.success is True
    assert hook.calls == ["complete"]


def test_dependent_views_are_required_completion_work():
    from app.etl.hooks import DependentMatviewHook

    assert DependentMatviewHook.required_on_complete is True


# ---------------------------------------------------------------------------
# Department coverage on a flat staging table without code_departement (02-21)
# ---------------------------------------------------------------------------


class _FlatTable:
    """Connection stub: one staging table whose columns are *columns*.

    A query naming a column the table lacks fails as PostgreSQL does
    (UndefinedColumn); otherwise the departement counts are returned.
    """

    def __init__(self, columns, counts):
        self.columns = set(columns)
        self.counts = counts
        self.queries: list[str] = []

    def execute(self, query, params):
        import psycopg

        self.queries.append(query)
        if "code_departement" in query and "code_departement" not in self.columns:
            raise psycopg.errors.UndefinedColumn(
                'column "code_departement" does not exist'
            )
        rows = [] if "DISTINCT" in query else list(self.counts.items())
        return MagicMock(fetchall=MagicMock(return_value=rows))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_COMMUNE_DEPARTEMENT = (
    "CASE WHEN LEFT(commune, 2) = '97' THEN LEFT(commune, 3) ELSE LEFT(commune, 2) END"
)


class FlatCommunePipeline(FakePipeline):
    """Departmental download staged in a flat table holding only `commune`."""

    @property
    def flat_staging_table_name(self) -> str:
        return "_test_ds_staging_flat"

    @property
    def supports_department_scope(self) -> bool:
        return True


class DeclaredCoveragePipeline(FlatCommunePipeline):
    @property
    def coverage_departement_sql(self) -> dict[str, str] | None:
        return {"_test_ds_staging_flat": _COMMUNE_DEPARTEMENT}


def _bound(pipeline_cls):
    from app.scope import InstanceScope

    pipeline = pipeline_cls()
    pipeline.bind_scope(InstanceScope(frozenset({"test_ds"}), ("44", "85")))
    return pipeline


def _coverage(monkeypatch, pipeline, table):
    monkeypatch.setattr("app.etl.runner.psycopg.connect", lambda _dsn: table)
    return PipelineRunner._check_department_coverage(
        "postgresql://x", pipeline, MagicMock()
    )


class TestCoverageDepartementSql:
    def test_base_pipeline_declares_no_coverage_expression(self):
        assert FakePipeline().coverage_departement_sql is None

    def test_declared_expression_passes_the_flat_table(self, monkeypatch):
        table = _FlatTable({"commune"}, {"44": 1_570_363, "85": 1_279_746})
        error = _coverage(monkeypatch, _bound(DeclaredCoveragePipeline), table)
        assert error is None
        assert table.queries
        assert all(
            _COMMUNE_DEPARTEMENT in q and "_test_ds_staging_flat" in q
            for q in table.queries
        )

    def test_declared_expression_still_names_a_missing_departement(self, monkeypatch):
        table = _FlatTable({"commune"}, {"44": 1_570_363})
        error = _coverage(monkeypatch, _bound(DeclaredCoveragePipeline), table)
        assert error == (
            "Department coverage: missing scoped departments ['85'] "
            "in _test_ds_staging_flat -- swap aborted"
        )

    def test_without_the_property_the_check_reads_code_departement(self, monkeypatch):
        # Unchanged fallback: the staging table on code_departement. On a flat
        # table without that column the guard stays closed (02-11 receipt).
        table = _FlatTable({"commune"}, {"44": 1, "85": 1})
        error = _coverage(monkeypatch, _bound(FlatCommunePipeline), table)
        assert error == "Department coverage check unreadable -- swap aborted"
        assert "FROM _test_ds_staging_flat" in table.queries[0]
        assert "code_departement" in table.queries[0]

        with_column = _FlatTable({"commune", "code_departement"}, {"44": 1, "85": 1})
        assert _coverage(monkeypatch, _bound(FlatCommunePipeline), with_column) is None
        assert all("code_departement" in q for q in with_column.queries)
