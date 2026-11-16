"""Resume oracles with real checkpoint files and forbidden DB connections."""

import json
from collections import Counter
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from app.config import settings
from app.etl.checkpoint import CheckpointManager
from app.etl.runner import PipelineRunner
from app.scope import InstanceScope
from tests.etl.test_runner import FakePipeline, _patch_all

pytestmark = pytest.mark.unit
REAL_LOAD = CheckpointManager.load
REAL_SAVE = CheckpointManager.save
REAL_CLEAR = CheckpointManager.clear


@pytest.fixture(autouse=True)
def offline_only(monkeypatch, host_test_datasets, tmp_path):
    host_test_datasets("test_ds")
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "source_archive_dir", "")


class MemoryPipeline(FakePipeline):
    def __init__(self, files):
        super().__init__()
        self._files = files
        self.rows = []
        self.creations = 0
        self.bind_scope(InstanceScope(frozenset(["test_ds"]), ("44", "85")))

    def create_staging_table(self, dsn):
        self.rows.clear()
        self.creations += 1

    def stage(self, path):
        self.rows.extend([self.checkpoint_file_id(path)] * 10)
        return 10

    def drop_staging_table(self, dsn):
        self.rows.clear()


def prepared(tmp_path, paths=("a.csv", "b.csv")):
    files = [tmp_path / name for name in paths]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"id\n1\n")
    return MemoryPipeline(files)


@contextmanager
def run_context(pipeline):
    with (
        _patch_all(staging_exists=bool(pipeline.rows), checkpoint_data=None),
        patch.object(CheckpointManager, "load", REAL_LOAD),
        patch.object(CheckpointManager, "save", REAL_SAVE),
        patch.object(CheckpointManager, "clear", REAL_CLEAR),
        patch.object(
            PipelineRunner,
            "_count_staging_rows",
            side_effect=lambda dsn, p: len(p.rows),
        ),
    ):
        yield


def save_committed(pipeline, loaded_files):
    key = pipeline.checkpoint_scope_key
    CheckpointManager("test_ds", pipeline.staging_table_name).save(
        loaded_files,
        scope_key=key,
        identity_key=pipeline.checkpoint_identity(key),
        source_files=PipelineRunner._fingerprint_sources(pipeline, pipeline._files),
        staging_rows=len(pipeline.rows),
    )


def test_etl01_resumed_stock_satisfies_full_import_minimum(tmp_path):
    pipeline = prepared(tmp_path)
    with patch.object(type(pipeline), "min_staging_rows", 15):
        pipeline.stage(pipeline._files[0])
        save_committed(pipeline, ["a.csv"])
        with run_context(pipeline):
            result = PipelineRunner().run(pipeline)
    assert result.success, result.error
    assert result.total_rows_loaded == len(pipeline.rows) == 20
    assert pipeline.creations == 0


def test_etl02_both_dvf_years_survive_resume(tmp_path):
    from app.etl_dvf.pipeline import DvfPipeline

    pipeline = prepared(tmp_path, ("2024/44.csv", "2025/44.csv"))
    pipeline.checkpoint_file_id = DvfPipeline().checkpoint_file_id
    pipeline.stage(pipeline._files[0])
    save_committed(pipeline, ["2024/44.csv"])
    with run_context(pipeline):
        result = PipelineRunner().run(pipeline)
    assert result.success, result.error
    assert Counter(pipeline.rows) == Counter({"2024/44.csv": 10, "2025/44.csv": 10})
    assert pipeline.creations == 0


@pytest.mark.parametrize("dataset", ["loyers", "vacance"])
def test_etl03_frozen_scope_expansion_changes_identity(dataset, tmp_path):
    from app.etl_loyers.pipeline import LoyersPipeline
    from app.etl_vacance.pipeline import VacancePipeline
    from tests.test_frozen_source_manifest import manifest_file

    path, _ = manifest_file(tmp_path, dataset)
    cls = LoyersPipeline if dataset == "loyers" else VacancePipeline
    pipeline = cls.from_manifest(path)
    try:
        pipeline.bind_scope(InstanceScope(frozenset([dataset]), ("44",)))
        previous = pipeline.checkpoint_identity(pipeline.checkpoint_scope_key)
        pipeline.bind_scope(InstanceScope(frozenset([dataset]), ("44", "85")))
        assert pipeline.checkpoint_identity(pipeline.checkpoint_scope_key) != previous
    finally:
        pipeline.close()


def test_etl03_current_loyers_scope_expansion_changes_identity():
    from app.etl_loyers.pipeline import LoyersPipeline

    pipeline = LoyersPipeline()
    pipeline.bind_scope(InstanceScope(frozenset(["loyers"]), ("44",)))
    previous = pipeline.checkpoint_identity(pipeline.checkpoint_scope_key)
    pipeline.bind_scope(InstanceScope(frozenset(["loyers"]), ("44", "85")))
    assert pipeline.checkpoint_identity(pipeline.checkpoint_scope_key) != previous


def test_etl04_ban_new_vintage_changes_identity():
    from app.etl_ban.pipeline import BanPipeline

    pipeline = BanPipeline()
    pipeline.bind_scope(InstanceScope(frozenset(["ban"]), ("44",)))
    pipeline.pin_millesime("2025-01-01")
    previous = pipeline.checkpoint_identity(pipeline.checkpoint_scope_key)
    pipeline.pin_millesime("2025-02-01")
    assert pipeline.checkpoint_identity(pipeline.checkpoint_scope_key) != previous


def test_etl05_crash_after_commit_reconstructs_uncertain_stock(tmp_path):
    pipeline = prepared(tmp_path)
    pipeline.stage(pipeline._files[0])
    save_committed(pipeline, ["a.csv"])
    original = pipeline.stage

    def committed_then_crash(path):
        assert CheckpointManager("test_ds", pipeline.staging_table_name).load()[
            "pending"
        ] == {"operation": "stage", "file_id": "b.csv"}
        original(path)
        raise KeyboardInterrupt("after durable commit, before checkpoint")

    with (
        run_context(pipeline),
        patch.object(pipeline, "stage", committed_then_crash),
        pytest.raises(KeyboardInterrupt),
    ):
        PipelineRunner().run(pipeline)
    assert len(pipeline.rows) == 20
    with run_context(pipeline):
        result = PipelineRunner().run(pipeline)
    assert result.success, result.error
    assert Counter(pipeline.rows) == Counter({"a.csv": 10, "b.csv": 10})
    assert pipeline.creations == 1


@pytest.mark.parametrize("legacy", [None, 1, 99])
def test_legacy_checkpoint_rebuilds_instead_of_silently_skipping(tmp_path, legacy):
    pipeline = prepared(tmp_path)
    pipeline.rows = ["stale"] * 10
    checkpoint = CheckpointManager("test_ds", pipeline.staging_table_name)
    checkpoint.path.parent.mkdir(parents=True)
    data = {
        "dataset_name": "test_ds",
        "staging_table": "test_ds_staging",
        "scope_key": "",
        "loaded_files": ["a.csv"],
    }
    if legacy is not None:
        data["version"] = legacy
    checkpoint.path.write_bytes(json.dumps(data).encode())
    with run_context(pipeline):
        result = PipelineRunner().run(pipeline)
    assert result.success, result.error
    assert Counter(pipeline.rows) == Counter({"a.csv": 10, "b.csv": 10})
    assert pipeline.creations == 1


def test_changed_source_bytes_with_same_name_rebuilds(tmp_path):
    pipeline = prepared(tmp_path)
    pipeline.stage(pipeline._files[0])
    save_committed(pipeline, ["a.csv"])
    pipeline._files[0].write_bytes(b"id\n2\n")
    with run_context(pipeline):
        result = PipelineRunner().run(pipeline)
    assert result.success, result.error
    assert pipeline.creations == 1
    assert Counter(pipeline.rows) == Counter({"a.csv": 10, "b.csv": 10})


@pytest.mark.parametrize("phase", ["validate_schema", "transform"])
def test_pre_stage_failures_keep_partial_file_tolerance(tmp_path, phase):
    pipeline = prepared(tmp_path)
    original = getattr(pipeline, phase)

    def reject_first(path):
        if path == pipeline._files[0]:
            raise ValueError("invalid source before staging")
        return original(path)

    with (
        patch.object(type(pipeline), "expected_file_count", 2),
        patch.object(type(pipeline), "min_success_ratio", 0.5),
        patch.object(pipeline, phase, reject_first),
        run_context(pipeline),
    ):
        result = PipelineRunner().run(pipeline)
    assert result.success, result.error
    assert Counter(pipeline.rows) == Counter({"b.csv": 10})
    assert result.total_rows_loaded == 10


def test_stage_failure_is_fatal_and_does_not_process_next_file(tmp_path):
    pipeline = prepared(tmp_path)
    attempted = []

    def failing_stage(path):
        attempted.append(path)
        raise ValueError("database write outcome uncertain")

    with patch.object(pipeline, "stage", failing_stage), run_context(pipeline):
        result = PipelineRunner().run(pipeline)
    assert not result.success
    assert attempted == [pipeline._files[0]]
    assert CheckpointManager("test_ds", pipeline.staging_table_name).load()[
        "pending"
    ] == {"operation": "stage", "file_id": "a.csv"}


def test_ambiguous_basenames_are_rejected_before_staging(tmp_path):
    pipeline = prepared(tmp_path, ("one/a.csv", "two/a.csv"))
    with run_context(pipeline):
        result = PipelineRunner().run(pipeline)
    assert not result.success
    assert "duplicate source identity" in result.error
    assert pipeline.rows == []


def test_dvf_file_identity_survives_cache_relocation(tmp_path):
    from app.etl_dvf.pipeline import DvfPipeline

    pipeline = DvfPipeline()
    assert pipeline.checkpoint_file_id(tmp_path / "cacheA/2024/44.csv") == "2024/44.csv"
    assert pipeline.checkpoint_file_id(tmp_path / "cacheB/2024/44.csv") == "2024/44.csv"
    assert pipeline.checkpoint_file_id(tmp_path / "cacheA/2025/44.csv") == "2025/44.csv"
    assert pipeline.checkpoint_file_id(tmp_path / "cacheA/2024/85.csv") == "2024/85.csv"


def test_same_normalized_scope_keeps_ban_identity():
    from app.etl_ban.pipeline import BanPipeline

    pipeline = BanPipeline()
    pipeline.bind_scope(InstanceScope(frozenset(["ban"]), ("44", "85")))
    original = pipeline.checkpoint_identity(pipeline.checkpoint_scope_key)
    pipeline.bind_scope(InstanceScope(frozenset(["ban"]), ("85", "44", "44")))
    assert pipeline.checkpoint_identity(pipeline.checkpoint_scope_key) == original


@pytest.mark.parametrize("point,staged", [("intent", 0), ("completion", 1)])
def test_checkpoint_write_failure_is_fatal_before_next_file(tmp_path, point, staged):
    pipeline = prepared(tmp_path)
    stage = Mock(wraps=pipeline.stage)

    def failing_save(self, files, **kwargs):
        if point == "intent" or kwargs.get("pending") is None:
            raise OSError("checkpoint write failure")
        return REAL_SAVE(self, files, **kwargs)

    with (
        run_context(pipeline),
        patch.object(pipeline, "stage", stage),
        patch.object(CheckpointManager, "save", failing_save),
    ):
        result = PipelineRunner().run(pipeline)
    assert not result.success
    assert "checkpoint write failure" in result.error
    assert stage.call_count == staged
    if staged:
        assert stage.call_args.args == (pipeline._files[0],)
        assert CheckpointManager("test_ds", pipeline.staging_table_name).load()[
            "pending"
        ] == {"operation": "stage", "file_id": "a.csv"}
