"""COR-02: validation-only runs must not mutate publication or resume state."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from app.etl.runner import PipelineRunner
from tests.etl.test_runner import FakePipeline, _patch_all

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    # FakePipeline is synthetic: its name is not a catalogue key.
    host_test_datasets("test_ds")


@pytest.mark.parametrize("failure", [None, "download", "validate", "disk"])
@pytest.mark.parametrize("expected_files", [0, 1])
@pytest.mark.parametrize(
    ("staging", "checkpoint"),
    [
        (True, None),
        (False, {"loaded_files": ["source.csv"]}),
        (True, {"loaded_files": ["source.csv"], "scope_key": "obsolete"}),
    ],
)
def test_dry_run_never_mutates_or_resumes(staging, checkpoint, failure, expected_files):
    pipeline = FakePipeline()
    pipeline._files = [Path("source.csv")]
    pipeline.download = MagicMock(return_value=pipeline._files)
    pipeline.validate_schema = MagicMock()
    pipeline.drop_staging_table = MagicMock()
    pipeline.create_staging_table = MagicMock()
    pipeline.transform = MagicMock()
    pipeline.stage = MagicMock()
    pipeline.swap = MagicMock()
    hook = MagicMock()
    if failure == "download":
        pipeline.download.side_effect = RuntimeError("synthetic download failure")
    if failure == "validate":
        pipeline.validate_schema.side_effect = RuntimeError("synthetic schema failure")
    with (
        _patch_all(staging_exists=staging, checkpoint_data=checkpoint) as mocks,
        patch.object(
            FakePipeline,
            "expected_file_count",
            new_callable=PropertyMock,
            return_value=expected_files,
        ),
        patch.object(
            FakePipeline,
            "min_success_ratio",
            new_callable=PropertyMock,
            return_value=1.0,
        ),
        patch("app.etl.runner.psycopg.connect") as connect,
    ):
        if failure == "disk":
            mocks["check_disk_space"].side_effect = RuntimeError("synthetic full disk")
        result = PipelineRunner([hook]).run(pipeline, dry_run=True)
    assert result.success is (failure is None)
    assert result.total_rows_loaded == 0
    if failure:
        assert result.error
    for name, spy in mocks.items():
        if name not in {"check_disk_space", "advisory_lock"}:
            spy.assert_not_called()
    connect.assert_not_called()
    assert hook.mock_calls == []
    for name in (
        "drop_staging_table",
        "create_staging_table",
        "transform",
        "stage",
        "swap",
    ):
        getattr(pipeline, name).assert_not_called()
    if failure not in {"disk", "download"}:
        pipeline.validate_schema.assert_called_once_with(Path("source.csv"))
