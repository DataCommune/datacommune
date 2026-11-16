"""Tests for ETL framework: BasePipeline, PipelineRunner, download module.

Covers: pipeline lifecycle, runner orchestration (success, failure, threshold),
download utilities (disk check, gzip integrity, cleanup), scheduler jobs.
"""

import gzip
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.etl.base import DEPARTEMENTS, BasePipeline
from app.etl.download import (
    IntegrityError,
    check_disk_space,
    cleanup_download_dir,
    get_download_dir,
    validate_gzip_integrity,
)
from app.etl.runner import PipelineRunner
from app.etl.schemas import EtlResult
from app.scope.model import InstanceScope, parse_departements

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    # The fake pipeline is synthetic: its name is not a catalogue key.
    host_test_datasets("fake")


def _unit_runner(*, hooks=()):
    """Keep required hook identities while doubling their external SQL work."""
    from app.etl.hooks import DependentMatviewHook, ValidationHook

    required = [ValidationHook(), DependentMatviewHook()]
    for hook in required:
        hook.on_run_complete = MagicMock()
    return PipelineRunner(hooks=[*required, *hooks])


class _PublicationDouble:
    """Exercise the callback ordering without opening a publication connection."""

    def __init__(self, _dsn, _pipeline):
        self.row_count = 100
        self.active = False
        self.staged_publication = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def observe_stock(self):
        return self.row_count

    @contextmanager
    def required_work(self):
        """Model callback ownership only; integration tests prove DB atomicity."""
        assert not self.active
        self.active = True
        try:
            yield self
            assert self.staged_publication
        except BaseException:
            self.staged_publication = False
            raise
        finally:
            self.active = False

    def publish(self, update_metadata):
        assert self.active
        update_metadata(object())
        self.staged_publication = True


@pytest.fixture(autouse=True)
def _skip_runner_db_gates(monkeypatch, request, tmp_path):
    """Keep unit runner tests DB-free."""
    if request.node.cls and request.node.cls.__name__ == "TestRunnerDepartmentScope":
        return
    monkeypatch.setattr("app.etl.runner.settings.etl_temp_dir", str(tmp_path))
    monkeypatch.setattr(
        PipelineRunner,
        "_count_staging_rows",
        staticmethod(lambda _dsn, pipeline: pipeline.staged_rows),
    )
    monkeypatch.setattr(
        "app.etl.runner.dataset_execution_lock", lambda *_: nullcontext()
    )
    monkeypatch.setattr("app.etl.runner.requires_fresh_start", lambda *_: False)
    monkeypatch.setattr("app.etl.runner.Publication", _PublicationDouble)
    monkeypatch.setattr(
        PipelineRunner,
        "_check_preflight_disk",
        staticmethod(lambda _pipeline, _dsn, _log: None),
    )
    monkeypatch.setattr(
        PipelineRunner,
        "_staging_table_exists",
        staticmethod(lambda _dsn, _staging_table: False),
    )
    monkeypatch.setattr(
        PipelineRunner,
        "_check_row_regression",
        staticmethod(lambda _dsn, _pipeline, _new_row_count, _log: (None, None)),
    )
    monkeypatch.setattr(
        PipelineRunner,
        "_check_department_coverage",
        staticmethod(lambda _dsn, _pipeline, _log: None),
    )
    monkeypatch.setattr(
        PipelineRunner,
        "_check_schema_drift",
        staticmethod(lambda _pipeline, _dsn, _log: None),
    )


# ---------------------------------------------------------------------------
# Concrete test pipeline
# ---------------------------------------------------------------------------


class FakePipeline(BasePipeline):
    """Minimal concrete pipeline for testing."""

    def __init__(self, source_dir: Path | None = None):
        self.source_dir = source_dir
        self.staged_rows = 0

    def source_file(self, name: str) -> Path:
        assert self.source_dir is not None, "Runner fixtures require a source directory"
        source = self.source_dir / name
        source.write_bytes(b"id,name,code_departement\n1,synthetic,44\n")
        return source

    @property
    def dataset_name(self) -> str:
        return "fake"

    @property
    def expected_columns(self) -> list[str]:
        return ["id", "name", "code_departement"]

    @property
    def staging_table_name(self) -> str:
        return "fake_staging"

    @property
    def production_table_name(self) -> str:
        return "fake_data"

    @property
    def expected_file_count(self) -> int:
        return 0  # single-file pipeline

    @property
    def min_staging_rows(self) -> int:
        # Explicit opt-out of GATE-01 (the BasePipeline default now applies a
        # fallback floor); the gate itself is tested in tests/etl/test_runner.py.
        return 0

    def download(self) -> list[Path]:
        return [self.source_file("fake.csv")]

    def validate_schema(self, file_path: Path) -> None:
        pass  # always valid

    def transform(self, file_path: Path) -> Path:
        return file_path

    def stage(self, file_path: Path) -> int:
        self.staged_rows += 100
        return 100

    def swap(self) -> None:
        pass


class FailingPipeline(FakePipeline):
    """Pipeline that fails during download."""

    def download(self) -> list[Path]:
        raise RuntimeError("Download failed")


class FailingSwapPipeline(FakePipeline):
    """Pipeline that fails during swap."""

    def swap(self) -> None:
        raise RuntimeError("Swap failed")


class ThresholdPipeline(FakePipeline):
    """Pipeline with expected file count for threshold testing."""

    @property
    def expected_file_count(self) -> int:
        return 10

    def download(self) -> list[Path]:
        return [self.source_file(f"dept_{i}.csv") for i in range(5)]

    def validate_schema(self, file_path: Path) -> None:
        # Fail on some files to test threshold
        if "dept_4" in str(file_path):
            raise ValueError("bad schema")


class ScopedFakePipeline(FakePipeline):
    """Fake departmental pipeline: reads its departements from the bound scope."""

    @property
    def supports_department_scope(self) -> bool:
        return True


def _bound(pipeline, *departements):
    """Bind the instance scope {fake} x *departements*, as the runner does."""
    pipeline.bind_scope(InstanceScope(frozenset({"fake"}), departements))
    return pipeline


# ===========================================================================
# 1. BasePipeline properties
# ===========================================================================


class TestBasePipeline:
    def test_departements_count(self):
        assert len(DEPARTEMENTS) == 102

    def test_departements_includes_corse(self):
        assert "2A" in DEPARTEMENTS
        assert "2B" in DEPARTEMENTS

    def test_departements_includes_dom(self):
        assert "971" in DEPARTEMENTS  # Guadeloupe
        assert "976" in DEPARTEMENTS  # Mayotte

    def test_pipeline_properties(self):
        p = FakePipeline()
        assert p.dataset_name == "fake"
        assert p.staging_table_name == "fake_staging"
        assert p.production_table_name == "fake_data"
        assert p.expected_columns == ["id", "name", "code_departement"]
        assert p.flat_staging_table_name is None

    def test_get_sync_dsn(self):
        dsn = FakePipeline.get_sync_dsn()
        assert dsn.startswith("postgresql://")
        assert "asyncpg" not in dsn

    def test_min_success_ratio(self):
        p = FakePipeline()
        ratio = p.min_success_ratio
        assert 0.0 < ratio <= 1.0

    def test_parse_departements_trims_uppercases_and_deduplicates(self):
        # Same input as the removed environment parser; the scope parser
        # returns the canonical order instead of the typed order.
        assert parse_departements("44,85, 2a,971,44") == ["2A", "44", "85", "971"]

    def test_parse_departements_rejects_invalid(self):
        with pytest.raises(ValueError, match="Unknown departement codes"):
            parse_departements("44,999")

    def test_unsupported_pipeline_ignores_department_scope(self):
        p = _bound(FakePipeline(), "44", "85")

        assert p.is_department_scope_limited is False
        assert p.partition_department_scope == DEPARTEMENTS
        assert p.scoped_min_rows(1_020) == 1_020

    def test_supported_pipeline_uses_department_scope(self):
        p = _bound(ScopedFakePipeline(), "44", "85")

        assert p.is_department_scope_limited is True
        assert p.department_scope == ["44", "85"]
        assert p.partition_department_scope == ["44", "85"]
        assert p.checkpoint_scope_key == "44,85"
        assert p.scoped_min_rows(1_020) == 20


# ===========================================================================
# 2. PipelineRunner — success path
# ===========================================================================


class TestRunnerSuccess:
    @patch("app.etl.runner.PipelineRunner._drop_published_old_tables", new=MagicMock())
    @patch("app.etl.runner.PipelineRunner._insert_etl_run", return_value=None)
    @patch("app.etl.runner.PipelineRunner._update_etl_run")
    @patch("app.etl.runner.PipelineRunner._update_dataset_metadata")
    @patch("app.etl.runner.cleanup_download_dir")
    @patch("app.etl.runner.check_disk_space")
    @patch("app.etl.runner.advisory_lock")
    def test_run_success(
        self,
        mock_lock,
        mock_disk,
        mock_cleanup,
        mock_meta,
        mock_update,
        mock_insert,
        tmp_path,
    ):
        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)
        p = FakePipeline(tmp_path)
        # Mock the DB operations in pipeline
        p.create_staging_table = MagicMock()
        p.drop_staging_table = MagicMock()

        runner = _unit_runner()
        result = runner.run(p)

        assert result.success
        assert result.dataset_name == "fake"
        assert result.total_rows_loaded == 100
        assert len(result.stages) > 0

    @patch("app.etl.runner.PipelineRunner._insert_etl_run", return_value=None)
    @patch("app.etl.runner.PipelineRunner._update_etl_run")
    @patch("app.etl.runner.check_disk_space")
    def test_dry_run(self, mock_disk, mock_update, mock_insert, tmp_path):
        p = FakePipeline(tmp_path)
        runner = _unit_runner()
        result = runner.run(p, dry_run=True)

        assert result.success
        assert result.total_rows_loaded == 0  # no staging/swap in dry run


# ===========================================================================
# 3. PipelineRunner — failure paths
# ===========================================================================


class TestRunnerFailure:
    @patch("app.etl.runner.PipelineRunner._insert_etl_run", return_value=None)
    @patch("app.etl.runner.PipelineRunner._update_etl_run")
    @patch("app.etl.runner.check_disk_space")
    def test_download_failure(self, mock_disk, mock_update, mock_insert):
        p = FailingPipeline()
        p.create_staging_table = MagicMock()
        p.drop_staging_table = MagicMock()

        runner = _unit_runner()
        result = runner.run(p)

        assert not result.success
        assert "Download failed" in result.error

    @patch("app.etl.runner.PipelineRunner._insert_etl_run", return_value=None)
    @patch("app.etl.runner.PipelineRunner._update_etl_run")
    @patch("app.etl.runner.PipelineRunner._update_dataset_metadata")
    @patch("app.etl.runner.cleanup_download_dir")
    @patch("app.etl.runner.check_disk_space")
    @patch("app.etl.runner.advisory_lock")
    def test_swap_failure(
        self,
        mock_lock,
        mock_disk,
        mock_cleanup,
        mock_meta,
        mock_update,
        mock_insert,
        tmp_path,
    ):
        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)
        p = FailingSwapPipeline(tmp_path)
        p.create_staging_table = MagicMock()
        p.drop_staging_table = MagicMock()

        runner = _unit_runner()
        result = runner.run(p)

        assert not result.success
        assert "Swap failed" in result.error

    @patch("app.etl.runner.check_disk_space", side_effect=RuntimeError("No space"))
    def test_disk_space_failure(self, mock_disk):
        p = FakePipeline()
        runner = _unit_runner()
        result = runner.run(p)

        assert not result.success
        assert "No space" in result.error


# ===========================================================================
# 4. PipelineRunner — success threshold
# ===========================================================================


class TestRunnerThreshold:
    @patch("app.etl.runner.PipelineRunner._insert_etl_run", return_value=None)
    @patch("app.etl.runner.PipelineRunner._update_etl_run")
    @patch("app.etl.runner.check_disk_space")
    def test_below_threshold_aborts_swap(
        self, mock_disk, mock_update, mock_insert, tmp_path
    ):
        """When success ratio < threshold, swap is aborted."""
        p = ThresholdPipeline(tmp_path)
        p.create_staging_table = MagicMock()
        p.drop_staging_table = MagicMock()
        p.swap = MagicMock()

        runner = _unit_runner()
        result = runner.run(p)

        # 4/10 succeeded (dept_4 fails validation), but threshold is 90%
        # 4/10 = 40% < 90% -> should fail
        assert not result.success
        assert "below threshold" in result.error.lower()
        p.swap.assert_not_called()


# ===========================================================================
# 5. PipelineRunner — department scope gates
# ===========================================================================


class TestRunnerDepartmentScope:
    def _mock_conn_fetchalls(self, fetchall_results):
        conn = MagicMock()
        conn.execute.return_value.fetchall.side_effect = fetchall_results
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=conn)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_scoped_department_coverage_passes(self):
        p = _bound(ScopedFakePipeline(), "44", "85")

        with patch(
            "app.etl.runner.psycopg.connect",
            return_value=self._mock_conn_fetchalls([[], [("44", 1), ("85", 1)]]),
        ):
            result = PipelineRunner._check_department_coverage(
                "postgresql://test",
                p,
                MagicMock(),
            )

        assert result is None

    def test_scoped_department_coverage_rejects_missing(self):
        p = _bound(ScopedFakePipeline(), "44", "85")

        with patch(
            "app.etl.runner.psycopg.connect",
            return_value=self._mock_conn_fetchalls([[], [("44", 1)]]),
        ):
            result = PipelineRunner._check_department_coverage(
                "postgresql://test",
                p,
                MagicMock(),
            )

        assert result is not None
        assert "85" in result

    def test_full_scope_refuses_missing_departements(self):
        # Full scope too: an expected departement missing from staging
        # refuses the swap (known absences are declared by the pipeline);
        # codes outside the 102 are refused as well.
        p = _bound(ScopedFakePipeline(), *DEPARTEMENTS)
        log = MagicMock()
        with patch(
            "app.etl.runner.psycopg.connect",
            return_value=self._mock_conn_fetchalls([[], [("44", 1)]]),
        ):
            result = PipelineRunner._check_department_coverage("dsn", p, log)
        assert result.startswith("Department coverage: missing scoped departments")
        assert "'85'" in result and "'975'" in result
        with patch(
            "app.etl.runner.psycopg.connect",
            return_value=self._mock_conn_fetchalls([[("00",)]]),
        ):
            result = PipelineRunner._check_department_coverage("dsn", p, log)
        assert "outside scoped ETL ['00']" in result

    def test_scoped_department_coverage_rejects_unexpected(self):
        p = _bound(ScopedFakePipeline(), "44")

        with patch(
            "app.etl.runner.psycopg.connect",
            return_value=self._mock_conn_fetchalls([[("75",)]]),
        ):
            result = PipelineRunner._check_department_coverage(
                "postgresql://test",
                p,
                MagicMock(),
            )

        assert result is not None
        assert "outside scoped ETL" in result
        assert "75" in result

    def test_scoped_row_regression_uses_production_scope(self):
        p = _bound(ScopedFakePipeline(), "44")

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (100,)
        with patch("app.etl.runner.psycopg.connect") as mock_connect:
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            result, prev = PipelineRunner._check_row_regression(
                "postgresql://test",
                p,
                90,
                MagicMock(),
            )

        assert result is None
        assert prev == 100
        sql = conn.execute.call_args.args[0]
        assert "WHERE code_departement = ANY" in sql

    def test_scoped_row_regression_blocks_large_drop(self):
        p = _bound(ScopedFakePipeline(), "44")

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (100,)
        with patch("app.etl.runner.psycopg.connect") as mock_connect:
            mock_connect.return_value.__enter__ = MagicMock(return_value=conn)
            mock_connect.return_value.__exit__ = MagicMock(return_value=False)
            result, prev = PipelineRunner._check_row_regression(
                "postgresql://test",
                p,
                70,
                MagicMock(),
            )

        assert result is not None
        assert "swap aborted" in result
        assert prev == 100


# ===========================================================================
# 6. PipelineRunner — hooks
# ===========================================================================


class TestRunnerHooks:
    @patch("app.etl.runner.PipelineRunner._drop_published_old_tables", new=MagicMock())
    @patch("app.etl.runner.PipelineRunner._insert_etl_run", return_value=None)
    @patch("app.etl.runner.PipelineRunner._update_etl_run")
    @patch("app.etl.runner.PipelineRunner._update_dataset_metadata")
    @patch("app.etl.runner.cleanup_download_dir")
    @patch("app.etl.runner.check_disk_space")
    @patch("app.etl.runner.advisory_lock")
    def test_hooks_called_on_success(
        self,
        mock_lock,
        mock_disk,
        mock_cleanup,
        mock_meta,
        mock_update,
        mock_insert,
        tmp_path,
    ):
        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)
        hook = MagicMock()
        p = FakePipeline(tmp_path)
        p.create_staging_table = MagicMock()
        p.drop_staging_table = MagicMock()

        runner = _unit_runner(hooks=[hook])
        result = runner.run(p)

        assert result.success
        hook.on_run_start.assert_called_once()
        hook.on_run_complete.assert_called_once()

    @patch("app.etl.runner.PipelineRunner._insert_etl_run", return_value=None)
    @patch("app.etl.runner.PipelineRunner._update_etl_run")
    @patch("app.etl.runner.check_disk_space")
    def test_hooks_called_on_failure(self, mock_disk, mock_update, mock_insert):
        hook = MagicMock()
        p = FailingPipeline()
        p.create_staging_table = MagicMock()
        p.drop_staging_table = MagicMock()

        runner = _unit_runner(hooks=[hook])
        result = runner.run(p)

        assert not result.success
        hook.on_run_start.assert_called_once()
        hook.on_run_failure.assert_called_once()


# ===========================================================================
# 7. Download utilities
# ===========================================================================


class TestDownloadUtils:
    def test_check_disk_space_ok(self, tmp_path):
        # tmp_path always has some free space
        check_disk_space(tmp_path, min_gb=0)

    def test_check_disk_space_fail(self, tmp_path):
        with pytest.raises(RuntimeError, match="Insufficient"):
            check_disk_space(tmp_path, min_gb=999999)

    def test_get_download_dir(self, tmp_path):
        with patch("app.etl.download.settings") as mock_settings:
            mock_settings.etl_temp_dir = str(tmp_path)
            d = get_download_dir("test_ds")
        assert d.exists()
        assert "datacommune_test_ds" in str(d)

    def test_cleanup_download_dir(self, tmp_path):
        with patch("app.etl.download.settings") as mock_settings:
            mock_settings.etl_temp_dir = str(tmp_path)
            d = tmp_path / "datacommune_cleanup_test"
            d.mkdir()
            (d / "file.csv").write_text("data")

            cleanup_download_dir("cleanup_test")
            assert not d.exists()


# ===========================================================================
# 8. Gzip integrity verification
# ===========================================================================


class TestGzipIntegrity:
    def test_valid_gzip(self, tmp_path):
        gz_file = tmp_path / "valid.gz"
        with gzip.open(gz_file, "wb") as f:
            f.write(b"hello world")
        validate_gzip_integrity(gz_file)  # should not raise

    def test_invalid_magic(self, tmp_path):
        bad_file = tmp_path / "bad.gz"
        bad_file.write_bytes(b"not a gzip file")
        with pytest.raises(IntegrityError, match="Not a valid gzip"):
            validate_gzip_integrity(bad_file)

    def test_truncated_gzip(self, tmp_path):
        gz_file = tmp_path / "truncated.gz"
        # Write valid gzip header but truncate the data
        with gzip.open(gz_file, "wb") as f:
            f.write(b"x" * 100)
        # Truncate the file
        data = gz_file.read_bytes()
        gz_file.write_bytes(data[:10])
        with pytest.raises(IntegrityError):
            validate_gzip_integrity(gz_file)

    def test_nonexistent_file(self, tmp_path):
        with pytest.raises(IntegrityError, match="Cannot read"):
            validate_gzip_integrity(tmp_path / "missing.gz")


# ===========================================================================
# 9. EtlResult schema
# ===========================================================================


class TestEtlSchemas:
    def test_etl_result_success(self):
        r = EtlResult(
            dataset_name="test",
            stages=[],
            total_rows_loaded=1000,
            total_duration_seconds=5.0,
            success=True,
        )
        assert r.success
        assert r.error is None

    def test_etl_result_failure(self):
        r = EtlResult(
            dataset_name="test",
            stages=[],
            total_rows_loaded=0,
            total_duration_seconds=1.0,
            success=False,
            error="boom",
        )
        assert not r.success
        assert r.error == "boom"


# ===========================================================================
# 10. Scheduler jobs (unit-level)
# ===========================================================================


class TestSchedulerJobs:
    @patch("app.scheduler.jobs.psycopg")
    def test_cleanup_api_request_log(self, mock_psycopg):
        from app.scheduler.jobs import cleanup_api_request_log

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 42
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_psycopg.connect.return_value = mock_conn

        cleanup_api_request_log()
        # First statement: the log itself; the same pass then purges the error reports.
        assert mock_conn.execute.call_args_list[0].args == (
            "DELETE FROM api_request_log WHERE ts < NOW() - INTERVAL '7 days'",
        )
        assert "signalements" in mock_conn.execute.call_args_list[-1].args[0]

    @patch("app.etl.publication.psycopg")
    def test_refresh_commune_synthese(self, mock_psycopg):
        from app.scheduler.jobs import refresh_commune_synthese_view

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_psycopg.connect.return_value = mock_conn

        refresh_commune_synthese_view()
        mock_conn.execute.assert_called()

    @patch("app.etl.publication.psycopg")
    def test_refresh_communes_lookup(self, mock_psycopg):
        from app.scheduler.jobs import refresh_communes_lookup

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_psycopg.connect.return_value = mock_conn

        refresh_communes_lookup()
        mock_conn.execute.assert_called()
