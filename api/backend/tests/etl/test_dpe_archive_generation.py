"""Complete DPE archive proofs with synthetic transport and no database."""

import csv
import json
import logging
import os
from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from app.config import settings
from app.etl import source_archive as archive
from app.etl.checkpoint import CheckpointManager
from app.etl.download import cleanup_download_dir, get_download_dir
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_dpe.pipeline import DpePipeline
from app.scope.model import InstanceScope
from tests.test_etl_dpe_sources import install_source_api, split_manifest_fixture

pytestmark = pytest.mark.unit
STARTED = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
RUN_ID = "1-20261005T100000Z"


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    bind_departements(("44", "85"), DpePipeline)


def test_runner_archive_preserves_every_generation_input(monkeypatch, tmp_path):
    calls = install_source_api(monkeypatch, tmp_path / "cache")
    pipeline = DpePipeline()
    try:
        units = pipeline.download()
        manifest = pipeline.import_manifest
        cache = get_download_dir("dpe")
        artifacts = {
            Path(proof.path)
            for entry in manifest.files
            for proof in (entry.metadata_start, entry.metadata_end, *entry.pages)
        }
        expected = {cache / "manifest.json", *units, *artifacts}
        assert len(units) == 4 and len(artifacts) == 16 and len(expected) == 21
        originals = {
            "manifest.json"
            if path == cache / "manifest.json"
            else path.relative_to(
                Path(manifest.directory)
            ).as_posix(): path.read_bytes()
            for path in expected
        }
        root = tmp_path / "archive"
        root.mkdir()
        (root / archive.SENTINEL_NAME).write_text(
            archive.SENTINEL_CONTENT, encoding="utf-8"
        )
        monkeypatch.setattr(settings, "source_archive_dir", str(root))
        monkeypatch.setattr(settings, "source_archive_min_free_bytes", 0)
        # Only this journal lookup is doubled; source selection and archive IO run.
        monkeypatch.setattr(PipelineRunner, "_read_run_started_at", lambda *_: STARTED)
        calls.clear()
        PipelineRunner._archive_sources(
            "postgresql://offline-invalid/unused",
            1,
            pipeline,
            units,
            None,
            logging.getLogger(__name__),
        )
        replay = archive.load_replay(root, "dpe", RUN_ID)
        assert {entry["relpath"] for entry in replay.files} == set(originals)
        restored = replay.materialize(tmp_path / "relocated")
        assert {
            path.relative_to(tmp_path / "relocated").as_posix(): path.read_bytes()
            for path in restored
        } == originals
        assert not calls
    finally:
        pipeline.close()


@pytest.fixture
def generation(monkeypatch, tmp_path):
    calls = install_source_api(monkeypatch, tmp_path / "cache")
    pipeline = DpePipeline()
    units = pipeline.download()
    cache = get_download_dir("dpe")
    manifest = pipeline.import_manifest
    paths = [cache / "manifest.json", *units]
    paths.extend(
        Path(proof.path)
        for entry in manifest.files
        for proof in (entry.metadata_start, entry.metadata_end, *entry.pages)
    )
    originals = {
        "manifest.json"
        if path == cache / "manifest.json"
        else path.relative_to(Path(manifest.directory)).as_posix(): path.read_bytes()
        for path in paths
    }
    root = tmp_path / "archive"
    root.mkdir()
    (root / archive.SENTINEL_NAME).write_text(
        archive.SENTINEL_CONTENT, encoding="utf-8"
    )
    monkeypatch.setattr(settings, "source_archive_dir", str(root))
    monkeypatch.setattr(settings, "source_archive_min_free_bytes", 0)
    monkeypatch.setattr(PipelineRunner, "_read_run_started_at", lambda *_: STARTED)
    PipelineRunner._archive_sources(
        "postgresql://offline-invalid/unused",
        1,
        pipeline,
        units,
        None,
        logging.getLogger(__name__),
    )
    replay = archive.load_replay(root, "dpe", RUN_ID)
    calls.clear()
    monkeypatch.setattr(
        DpePipeline,
        "_request_json",
        lambda *args, **kwargs: pytest.fail("Replay must never request producer HTTP"),
    )
    try:
        yield SimpleNamespace(
            pipeline=pipeline,
            units=units,
            manifest=manifest,
            cache=cache,
            paths=paths,
            originals=originals,
            root=root,
            replay=replay,
            calls=calls,
        )
    finally:
        pipeline.close()


def transformed_rows(pipeline, files):
    rows = Counter()
    for file in files:
        with pipeline.transform(file).open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                rows[tuple(sorted(row.items()))] += 1
    return rows


def logical_manifest(manifest):
    """Keep all evidence fields; normalize only the original generation root."""
    root = Path(manifest.directory)

    def normalize(value, key=None):
        if isinstance(value, dict):
            return {name: normalize(item, name) for name, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if key == "directory":
            return "."
        if key == "path":
            return Path(value).relative_to(root).as_posix()
        return value

    return normalize(asdict(manifest))


def test_purge_relocation_preserves_original_bytes_generation_and_transformation(
    generation, monkeypatch, tmp_path
):
    g = generation
    expected_manifest = logical_manifest(g.manifest)
    expected_rows = transformed_rows(g.pipeline, g.units)
    assert sum(expected_rows.values()) == 8
    identity = g.pipeline.checkpoint_identity(g.pipeline.checkpoint_scope_key)
    g.pipeline.close()
    cleanup_download_dir("dpe")
    assert not g.cache.exists()
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "new-cache"))
    destination = get_download_dir("dpe")
    fresh = DpePipeline()
    try:
        units = fresh.restore_source_inputs(g.replay, destination)
        assert len(units) == 4
        assert all(path.is_relative_to(destination) for path in units)
        restored_root = Path(fresh.import_manifest.directory)
        assert {
            relpath: (restored_root / relpath).read_bytes() for relpath in g.originals
        } == g.originals
        assert logical_manifest(fresh.import_manifest) == expected_manifest
        assert fresh.checkpoint_identity(fresh.checkpoint_scope_key) == identity
        assert transformed_rows(fresh, units) == expected_rows
        checkpoint = CheckpointManager("dpe", fresh.staging_table_name)
        checkpoint.save(
            [units[0].name],
            scope_key=fresh.checkpoint_scope_key,
            identity_key=identity,
            source_files=PipelineRunner._fingerprint_sources(fresh, units),
            staging_rows=2,
        )
        before = checkpoint.path.read_bytes()
        resumed = DpePipeline()
        try:
            restored = resumed.restore_source_inputs(g.replay, destination)
            assert resumed.checkpoint_identity(resumed.checkpoint_scope_key) == identity
            assert (
                PipelineRunner._fingerprint_sources(resumed, restored)
                == checkpoint.load()["source_files"]
            )
            assert checkpoint.path.read_bytes() == before
        finally:
            resumed.close()
        PipelineRunner._archive_sources(
            "postgresql://offline-invalid/unused",
            2,
            fresh,
            units,
            g.replay,
            logging.getLogger(__name__),
        )
        second = archive.load_replay(g.root, "dpe", "2-20261005T100000Z")
        assert {entry["relpath"] for entry in second.files} == set(g.originals)
        fresh.close()
        cleanup_download_dir("dpe")
        assert not destination.exists()
        monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "third-cache"))
        third_root = get_download_dir("dpe")
        third = DpePipeline()
        try:
            third_units = third.restore_source_inputs(second, third_root)
            assert (
                Path(third.import_manifest.directory) / "manifest.json"
            ).read_bytes() == g.originals["manifest.json"]
            assert logical_manifest(third.import_manifest) == expected_manifest
            assert transformed_rows(third, third_units) == expected_rows
        finally:
            third.close()
        assert not g.calls
    finally:
        fresh.close()


def test_real_cli_admits_complete_dpe_archive(generation):
    from run_etl import _load_replay

    replay = _load_replay("dpe", RUN_ID)
    assert replay.record == generation.replay.record
    assert len(replay.files) == 21


def test_partitioned_external_manifest_survives_archive_purge_and_relocation(
    monkeypatch, tmp_path
):
    manifest = split_manifest_fixture(monkeypatch, tmp_path / "cache")
    external = tmp_path / "durable-selected-manifest.json"
    manifest.save(external)
    original_manifest = external.read_bytes()
    paths = set()
    for entry in manifest.files:
        paths.add(Path(entry.path))
        paths.update(
            Path(item.path)
            for item in (entry.metadata_start, entry.metadata_end, *entry.pages)
        )
        for part in entry.partitions:
            paths.update(
                Path(item.path)
                for item in (part.metadata_start, part.metadata_end, *part.pages)
            )
    originals = {
        path.relative_to(Path(manifest.directory)).as_posix(): path.read_bytes()
        for path in paths
    }
    originals["manifest.json"] = original_manifest
    assert len(manifest.files[0].partitions) == 4
    assert len(originals) == 23
    original = DpePipeline.from_manifest(external)
    root = tmp_path / "archive"
    root.mkdir()
    (root / archive.SENTINEL_NAME).write_text(
        archive.SENTINEL_CONTENT, encoding="utf-8"
    )
    monkeypatch.setattr(settings, "source_archive_dir", str(root))
    monkeypatch.setattr(settings, "source_archive_min_free_bytes", 0)
    monkeypatch.setattr(PipelineRunner, "_read_run_started_at", lambda *_: STARTED)
    units = original.download()
    expected = transformed_rows(original, units)
    identity = original.checkpoint_identity(original.checkpoint_scope_key)
    try:
        PipelineRunner._archive_sources(
            "postgresql://offline-invalid/unused",
            1,
            original,
            units,
            None,
            logging.getLogger(__name__),
        )
    finally:
        original.close()
    assert external.read_bytes() == original_manifest
    cleanup_download_dir("dpe")
    assert not any(path.exists() for path in paths)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "relocated-cache"))
    monkeypatch.setattr(
        DpePipeline,
        "_request_json",
        lambda *args, **kwargs: pytest.fail("No replay HTTP"),
    )
    replay = archive.load_replay(root, "dpe", RUN_ID)
    assert {entry["relpath"] for entry in replay.files} == set(originals)
    restored = DpePipeline()
    try:
        units = restored.restore_source_inputs(replay, get_download_dir("dpe"))
        directory = Path(restored.import_manifest.directory)
        assert {
            relpath: (directory / relpath).read_bytes() for relpath in originals
        } == originals
        assert logical_manifest(restored.import_manifest) == logical_manifest(manifest)
        assert restored.checkpoint_identity(restored.checkpoint_scope_key) == identity
        assert transformed_rows(restored, units) == expected
        assert sum(expected.values()) == 10
    finally:
        restored.close()
    assert external.read_bytes() == original_manifest


def test_real_runner_restores_processes_only_four_csv_and_rearchives(
    generation, monkeypatch
):
    """Source flow is real; SQL, publication and required-hook work are doubled."""
    g = generation
    expected = transformed_rows(g.pipeline, g.units)
    g.pipeline.close()
    cleanup_download_dir("dpe")
    assert not g.cache.exists()
    pipeline = DpePipeline()
    rows = Counter()
    processed = []
    monkeypatch.setattr(
        pipeline, "download", lambda: pytest.fail("Replay cannot download")
    )
    monkeypatch.setattr(pipeline, "create_staging_table", lambda *_: rows.clear())
    monkeypatch.setattr(pipeline, "drop_staging_table", Mock())
    monkeypatch.setattr(pipeline, "swap", Mock())

    def stage(path):
        processed.append(path)
        with path.open(encoding="utf-8", newline="") as stream:
            loaded = list(csv.DictReader(stream))
        rows.update(tuple(sorted(row.items())) for row in loaded)
        return len(loaded)

    monkeypatch.setattr(pipeline, "stage", stage)
    monkeypatch.setattr(
        "app.etl.runner.dataset_execution_lock", lambda *_: nullcontext()
    )
    monkeypatch.setattr("app.etl.runner.advisory_lock", lambda *_: nullcontext())
    monkeypatch.setattr("app.etl.runner.requires_fresh_start", lambda *_: False)
    hooks = [ValidationHook(), DependentMatviewHook()]
    for hook in hooks:
        monkeypatch.setattr(hook, "on_run_complete", Mock())
    runner = PipelineRunner(
        hooks=hooks,
        scope_loader=lambda _: InstanceScope(frozenset(["dpe"]), ("44", "85")),
    )
    for name in (
        "_check_preflight_disk",
        "_update_etl_run",
        "_record_source_files",
        "_drop_published_old_tables",
        "_update_dataset_metadata",
        "_check_department_coverage",
    ):
        monkeypatch.setattr(runner, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_insert_etl_run", lambda *_: 2)
    monkeypatch.setattr(runner, "_staging_table_exists", lambda *_: False)
    monkeypatch.setattr(runner, "_count_staging_rows", lambda *_: sum(rows.values()))
    monkeypatch.setattr(runner, "_check_row_regression", lambda *_: (None, None))
    publication = MagicMock()
    publication.__enter__.return_value = publication
    publication.observe_stock.side_effect = lambda: sum(rows.values())
    publication.publish.side_effect = lambda callback: callback(object())
    monkeypatch.setattr("app.etl.runner.Publication", lambda *_: publication)
    result = runner.run(pipeline, source_replay=g.replay)
    assert result.success, result.error
    assert result.total_rows_loaded == 8
    assert len(processed) == 4
    assert rows == expected
    pipeline.swap.assert_called_once()
    pipeline.drop_staging_table.assert_not_called()
    second = archive.load_replay(g.root, "dpe", "2-20261005T100000Z")
    assert {item["relpath"] for item in second.files} == set(g.originals)
    restored = second.materialize(g.cache)
    assert {
        path.relative_to(g.cache).as_posix(): path.read_bytes() for path in restored
    } == g.originals
    assert not g.calls


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_real_cli_rejects_missing_or_corrupted_objects(generation, fault):
    from run_etl import _load_replay

    entry = next(
        item
        for item in generation.replay.files
        if item["relpath"].endswith("page-1.json")
    )
    digest = entry["sha256"]
    object_path = generation.root / "dpe" / "objects" / digest[:2] / digest
    os.chmod(object_path, 0o644)
    if fault == "missing":
        object_path.unlink()
    else:
        object_path.write_bytes(b"corrupt")
    with pytest.raises(SystemExit) as caught:
        _load_replay("dpe", RUN_ID)
    assert caught.value.code == 2


@pytest.mark.parametrize("fault", ["csv-only", "mixed-generation", "outside-root"])
def test_generic_archive_can_be_integral_but_dpe_restore_refuses(
    generation, monkeypatch, tmp_path, fault
):
    from run_etl import _load_replay

    g = generation
    source_root = tmp_path / "semantic-inputs"
    source_root.mkdir()
    for relpath, data in g.originals.items():
        target = source_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    selected = [source_root / relpath for relpath in g.originals]
    if fault == "csv-only":
        selected = [path for path in selected if path.suffix == ".csv"]
    else:
        manifest_path = source_root / "manifest.json"
        payload = json.loads(manifest_path.read_bytes())
        if fault == "mixed-generation":
            payload["files"][0]["import_id"] = "other-generation"
        else:
            original = Path(payload["files"][0]["path"])
            outside = tmp_path / "outside" / original.name
            outside.parent.mkdir()
            outside.write_bytes(original.read_bytes())
            payload["files"][0]["path"] = str(outside)
        manifest_path.write_bytes(json.dumps(payload).encode())
    run_id = "2-20261005T100000Z"
    archive.archive_sources(
        g.root, "dpe", run_id, selected, source_root, "telechargement"
    )
    replay = _load_replay("dpe", run_id)
    fresh = DpePipeline()
    try:
        expected = {
            "csv-only": "manifest.json",
            "mixed-generation": "generation or mapping mismatch",
            "outside-root": "outside",
        }[fault]
        with pytest.raises((ValueError, OSError), match=expected):
            fresh.restore_source_inputs(replay, tmp_path / "restore")
        private = list((tmp_path / "restore").glob("dpe-source-*"))
        assert len(private) == 1
        assert not g.calls
    finally:
        fresh.close()
    assert all(not path.exists() for path in private)

    locks = []

    @contextmanager
    def owned_lock(*args):
        locks.append("enter")
        try:
            yield
        finally:
            locks.append("exit")

    monkeypatch.setattr("app.etl.runner.dataset_execution_lock", owned_lock)
    unrelated = g.cache / "dpe-source-preexisting"
    unrelated.mkdir()
    witness = unrelated / "keep.txt"
    witness.write_bytes(b"preexisting-cache-must-survive")
    previous = set(g.cache.glob("dpe-source-*"))
    runner = PipelineRunner(
        scope_loader=lambda _: InstanceScope(frozenset(["dpe"]), ("44", "85"))
    )
    for name in ("_check_preflight_disk", "_insert_etl_run"):
        monkeypatch.setattr(
            runner,
            name,
            lambda *args: pytest.fail("Restore refusal must precede PostgreSQL work"),
        )
    fresh = DpePipeline()
    monkeypatch.setattr(
        fresh,
        "create_staging_table",
        lambda *args: pytest.fail("Restore refusal must precede staging creation"),
    )
    result = runner.run(fresh, source_replay=replay)
    assert not result.success
    assert isinstance(result.failure_cause.__cause__, (ValueError, OSError))
    assert locks == ["enter", "exit"]
    assert set(g.cache.glob("dpe-source-*")) == previous
    assert witness.read_bytes() == b"preexisting-cache-must-survive"
    assert not g.calls
