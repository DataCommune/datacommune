"""Explicit frozen input must be selected and qualified before DB work."""

import hashlib
import json
from pathlib import Path

import pytest

from app.config import settings
from app.etl.base import DEPARTEMENTS
from app.etl.download import cleanup_download_dir
from app.etl.runner import PipelineRunner
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_vacance.pipeline import VacancePipeline


def manifest_file(tmp_path, dataset="vacance"):
    """Independent minimal CSVs; no transformation helpers generate their values."""
    folder = tmp_path / "durable"
    folder.mkdir(exist_ok=True)
    kinds = (
        ["communes"]
        if dataset == "vacance"
        else ["appartement", "maison", "1-2_pieces", "3+_pieces"]
    )
    files = []
    for index, kind in enumerate(kinds):
        content = (
            "CODGEO_26;LIBGEO_26;ff_pp_total_25;pp_vacant_26;pp_vacant_plus_2ans_26\n"
            "01001;Test;640;92;20\n"
            if dataset == "vacance"
            else "INSEE_C;LIBGEO;EPCI;DEP;REG;loypredm2;lwr.IPm2;upr.IPm2;"
            "id_zone;TYPPRED;nbobs_com;nbobs_mail;R2_adj\n"
            f"01001;Test;123;01;84;{10 + index};8;12;zone;commune;10;20;0.8\n"
        ).encode()
        path = folder / f"{kind}.csv"
        path.write_bytes(content)
        files.append(
            {
                "kind": kind,
                "path": str(path),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    data = {
        "version": 1,
        "dataset": dataset,
        "vintage": 2026 if dataset == "vacance" else 2025,
        "source_contract": "lovac-communes-v1:2026:2025"
        if dataset == "vacance"
        else "loyers-prediction-metadata-v1:2025",
        "files": files,
    }
    path = folder / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


@pytest.fixture(autouse=True)
def separate_temporary_output(monkeypatch, tmp_path, bind_departements):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    bind_departements(DEPARTEMENTS, LoyersPipeline, VacancePipeline)


@pytest.mark.parametrize(
    "dataset,cls", [("vacance", VacancePipeline), ("loyers", LoyersPipeline)]
)
def test_local_inputs_are_snapshotted_before_runner_and_survive_cleanup(
    tmp_path, monkeypatch, dataset, cls
):
    path, data = manifest_file(tmp_path, dataset)
    pipeline = cls.from_manifest(path)

    def no_network(*args, **kwargs):
        pytest.fail("frozen input attempted network access")

    monkeypatch.setattr(cls, "_async_download", no_network)
    selected = pipeline.download()
    assert len(selected) == len(data["files"])
    original = [Path(entry["path"]).read_bytes() for entry in data["files"]]
    assert [file.read_bytes() for file in selected] == original
    # Changes to durable sources after qualification cannot alter the consumed snapshot.
    Path(data["files"][0]["path"]).write_bytes(b"replaced after qualification")
    assert [file.read_bytes() for file in pipeline.download()] == original
    cleanup_download_dir(dataset)
    assert path.exists()
    assert all(Path(entry["path"]).exists() for entry in data["files"])
    assert all(file.exists() for file in selected)
    pipeline.close()
    assert all(not file.exists() for file in selected)


@pytest.mark.parametrize(
    "dataset,cls", [("vacance", VacancePipeline), ("loyers", LoyersPipeline)]
)
def test_prepared_invocations_own_their_cleanup_and_resume_names(
    tmp_path, dataset, cls
):
    from app.etl.checkpoint import CheckpointManager

    path, data = manifest_file(tmp_path, dataset)
    first = cls.from_manifest(path)
    second = cls.from_manifest(path)
    first_paths, second_paths = first.download(), second.download()
    assert first_paths[0].parent != second_paths[0].parent
    assert first.checkpoint_scope_key == second.checkpoint_scope_key
    checkpoint = CheckpointManager(dataset, first.staging_table_name)
    key = first.checkpoint_scope_key
    checkpoint.save(
        [first_paths[0].name],
        scope_key=key,
        identity_key=first.checkpoint_identity(key),
        source_files=PipelineRunner._fingerprint_sources(first, first_paths),
        staging_rows=0,  # Snapshot ownership only; no SQL staging in this test.
    )
    first.close()
    assert checkpoint.load()["scope_key"] == second.checkpoint_scope_key
    assert second_paths[0].name in checkpoint.get_loaded_files()
    # A succeeds after B has prepared but before B acquires the dataset lock.
    cleanup_download_dir(dataset)
    assert second.download() == second_paths
    third = cls.from_manifest(path)
    third_paths = third.download()
    second.close()
    second.close()
    assert all(not file.exists() for file in second_paths)
    assert third.download() == third_paths
    assert path.exists()
    assert all(Path(entry["path"]).exists() for entry in data["files"])
    third.close()


@pytest.mark.parametrize(
    "dataset,cls", [("vacance", VacancePipeline), ("loyers", LoyersPipeline)]
)
@pytest.mark.parametrize(
    "exit_kind", ["lock_refused", "exception", "dry_run", "dry_run_error"]
)
def test_runner_releases_only_its_snapshot_on_every_exit(
    tmp_path, monkeypatch, dataset, cls, exit_kind
):
    from contextlib import contextmanager

    import app.etl.runner as runner_module

    path, _ = manifest_file(tmp_path, dataset)
    pipeline, sibling = cls.from_manifest(path), cls.from_manifest(path)
    selected, sibling_paths = pipeline.download(), sibling.download()
    checkpoint = runner_module.CheckpointManager(dataset, pipeline.staging_table_name)
    key = pipeline.checkpoint_scope_key
    checkpoint.save(
        [selected[0].name],
        scope_key=key,
        identity_key=pipeline.checkpoint_identity(key),
        source_files=PipelineRunner._fingerprint_sources(pipeline, selected),
        staging_rows=0,
    )
    checkpoint_bytes = checkpoint.path.read_bytes()

    @contextmanager
    def lock(*args):
        if exit_kind == "lock_refused":
            raise RuntimeError("another run holds the lock")
        yield

    monkeypatch.setattr(runner_module, "dataset_execution_lock", lock)
    runner = runner_module.PipelineRunner()
    if exit_kind == "exception":

        def fail(*args, **kwargs):
            raise RuntimeError("runner interrupted")

        monkeypatch.setattr(runner, "_run", fail)
    if exit_kind == "dry_run_error":
        selected[0].write_bytes(b"corrupted working snapshot")
    if exit_kind in {"lock_refused", "exception"}:
        with pytest.raises(RuntimeError):
            runner.run(pipeline)
    else:
        result = runner.run(pipeline, dry_run=True)
        assert result.success is (exit_kind == "dry_run")
    assert all(not file.exists() for file in selected)
    assert sibling.download() == sibling_paths
    assert checkpoint.path.read_bytes() == checkpoint_bytes
    assert path.exists()
    sibling.close()


@pytest.mark.parametrize(
    "case",
    [
        "hash",
        "size",
        "missing",
        "dataset",
        "vintage",
        "contract",
        "extra",
        "duplicate",
        "relative",
        "temporary",
        "columns",
    ],
)
def test_bad_manifest_is_refused_before_cli_can_enter_runner(  # noqa: C901
    tmp_path, monkeypatch, case
):
    import run_etl

    path, data = manifest_file(tmp_path)
    if case == "hash":
        data["files"][0]["sha256"] = "0" * 64
    elif case == "size":
        data["files"][0]["bytes"] += 1
    elif case == "missing":
        Path(data["files"][0]["path"]).unlink()
    elif case == "dataset":
        data["dataset"] = "loyers"
    elif case == "vintage":
        data["vintage"] = 2025
    elif case == "contract":
        data["source_contract"] = "unreviewed"
    elif case == "extra":
        data["publication_date"] = "2026-09-09"
    elif case == "duplicate":
        data["files"].append(data["files"][0])
    elif case == "relative":
        data["files"][0]["path"] = "relative.csv"
    elif case == "temporary":
        unsafe = Path(settings.etl_temp_dir) / "source.csv"
        unsafe.parent.mkdir()
        unsafe.write_bytes(Path(data["files"][0]["path"]).read_bytes())
        data["files"][0]["path"] = str(unsafe)
    elif case == "columns":
        source = Path(data["files"][0]["path"])
        raw = source.read_bytes().replace(b"CODGEO_26", b"CODGEO_27")
        source.write_bytes(raw)
        data["files"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    path.write_text(json.dumps(data), encoding="utf-8")
    entered = []
    monkeypatch.setattr(
        run_etl.PipelineRunner, "run", lambda *a, **k: entered.append(True)
    )
    monkeypatch.setattr(
        run_etl.sys, "argv", ["run_etl", "vacance", "--source-manifest", str(path)]
    )
    with pytest.raises((ValueError, OSError)):
        run_etl.main()
    assert entered == []


def test_checkpoint_scope_changes_with_manifest_bytes_and_rejects_tampered_snapshot(
    tmp_path,
):
    path, data = manifest_file(tmp_path)
    first = VacancePipeline.from_manifest(path)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    second = VacancePipeline.from_manifest(path)
    assert first.checkpoint_scope_key != second.checkpoint_scope_key
    assert first.checkpoint_scope_key != VacancePipeline().checkpoint_scope_key
    first.download()[0].write_bytes(b"changed working copy")
    with pytest.raises(ValueError, match="snapshot checksum"):
        first.download()


@pytest.mark.parametrize("mutation", ["missing", "duplicate_kind", "same_path"])
def test_four_rental_types_are_required(tmp_path, mutation):
    path, data = manifest_file(tmp_path, "loyers")
    if mutation == "missing":
        data["files"].pop()
    elif mutation == "duplicate_kind":
        data["files"][1]["kind"] = data["files"][0]["kind"]
    else:
        data["files"][1] = data["files"][0] | {"kind": "maison"}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        LoyersPipeline.from_manifest(path)


@pytest.mark.parametrize(
    "mutation", ["boolean_version", "duplicate_json_key", "same_bytes_distinct_paths"]
)
def test_manifest_rejects_ambiguous_types_and_repeated_sources(tmp_path, mutation):
    path, data = manifest_file(tmp_path, "loyers")
    if mutation == "boolean_version":
        data["version"] = True
    elif mutation == "same_bytes_distinct_paths":
        first, second = data["files"][:2]
        Path(second["path"]).write_bytes(Path(first["path"]).read_bytes())
        second.update({"bytes": first["bytes"], "sha256": first["sha256"]})
    text = json.dumps(data)
    if mutation == "duplicate_json_key":
        text = text.replace('"version": 1', '"version": 2, "version": 1')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        LoyersPipeline.from_manifest(path)


def test_snapshot_copy_preserves_existing_disk_reserve(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import app.etl.source_manifest as source_module

    path, _ = manifest_file(tmp_path)
    monkeypatch.setattr(
        source_module.shutil, "disk_usage", lambda _: SimpleNamespace(free=0)
    )
    with pytest.raises(ValueError, match="existing ETL disk reserve"):
        VacancePipeline.from_manifest(path)
    assert not list(Path(settings.etl_temp_dir).rglob("frozen-*"))


def test_source_changed_during_read_is_refused_even_if_read_bytes_match(
    tmp_path, monkeypatch
):
    import app.etl.source_manifest as source_module

    path, data = manifest_file(tmp_path)
    source = Path(data["files"][0]["path"])
    original = source.read_bytes()
    sha256 = hashlib.sha256

    class MutatingDigest:
        def __init__(self):
            self.inner = sha256()

        def update(self, chunk):
            self.inner.update(chunk)
            # The chunk already read still hashes correctly; the open file changes.
            source.write_bytes(original.replace(b"Test", b"Else"))

        def hexdigest(self):
            return self.inner.hexdigest()

    monkeypatch.setattr(
        source_module.hashlib,
        "sha256",
        lambda *args: sha256(*args) if args else MutatingDigest(),
    )
    with pytest.raises(ValueError, match="changed while reading"):
        VacancePipeline.from_manifest(path)
    assert source.read_bytes() != original


@pytest.mark.parametrize("kind", ["source", "manifest"])
@pytest.mark.parametrize("change", ["same_size", "replace_path"])
def test_input_change_is_refused_when_timestamp_identity_collides(
    tmp_path, monkeypatch, kind, change
):
    import app.etl.source_manifest as source_module

    path, data = manifest_file(tmp_path)
    watched = path if kind == "manifest" else Path(data["files"][0]["path"])
    original = watched.read_bytes()
    changed = (
        original.replace(b'"version": 1', b'"version": 2')
        if kind == "manifest"
        else original.replace(b"Test", b"Else")
    )
    assert len(changed) == len(original) and changed != original
    open_path = Path.open
    mutated = False

    # Model coarse or restored timestamps without depending on filesystem ticks.
    # Device, inode and size remain observable, including path replacement.
    monkeypatch.setattr(
        source_module,
        "_identity",
        lambda info: (info.st_dev, info.st_ino, info.st_size, 0, 0),
    )

    class WatchedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            nonlocal mutated
            result = self.stream.__exit__(*args)
            if change == "replace_path" and not mutated:
                mutated = True
                replacement = watched.with_suffix(".replacement")
                # Close first: Windows need not allow renaming an open file.
                # Identical bytes test pathname identity independently of hash.
                replacement.write_bytes(original)
                replacement.replace(watched)
            return result

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            nonlocal mutated
            chunk = self.stream.read(size)
            if chunk and not mutated and change == "same_size":
                mutated = True
                watched.write_bytes(changed)
            return chunk

    def open_watched(candidate, mode="r", *args, **kwargs):
        stream = open_path(candidate, mode, *args, **kwargs)
        return (
            WatchedReader(stream) if candidate == watched and mode == "rb" else stream
        )

    monkeypatch.setattr(Path, "open", open_watched)
    with pytest.raises(ValueError, match="changed while reading"):
        VacancePipeline.from_manifest(path)
    assert mutated
    assert not list(Path(settings.etl_temp_dir).rglob("frozen-*"))


@pytest.mark.parametrize("dataset", ["vacance", "loyers"])
def test_source_manifest_cli_accepts_exact_dataset_and_path(dataset):
    from run_etl import _parse_args

    options = _parse_args([dataset, "--source-manifest", "local manifest.json"])
    assert options.datasets == [dataset]
    assert options.source_manifest == Path("local manifest.json")


@pytest.mark.parametrize(
    "datasets", [[], ["dpe"], ["ban"], ["vacance", "loyers"], ["loyers", "loyers"]]
)
def test_source_manifest_cli_refuses_implicit_or_other_datasets(datasets):
    from run_etl import _parse_args

    with pytest.raises(SystemExit) as error:
        _parse_args([*datasets, "--source-manifest", "fixture.json"])
    assert error.value.code == 2


def test_source_manifest_cli_refuses_two_manifest_kinds():
    from run_etl import _parse_args

    with pytest.raises(SystemExit) as error:
        _parse_args(["vacance", "--source-manifest", "a", "--dpe-manifest", "b"])
    assert error.value.code == 2


def test_vps_mounts_only_dedicated_communal_sources_read_only():
    import yaml

    overlay = Path(__file__).resolve().parents[2] / "docker-compose.vps.yml"
    services = yaml.safe_load(overlay.read_text(encoding="utf-8"))["services"]
    mounts = services["api"]["volumes"]
    target = "/opt/datacommune/data/sources"
    dedicated = [
        mount
        for mount in mounts
        if isinstance(mount, dict) and mount.get("target") == target
    ]
    assert dedicated == [
        {
            "type": "bind",
            "source": target,
            "target": target,
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    assert not any(
        (mount.split(":")[0] if isinstance(mount, str) else mount.get("source"))
        == "/opt/datacommune/data"
        for mount in mounts
    )


@pytest.mark.parametrize("kind", ["source", "manifest"])
@pytest.mark.parametrize("change", ["stable", "descriptor_ctime", "path_ctime"])
def test_ctime_is_compared_within_each_stat_api(tmp_path, monkeypatch, kind, change):
    from app.etl import source_manifest as module

    manifest, data = manifest_file(tmp_path)
    watched = manifest if kind == "manifest" else Path(data["files"][0]["path"])
    real_stat = Path.stat
    real_fstat = module.os.fstat
    calls = 0

    class Metadata:
        def __init__(self, info, ctime):
            self.info = info
            self.st_ctime_ns = ctime

        def __getattr__(self, name):
            return getattr(self.info, name)

    def descriptor_stat(fd):
        nonlocal calls
        calls += 1
        ctime = 1 if change == "descriptor_ctime" and calls > 1 else 0
        return Metadata(real_fstat(fd), ctime)

    def pathname_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if path == watched:
            ctime = 11 if change == "path_ctime" and calls else 10
            return Metadata(info, ctime)
        return info

    monkeypatch.setattr(module.os, "fstat", descriptor_stat)
    monkeypatch.setattr(Path, "stat", pathname_stat)

    def run():
        if kind == "manifest":
            module._read_manifest(manifest, "vacance")
        else:
            entry = module.SourceFile.model_validate(data["files"][0])
            module._copy_verified(entry, tmp_path / "copy.csv")

    if change == "stable":
        run()
    else:
        with pytest.raises(ValueError, match="changed while reading"):
            run()
