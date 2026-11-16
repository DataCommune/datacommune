"""Unit tests for CheckpointManager.

Covers:
  - Fresh start: load() returns None, get_loaded_files() returns empty set
  - Save / load roundtrip preserves loaded_files
  - Staging table mismatch invalidates checkpoint (returns None)
  - Corrupt JSON does not raise, returns None
  - clear() removes the checkpoint file
"""

import json
from pathlib import Path

import pytest

from app.etl.checkpoint import CheckpointManager


def save_checkpoint(checkpoint, files, **kwargs):
    """Small explicit source evidence for checkpoint serialization tests."""
    checkpoint.save(
        files,
        identity_key="test-generation",
        source_files=[
            {"id": name, "bytes": 1, "sha256": "a" * 64} for name in sorted(set(files))
        ],
        staging_rows=len(files),
        **kwargs,
    )


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    """Return a CheckpointManager backed by a temp directory."""
    monkeypatch.setattr(
        "app.etl.checkpoint.settings",
        type("S", (), {"etl_temp_dir": str(tmp_path)})(),
    )
    return CheckpointManager("test_ds", "test_staging")


class TestFreshStart:
    def test_load_returns_none(self, checkpoint):
        assert checkpoint.load() is None

    def test_exists_false(self, checkpoint):
        assert not checkpoint.exists()

    def test_get_loaded_files_empty(self, checkpoint):
        assert checkpoint.get_loaded_files() == set()


class TestSaveLoad:
    def test_roundtrip_preserves_files(self, checkpoint):
        save_checkpoint(checkpoint, ["b.csv", "a.csv"])
        assert checkpoint.get_loaded_files() == {"a.csv", "b.csv"}

    def test_load_returns_dict_with_correct_fields(self, checkpoint):
        save_checkpoint(checkpoint, ["a.csv"])
        data = checkpoint.load()
        assert data is not None
        assert data["dataset_name"] == "test_ds"
        assert data["staging_table"] == "test_staging"
        assert data["loaded_files"] == ["a.csv"]

    def test_save_deduplicates(self, checkpoint):
        save_checkpoint(checkpoint, ["a.csv", "a.csv", "b.csv"])
        assert checkpoint.get_loaded_files() == {"a.csv", "b.csv"}

    def test_exists_after_save(self, checkpoint):
        save_checkpoint(checkpoint, ["x.csv"])
        assert checkpoint.exists()

    def test_save_is_atomic_tmp_replaced(self, checkpoint):
        """Verify tmp file does not remain after save."""
        save_checkpoint(checkpoint, ["a.csv"])
        tmp = checkpoint.path.with_suffix(".json.tmp")
        assert not tmp.exists()


class TestMismatch:
    def test_staging_table_mismatch_returns_none(
        self, checkpoint, tmp_path, monkeypatch
    ):
        # Save with original staging table name
        save_checkpoint(checkpoint, ["a.csv"])

        # Create a manager with a different staging table name
        monkeypatch.setattr(
            "app.etl.checkpoint.settings",
            type("S", (), {"etl_temp_dir": str(tmp_path)})(),
        )
        m2 = CheckpointManager("test_ds", "OTHER_staging")
        assert m2.load() is None

    def test_staging_table_mismatch_get_loaded_files_empty(
        self, checkpoint, tmp_path, monkeypatch
    ):
        save_checkpoint(checkpoint, ["a.csv"])
        monkeypatch.setattr(
            "app.etl.checkpoint.settings",
            type("S", (), {"etl_temp_dir": str(tmp_path)})(),
        )
        m2 = CheckpointManager("test_ds", "OTHER_staging")
        assert m2.get_loaded_files() == set()


class TestCorruptJson:
    def test_corrupt_json_returns_none(self, checkpoint):
        checkpoint._dir.mkdir(parents=True, exist_ok=True)
        checkpoint.path.write_text("not valid json {{{", encoding="utf-8")
        assert checkpoint.load() is None

    def test_corrupt_json_get_loaded_files_empty(self, checkpoint):
        checkpoint._dir.mkdir(parents=True, exist_ok=True)
        checkpoint.path.write_text("{broken", encoding="utf-8")
        assert checkpoint.get_loaded_files() == set()

    def test_empty_file_returns_none(self, checkpoint):
        checkpoint._dir.mkdir(parents=True, exist_ok=True)
        checkpoint.path.write_text("", encoding="utf-8")
        assert checkpoint.load() is None


class TestClear:
    def test_clear_removes_file(self, checkpoint):
        save_checkpoint(checkpoint, ["a.csv"])
        assert checkpoint.exists()
        checkpoint.clear()
        assert not checkpoint.exists()

    def test_clear_when_absent_does_not_raise(self, checkpoint):
        # Should be safe to call even if file was never created
        checkpoint.clear()  # No exception expected

    def test_load_after_clear_returns_none(self, checkpoint):
        save_checkpoint(checkpoint, ["a.csv"])
        checkpoint.clear()
        assert checkpoint.load() is None


def test_failed_atomic_replace_preserves_committed_evidence(checkpoint, monkeypatch):
    save_checkpoint(checkpoint, ["a.csv"])
    before = checkpoint.path.read_bytes()

    def reject_replace(*args):
        raise OSError("simulated checkpoint disk failure")

    monkeypatch.setattr(Path, "replace", reject_replace)
    with pytest.raises(OSError, match="disk failure"):
        save_checkpoint(checkpoint, ["a.csv", "b.csv"])
    assert checkpoint.path.read_bytes() == before
    assert checkpoint.get_loaded_files() == {"a.csv"}
    assert not checkpoint.path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 1),
        ("version", 99),
        ("staging_rows", -1),
        ("staging_rows", True),
        ("source_files", [{"id": "../escape", "bytes": 1, "sha256": "a" * 64}]),
        ("loaded_files", ["unknown.csv"]),
        ("pending", {"operation": "stage", "file_id": "unknown.csv"}),
    ],
)
def test_untrusted_checkpoint_fields_are_refused(checkpoint, field, value):
    save_checkpoint(checkpoint, ["a.csv"])
    data = json.loads(checkpoint.path.read_bytes())
    data[field] = value
    checkpoint.path.write_bytes(json.dumps(data).encode())
    assert checkpoint.load() is None
    assert checkpoint.get_loaded_files() == set()
