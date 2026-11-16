"""COR-20 regression proofs with the actual orchestrator and isolated I/O."""

from unittest.mock import Mock

import pytest

from app.etl.runner import PipelineRunner
from tests.etl.test_runner import FakePipeline, _patch_all


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    # FakePipeline is synthetic: its name is not a catalogue key.
    host_test_datasets("test_ds")


def test_required_failure_does_not_advance_success_metadata():
    hook = Mock(required_on_complete=True)
    hook.on_run_complete.side_effect = RuntimeError("synthetic required failure")
    pipeline = FakePipeline()
    with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
        result = PipelineRunner(hooks=[hook]).run(pipeline)
        assert not result.success
        mocks["update_metadata"].assert_not_called()


def test_optional_hooks_observe_only_final_publication_result():
    events = []
    required = Mock(required_on_complete=True)
    required.on_run_complete.side_effect = RuntimeError("synthetic required failure")
    optional = Mock(required_on_complete=False)
    optional.on_run_complete.side_effect = lambda _name, result: events.append(
        result.success
    )
    pipeline = FakePipeline()
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = PipelineRunner(hooks=[optional, required]).run(pipeline)
    assert not result.success
    assert True not in events


def test_no_required_checks_is_fail_closed():
    with pytest.raises(RuntimeError, match="No required publication"):
        PipelineRunner()._require_publication_checks()


def test_incomplete_publication_discards_stale_checkpoint(tmp_path):
    pipeline = FakePipeline()
    source = tmp_path / "already-staged.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    pipeline._files = [source]
    pipeline.drop_staging_table = Mock()
    pipeline.stage = Mock(return_value=10)
    checkpoint = {"loaded_files": ["already-staged.csv"], "scope_key": ""}
    with _patch_all(staging_exists=True, checkpoint_data=checkpoint) as mocks:
        mocks["requires_fresh_start"].return_value = True
        assert PipelineRunner().run(pipeline).success
        pipeline.drop_staging_table.assert_called_once()
        pipeline.stage.assert_called_once()


def test_arbitrary_required_hook_cannot_replace_validation_and_view_checks():
    from app.etl.hooks import DependentMatviewHook, ValidationHook

    for hooks in (
        [Mock(required_on_complete=True)],
        [ValidationHook()],
        [DependentMatviewHook()],
    ):
        with pytest.raises(RuntimeError, match="No required publication"):
            PipelineRunner(hooks)._require_publication_checks()
    PipelineRunner(
        [ValidationHook(), DependentMatviewHook()]
    )._require_publication_checks()


def test_required_hook_keeps_private_transient_cause_for_retry():
    import httpx

    from app.etl.errors import transient_cause

    failure = httpx.ReadTimeout("synthetic private timeout")
    hook = Mock(required_on_complete=True)
    hook.on_run_complete.side_effect = failure
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = PipelineRunner([hook]).run(FakePipeline())
    assert not result.success
    assert transient_cause(result.failure_cause) is failure
    assert "synthetic private timeout" not in result.model_dump_json()
