"""Explicit offline source selection must preserve the verified generation."""

from pathlib import Path

import pytest

from app.etl_dpe.pipeline import DpePipeline
from tests.test_etl_dpe_sources import install_source_api


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    # DPE reads its departements from the bound instance scope (44/85,
    # within the migration 091 lock), for direct calls and runners alike.
    bind_departements(("44", "85"), DpePipeline)


@pytest.mark.parametrize("slug", ["dpe03existant", "dpe02neuf"])
def test_official_pagination_accepts_only_pinned_dataset_alias(slug):
    from app.etl_dpe.sources import DPE_SOURCES, page_cursor

    source = DPE_SOURCES[slug]
    alias = source.endpoint.replace(f"/{slug}/", f"/{source.dataset_id}/")
    assert page_cursor(alias + "?after=known-cursor", source.endpoint) == "known-cursor"
    for invalid in (
        alias.replace(source.dataset_id, "unreviewed-dataset"),
        alias.replace("https://", "http://"),
        alias.replace("data.ademe.fr", "untrusted.invalid"),
        DPE_SOURCES[
            "dpe02neuf" if slug == "dpe03existant" else "dpe03existant"
        ].endpoint,
    ):
        with pytest.raises(ValueError, match="pagination"):
            page_cursor(invalid + "?after=known-cursor", source.endpoint)


def frozen_source(monkeypatch, tmp_path):
    calls = install_source_api(monkeypatch, tmp_path / "source")
    original = DpePipeline()
    original.download()
    manifest = original.import_manifest
    path = tmp_path / "frozen-manifest.json"
    manifest.save(path)
    original.close()
    return path, manifest, calls


def test_explicit_manifest_uses_verified_files_without_checkpoint_or_http(
    monkeypatch, tmp_path
):
    path, manifest, calls = frozen_source(monkeypatch, tmp_path)
    calls.clear()
    pipeline = DpePipeline.from_manifest(path)
    try:
        assert pipeline.checkpoint_scope_key.endswith(manifest.import_id)
        assert pipeline.download() == [Path(entry.path) for entry in manifest.files]
        assert pipeline.import_manifest == manifest
        assert not calls
    finally:
        pipeline.close()


def test_explicit_manifest_rejects_tampered_source_before_start(monkeypatch, tmp_path):
    path, manifest, calls = frozen_source(monkeypatch, tmp_path)
    Path(manifest.files[0].path).write_text("altered", encoding="utf-8")
    calls.clear()
    with pytest.raises(ValueError, match="checksum"):
        DpePipeline.from_manifest(path)
    assert not calls


@pytest.mark.parametrize("args", [[], ["ban"], ["dpe", "ban"], ["dpe", "dpe"]])
def test_manifest_cli_requires_only_explicit_dpe(args):
    from run_etl import _parse_args

    with pytest.raises(SystemExit) as error:
        _parse_args([*args, "--dpe-manifest", "fixture.json"])
    assert error.value.code == 2


def test_manifest_cli_keeps_dry_run_and_exact_path():
    from run_etl import _parse_args

    options = _parse_args(["--dry-run", "dpe", "--dpe-manifest", "cache path.json"])
    assert options.datasets == ["dpe"]
    assert options.dry_run is True
    assert options.dpe_manifest == Path("cache path.json")


def test_existing_cli_keeps_multiple_datasets_with_intermixed_dry_run():
    from run_etl import _parse_args

    options = _parse_args(["ban", "--dry-run", "cadastre"])
    assert options.datasets == ["ban", "cadastre"]
    assert options.dry_run is True
    assert options.dpe_manifest is None


def test_manifest_cli_passes_selected_pipeline_to_runner(monkeypatch, tmp_path):
    import run_etl

    path, manifest, calls = frozen_source(monkeypatch, tmp_path)
    calls.clear()
    observed = []

    def run(_runner, pipeline, *, dry_run):
        from app.etl.schemas import EtlResult

        observed.append((pipeline.import_manifest, dry_run))
        pipeline.close()
        return EtlResult(
            dataset_name="dpe",
            stages=[],
            total_rows_loaded=0,
            total_duration_seconds=0,
            success=True,
        )

    monkeypatch.setattr(run_etl.PipelineRunner, "run", run)
    monkeypatch.setattr(run_etl, "build_default_hooks", lambda: [])
    monkeypatch.setattr(
        run_etl.sys,
        "argv",
        ["run_etl", "--dry-run", "dpe", "--dpe-manifest", str(path)],
    )
    run_etl.main()
    assert observed == [(manifest, True)]
    assert not calls
