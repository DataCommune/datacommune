"""Synthetic transport proofs for complete, generation-bound DPE imports."""

import csv
import json
import logging
import re
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.etl.checkpoint import CheckpointManager
from app.etl.runner import PipelineRunner
from app.etl_dpe import config
from app.etl_dpe.pipeline import DpePipeline
from app.etl_dpe.sources import DPE_SOURCES, DpeImportManifest
from app.scope.model import SCOPE_LOCKS

# Captured at import: a test may install the local API twice (two runs).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    # DPE reads its departements from the bound instance scope (44/85,
    # within the migration 091 lock), for direct calls and runners alike.
    bind_departements(("44", "85"), DpePipeline)


def install_source_api(  # noqa: C901
    monkeypatch, tmp_path, *, fault=None, row_override=None, on_response=None
):
    """Install an official-shaped, wholly local paginated API.

    *on_response(request, response)*, when given, may replace each response
    (network faults of the resilience tests).
    """
    from app.config import settings

    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    calls = []
    metadata_reads = {}

    def respond(request):  # noqa: C901
        slug = request.url.path.split("/")[5]
        calls.append(request)
        if not request.url.path.endswith("/lines"):
            metadata_reads[slug] = metadata_reads.get(slug, 0) + 1
            version = "2026-09-02T19:09:24.311Z"
            if fault == "version" and metadata_reads[slug] > 1:
                version = "2026-09-03T19:09:24.311Z"
            schema = [
                {
                    "key": key,
                    "type": config.SOURCE_FIELD_TYPES[key],
                    **(
                        {"format": config.SOURCE_FIELD_FORMATS[key]}
                        if key in config.SOURCE_FIELD_FORMATS
                        else {}
                    ),
                }
                for key in config.EXPECTED_COLUMNS
            ]
            if fault == "schema":
                schema = schema[1:]
            if fault == "type":
                schema[0]["type"] = "integer"
            return httpx.Response(
                200,
                json={
                    "id": DPE_SOURCES[slug].dataset_id,
                    "slug": slug,
                    "href": DPE_SOURCES[slug].endpoint.removesuffix("/lines"),
                    "dataUpdatedAt": version,
                    "schema": schema,
                    "virtual": {"filters": [{"key": "dpe_desactive", "values": [0]}]},
                },
            )
        params = request.url.params
        department = re.fullmatch(r'code_departement_ban:"(\w+)"', params["qs"])[1]
        if params["size"] == "0":
            if fault == "count_zero":
                return httpx.Response(400, json={"error": "size zero unsupported"})
            return httpx.Response(200, json={"total": 1 if row_override else 2})
        if params.get("select") == "numero_dpe":
            assert params["size"] == "1"
            return httpx.Response(200, json={"total": 1 if row_override else 2})
        if fault == "http" and slug == "dpe02neuf":
            return httpx.Response(403)
        if fault == "http500" and slug == "dpe02neuf":
            return httpx.Response(500)
        second = "after" in params
        row = dict.fromkeys(config.EXPECTED_COLUMNS, None)
        row.update(
            {
                "numero_dpe": f"fixture-{slug}-{department}-{int(second)}",
                "code_departement_ban": department,
                "code_insee_ban": department + "109",
                "coordonnee_cartographique_x_ban": 354780.58,
                "coordonnee_cartographique_y_ban": 6690061.58,
                "adresse_brut": "3 RUE\nDES LILAS",
                "version_dpe": "2.6",
                "conso_5_usages_ep": 12345.67,
                "emission_ges_5_usages": 1500,
                "date_reception_dpe": "2026-09-01",
                "complement_adresse_logement": "",
            }
        )
        if row_override is not None:
            row.update(row_override)
            row["code_departement_ban"] = department
        if fault == "scope":
            row["code_departement_ban"] = "75"
        if fault == "identity":
            row["numero_dpe"] = None
        body = {"results": [row]}
        if not second:
            body["total"] = 1 if row_override else 2
        if (not second or fault == "cursor") and not row_override:
            body["next"] = str(request.url.copy_set_param("after", "cursor-one"))
        if fault == "truncated":
            body.pop("next", None)
        return httpx.Response(200, json=body)

    def handle(request):
        response = respond(request)
        return response if on_response is None else on_response(request, response)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    return calls


def transform_fixture(path, source_slug="dpe03existant"):
    """Route historical transform fixtures through the same verified transport."""
    with path.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    # Each synthetic pair must have its own identity; sharing the fixture's
    # number across departments now correctly fails raw collision validation.
    row.pop("numero_dpe", None)
    with pytest.MonkeyPatch.context() as monkeypatch:
        install_source_api(monkeypatch, path.parent / "transport", row_override=row)
        pipeline = DpePipeline()
        pipeline.download()
        entry = next(
            entry
            for entry in pipeline.import_manifest.files
            if entry.pair == (source_slug, "44")
        )
        try:
            return pipeline.transform(Path(entry.path))
        finally:
            pipeline.close()


def test_closed_sources_scope_and_mapping():
    assert set(DPE_SOURCES) == {"dpe03existant", "dpe02neuf"}
    assert "dpe" not in SCOPE_LOCKS  # lifted by migration 101 (04.1-07)
    assert DpePipeline().expected_file_count == 4
    assert DpePipeline().min_success_ratio == 1.0
    for slug, descriptor in DPE_SOURCES.items():
        assert descriptor.slug == slug
        assert descriptor.units["conso_5_usages_ep"] == "kWhEP/an"
        assert descriptor.units["emission_ges_5_usages"] == "kgCO2/an"
        assert (
            descriptor.column_mapping["surface_habitable_logement"]
            == "surface_habitable"
        )
        # 40 fields of 58.2, plus C2/C3 of plan 04-15 (one mapping version).
        assert len(descriptor.columns) == 44
        assert descriptor.mapping_version == "04.15-44-fields-v1"
        assert descriptor.units["conso_5_usages_par_m2_ep"] == "kWhEP/m2/an"
        assert descriptor.units["emission_ges_5_usages_par_m2"] == "kgCO2/m2/an"


def test_count_uses_measured_single_identity_projection(monkeypatch, tmp_path):
    calls = install_source_api(monkeypatch, tmp_path, fault="count_zero")
    assert len(DpePipeline().download()) == 4
    counts = [call for call in calls if call.url.params.get("size") == "1"]
    assert len(counts) == 8
    assert all(call.url.params["select"] == "numero_dpe" for call in counts)


def test_four_pairs_follow_runner_single_argument_transform(monkeypatch, tmp_path):
    calls = install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    paths = pipeline.download()
    assert len(paths) == 4
    observed = []

    def stage(path):
        with path.open(encoding="utf-8", newline="") as stream:
            observed.extend(csv.DictReader(stream))
        return 2

    monkeypatch.setattr(pipeline, "stage", stage)
    for path in paths:
        pipeline.validate_schema(path)
        assert (
            PipelineRunner()._process_file(pipeline, path, [], logging.getLogger()) == 2
        )
    assert {(row["regime_dpe"], row["code_departement"]) for row in observed} == {
        (regime, department)
        for regime in ("existant", "neuf")
        for department in ("44", "85")
    }
    assert all(row["adresse_brut"] == "3 RUE\nDES LILAS" for row in observed)
    assert all(row["conso_5_usages_ep"] == "12345.67" for row in observed)
    assert all(row["emission_ges_5_usages"] == "1500" for row in observed)
    assert all(request.url.host == "data.ademe.fr" for request in calls)
    assert all(request.url.params.get("format") != "csv" for request in calls)


@pytest.mark.parametrize(
    "fault", ["http", "version", "schema", "truncated", "cursor", "scope", "identity"]
)
def test_incomplete_source_refuses_whole_generation(monkeypatch, tmp_path, fault):
    install_source_api(monkeypatch, tmp_path, fault=fault)
    with pytest.raises((ValueError, httpx.HTTPError)):
        DpePipeline().download()
    assert not (tmp_path / "datacommune_dpe" / "manifest.json").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "generation",
        "count",
        "version",
        "pagination",
        "filter",
        "projection",
        "mapping",
        "previous_mapping",
    ],
)
def test_manifest_requires_exact_pairs_and_completeness(monkeypatch, tmp_path, fault):
    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    pipeline.download()
    manifest = pipeline.import_manifest
    entries = list(manifest.files)
    changes = {
        "generation": {"import_id": "another-generation"},
        "count": {"rows": 3},
        "version": {"version_end": "changed"},
        "pagination": {"pagination_complete": False},
        "filter": {"filter": 'code_departement_ban:"75"'},
        "projection": {"columns": ("numero_dpe",)},
        "mapping": {"mapping_version": "unreviewed"},
        # P-ARCHIVE-DPE option B: a 58.2 generation (40 fields) never replays
        # under the 04-15 column list, even with its own consistent projection.
        "previous_mapping": {
            "mapping_version": "58.2-40-fields-v1",
            "columns": tuple(config.EXPECTED_COLUMNS[:40]),
        },
    }
    if fault == "missing":
        entries.pop()
    elif fault == "duplicate":
        entries[-1] = entries[0]
    else:
        entries[0] = replace(entries[0], **changes[fault])
    with pytest.raises(ValueError):
        replace(manifest, files=tuple(entries)).validate()


def test_no_filename_guess_or_source_override(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    paths = pipeline.download()
    impostor = paths[0].parent / "dpe02neuf-44.csv"
    impostor.write_bytes(paths[0].read_bytes())
    with pytest.raises(ValueError, match="manifest"):
        pipeline.transform(impostor)
    with pytest.raises(TypeError):
        pipeline.transform(paths[0], "dpe02neuf-derived")


@pytest.mark.parametrize("damage", ["csv", "page"])
def test_checksums_cover_raw_pages_and_csv(monkeypatch, tmp_path, damage):
    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    paths = pipeline.download()
    target = (
        paths[0]
        if damage == "csv"
        else Path(pipeline.import_manifest.files[0].pages[0].path)
    )
    target.write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError):
        pipeline.transform(paths[0])


def _validation_checksums(monkeypatch, tmp_path, departements, bind):
    """Checksums computed by manifest validation, download then runner loop."""
    from app.etl_dpe import sources

    bind(departements, DpePipeline)
    install_source_api(monkeypatch, tmp_path / "-".join(departements))
    real, count = sources.sha256_file, []

    def counted(path):
        count.append(path)
        return real(path)

    monkeypatch.setattr(sources, "sha256_file", counted)
    pipeline = DpePipeline()
    try:
        for path in pipeline.download():
            pipeline.validate_schema(path)
            pipeline.transform(path)
    finally:
        pipeline.close()
        monkeypatch.setattr(sources, "sha256_file", real)
    return len(count)


def test_manifest_checksums_grow_linearly_with_its_files(
    monkeypatch, tmp_path, bind_departements
):
    # National scale: 202 pairs, tens of GB. One full validation per resolved
    # file would read the whole generation once per file (N^2).
    small = _validation_checksums(
        monkeypatch, tmp_path, ("44", "85"), bind_departements
    )
    large = _validation_checksums(
        monkeypatch, tmp_path, ("01", "02", "44", "85"), bind_departements
    )
    assert small > 0
    assert large <= 2 * small


def test_resolve_still_verifies_the_requested_entry(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    try:
        paths = pipeline.download()
        other = Path(pipeline.import_manifest.files[1].pages[0].path)
        other.write_bytes(other.read_bytes() + b" ")
        # Only the requested entry is read again; its own damage is refused.
        pipeline.validate_schema(paths[0])
        with pytest.raises(ValueError, match="checksum"):
            pipeline.validate_schema(paths[1])
        with pytest.raises(ValueError, match="checksum"):
            pipeline.import_manifest.validate()
    finally:
        pipeline.close()


def test_resume_reuses_only_validated_generation(monkeypatch, tmp_path):
    calls = install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    key = pipeline.checkpoint_scope_key
    paths = pipeline.download()
    checkpoint = CheckpointManager("dpe", pipeline.staging_table_name)
    checkpoint.save(
        [paths[0].name],
        scope_key=key,
        identity_key=pipeline.checkpoint_identity(key),
        source_files=PipelineRunner._fingerprint_sources(pipeline, paths),
        staging_rows=0,  # This transport-only test creates no database staging.
    )
    calls.clear()
    resumed = DpePipeline()
    assert resumed.checkpoint_scope_key == key
    assert resumed.download() == paths
    assert calls == []
    paths[0].write_text("corrupted", encoding="utf-8")
    fresh = DpePipeline()
    assert fresh.checkpoint_scope_key != key
    assert set(fresh.download()).isdisjoint(paths)


def test_valid_cache_without_checkpoint_starts_new_generation(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path)
    old = DpePipeline()
    paths = old.download()
    fresh = DpePipeline()
    assert fresh.checkpoint_scope_key != old.checkpoint_scope_key
    assert set(fresh.download()).isdisjoint(paths)


def test_runner_orphan_checkpoint_downloads_a_fresh_generation(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from unittest.mock import MagicMock, Mock

    from app.etl.hooks import DependentMatviewHook, ValidationHook

    calls = install_source_api(monkeypatch, tmp_path)
    old = DpePipeline()
    paths = old.download()
    checkpoint = CheckpointManager("dpe", old.staging_table_name)
    key = old.checkpoint_scope_key
    checkpoint.save(
        [paths[0].name],
        scope_key=key,
        identity_key=old.checkpoint_identity(key),
        source_files=PipelineRunner._fingerprint_sources(old, paths),
        staging_rows=0,
    )
    calls.clear()
    monkeypatch.setattr("app.etl.runner.check_disk_space", lambda *_: None)
    # The source fixture has a completed publication and no real journal/locks.
    monkeypatch.setattr(
        "app.etl.runner.dataset_execution_lock", lambda *_: nullcontext()
    )
    monkeypatch.setattr("app.etl.runner.requires_fresh_start", lambda *_: False)
    publication = MagicMock()
    publication.__enter__.return_value = publication
    publication.observe_stock.return_value = 8
    publication.row_count = 8
    publication.publish.side_effect = lambda callback: callback(object())
    monkeypatch.setattr("app.etl.runner.Publication", lambda *_: publication)
    required = [ValidationHook(), DependentMatviewHook()]
    for hook in required:
        monkeypatch.setattr(hook, "on_run_complete", Mock())
    runner = PipelineRunner(hooks=required)
    monkeypatch.setattr(runner, "_check_preflight_disk", lambda *_: None)
    monkeypatch.setattr(runner, "_insert_etl_run", lambda *_: 1)
    monkeypatch.setattr(runner, "_update_etl_run", lambda *_: None)
    monkeypatch.setattr(runner, "_record_source_files", lambda *_: None)
    monkeypatch.setattr(runner, "_drop_published_old_tables", lambda *_: None)
    monkeypatch.setattr(runner, "_update_dataset_metadata", lambda *_, **__: None)
    monkeypatch.setattr(runner, "_staging_table_exists", lambda *_: False)
    monkeypatch.setattr(runner, "_check_row_regression", lambda *_: (None, None))
    monkeypatch.setattr(runner, "_check_department_coverage", lambda *_: None)
    monkeypatch.setattr(runner, "_check_min_row_count", lambda *_: None)
    monkeypatch.setattr(runner, "_check_schema_drift", lambda *_: None)
    monkeypatch.setattr(runner, "_process_file", lambda *_, **__: 2)
    monkeypatch.setattr(runner, "_count_staging_rows", lambda *_: 8)
    monkeypatch.setattr("app.etl.runner.advisory_lock", lambda *_: nullcontext())
    monkeypatch.setattr("app.etl.runner.cleanup_download_dir", lambda *_: None)
    connect = Mock(side_effect=AssertionError("This source test must not use SQL"))
    monkeypatch.setattr("app.etl.runner.psycopg.connect", connect)
    fresh = DpePipeline()
    monkeypatch.setattr(fresh, "create_staging_table", lambda *_: None)
    monkeypatch.setattr(fresh, "validate_data", lambda *_: [])
    monkeypatch.setattr(fresh, "swap", lambda: None)
    download = fresh.download

    def download_after_orphan_cleanup():
        # The ordering is the contract: clear the orphan before source lookup
        # can restore a cached generation. Only a normal run may do this.
        assert not checkpoint.path.exists()
        return download()

    monkeypatch.setattr(fresh, "download", download_after_orphan_cleanup)
    result = runner.run(fresh)
    assert result.success
    connect.assert_not_called()
    assert calls
    assert fresh.import_manifest.import_id != old.import_manifest.import_id
    assert fresh.checkpoint_scope_key != old.checkpoint_scope_key
    assert set(fresh.download()).isdisjoint(paths)


def test_runner_dry_run_preserves_cached_generation_and_checkpoint(
    monkeypatch, tmp_path
):
    from unittest.mock import Mock

    calls = install_source_api(monkeypatch, tmp_path)
    old = DpePipeline()
    paths = old.download()
    checkpoint = CheckpointManager("dpe", old.staging_table_name)
    key = old.checkpoint_scope_key
    checkpoint.save(
        [paths[0].name],
        scope_key=key,
        identity_key=old.checkpoint_identity(key),
        source_files=PipelineRunner._fingerprint_sources(old, paths),
        staging_rows=0,
    )
    before = checkpoint.path.read_bytes()
    calls.clear()
    monkeypatch.setattr("app.etl.runner.check_disk_space", lambda *_: None)
    connect = Mock(side_effect=AssertionError("Dry-run must not use SQL"))
    monkeypatch.setattr("app.etl.runner.psycopg.connect", connect)
    pipeline = DpePipeline()
    validate = Mock(wraps=pipeline.validate_schema)
    monkeypatch.setattr(pipeline, "validate_schema", validate)

    result = PipelineRunner().run(pipeline, dry_run=True)

    assert result.success
    assert result.total_rows_loaded == 0
    connect.assert_not_called()
    assert calls == []
    assert checkpoint.path.read_bytes() == before
    assert pipeline.import_manifest.import_id == old.import_manifest.import_id
    # A cached import checkpoint must not skip validation of already-staged files.
    assert [call.args[0] for call in validate.call_args_list] == paths


def test_manifest_roundtrip_and_lossless_private_values(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    pipeline.download()
    saved = tmp_path / "datacommune_dpe" / "manifest.json"
    manifest = DpeImportManifest.load(saved)
    assert manifest == pipeline.import_manifest
    manifest.validate()
    row = next(manifest.files[0].source_rows())
    assert row["date_reception_dpe"] == "2026-09-01"
    assert row["version_dpe"] == "2.6"
    assert row["date_derniere_modification_dpe"] is None
    assert row["complement_adresse_logement"] == ""
    assert json.loads(saved.read_text())["schema_version"] == 1


def test_decimal_source_value_keeps_type_and_precision():
    from decimal import Decimal

    from app.etl_dpe.sources import decode_json

    row = decode_json(
        '{"number":12345.6700000000000000001,"text":"12345.6700000000000000001"}'
    )
    assert row["number"] == Decimal("12345.6700000000000000001")
    assert isinstance(row["number"], Decimal)
    assert isinstance(row["text"], str)


def test_source_type_drift_refuses_complete_transport(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path, fault="type")
    with pytest.raises(ValueError, match="schema"):
        DpePipeline().download()


def test_official_metadata_uses_distinct_opaque_id_and_slug(monkeypatch, tmp_path):
    from app.etl_dpe.sources import source_version

    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    pipeline.download()
    entry = pipeline.import_manifest.files[0]
    metadata = json.loads(Path(entry.metadata_start.path).read_text(encoding="utf-8"))
    metadata.update(
        id="meg-83tjwtg8dyz4vv7h1dqe",
        slug="dpe03existant",
        href=DPE_SOURCES["dpe03existant"].endpoint.removesuffix("/lines"),
    )
    assert len(source_version(metadata, DPE_SOURCES["dpe03existant"])) == 64


def test_http500_exhausted_retries_leave_no_publishable_manifest(monkeypatch, tmp_path):
    from app.etl_dpe.pipeline import _RetryableHTTPError

    calls = install_source_api(monkeypatch, tmp_path, fault="http500")
    monkeypatch.setattr(DpePipeline._request_json.retry, "wait", lambda _: 0)
    with pytest.raises(_RetryableHTTPError):
        DpePipeline().download()
    failed_page_calls = [
        request
        for request in calls
        if "/dpe02neuf/lines" in str(request.url)
        and request.url.params.get("select") != "numero_dpe"
    ]
    assert len(failed_page_calls) >= config.MAX_RETRIES
    assert not (tmp_path / "datacommune_dpe" / "manifest.json").exists()


def test_new_version_and_cosmetic_metadata_changes_remain_supported(
    monkeypatch, tmp_path
):
    from app.etl_dpe.sources import source_version

    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    pipeline.download()
    entry = pipeline.import_manifest.files[0]
    metadata = json.loads(Path(entry.metadata_start.path).read_text(encoding="utf-8"))
    metadata["dataUpdatedAt"] = "2026-09-04T19:09:24.311Z"
    metadata["schema"][0]["description"] = "New presentation only"
    metadata["schema"].append({"key": "unselected", "type": "boolean"})
    version = source_version(metadata, DPE_SOURCES[entry.source_slug])
    assert len(version) == 64
    assert version != entry.version_start


def test_source_version_consistent_across_departments(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    pipeline.download()
    manifest = pipeline.import_manifest
    entry = manifest.files[0]
    metadata = json.loads(Path(entry.metadata_start.path).read_text(encoding="utf-8"))
    metadata["dataUpdatedAt"] = "2026-09-04T19:09:24.311Z"
    from app.etl_dpe.sources import DpeArtifact, sha256_file, source_version

    for artifact in (entry.metadata_start, entry.metadata_end):
        Path(artifact.path).write_text(json.dumps(metadata), encoding="utf-8")
    changed = replace(
        entry,
        version_start=source_version(metadata, DPE_SOURCES[entry.source_slug]),
        version_end=source_version(metadata, DPE_SOURCES[entry.source_slug]),
        metadata_start=DpeArtifact(
            entry.metadata_start.path, sha256_file(Path(entry.metadata_start.path))
        ),
        metadata_end=DpeArtifact(
            entry.metadata_end.path, sha256_file(Path(entry.metadata_end.path))
        ),
    )
    with pytest.raises(ValueError, match="version"):
        replace(manifest, files=(changed, *manifest.files[1:])).validate()


def split_manifest_fixture(monkeypatch, tmp_path):
    from app.etl_dpe.sources import (
        SPLIT_44_FILTERS,
        DpeCollectionPartition,
        DpePageProof,
        csv_record,
        csv_value,
        sha256_file,
    )

    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    pipeline.download()
    manifest = pipeline.import_manifest
    entry = manifest.files[0]
    template = next(entry.source_rows())
    partitions, records = [], []
    for partition_id, kind, establishment in (
        ("house", "maison", "2024-01-01"),
        ("flat_early", "appartement", "2023-12-31"),
        ("flat_later", "appartement", None),
        ("other", None, None),
    ):
        row = {
            **template,
            "numero_dpe": "fixture-split-" + partition_id,
            "type_batiment": kind,
            "date_etablissement_dpe": establishment,
        }
        # Decimal values remain exact in the actual pipeline. This synthetic
        # JSON fixture serializes its known decimal values as numbers.
        from decimal import Decimal

        body = json.dumps(
            {"results": [row], "total": 1},
            default=lambda value: float(value) if isinstance(value, Decimal) else value,
        )
        page_path = Path(entry.path).with_name(f"{partition_id}-page.json")
        page_path.write_text(body, encoding="utf-8")
        pages = (DpePageProof(str(page_path), sha256_file(page_path), 1, None, None),)
        partitions.append(
            DpeCollectionPartition(
                partition_id=partition_id,
                filter=SPLIT_44_FILTERS[partition_id],
                initial_total=1,
                final_total=1,
                rows=1,
                metadata_start=entry.metadata_start,
                metadata_end=entry.metadata_end,
                pages=pages,
            )
        )
        records.append(row)
    encoded = csv_record(entry.columns) + b"".join(
        csv_record([csv_value(row.get(key)) for key in entry.columns])
        for row in records
    )
    Path(entry.path).write_bytes(encoded)
    split = replace(
        entry,
        pages=(),
        partitions=tuple(partitions),
        initial_total=4,
        final_total=4,
        rows=4,
        sha256=sha256_file(Path(entry.path)),
    )
    return replace(manifest, files=(split, *manifest.files[1:]))


def test_four_disjoint_partition_chains_preserve_missing_values(monkeypatch, tmp_path):
    manifest = split_manifest_fixture(monkeypatch, tmp_path)
    manifest.validate()
    rows = list(manifest.files[0].source_rows())
    assert len(rows) == 4
    assert rows[2]["date_etablissement_dpe"] is None
    assert rows[3]["type_batiment"] is None
    path = tmp_path / "split-manifest.json"
    manifest.save(path)
    assert DpeImportManifest.load(path) == manifest


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "count",
        "filter",
        "scope",
        "chain",
        "membership",
        "duplicate",
        "metadata",
    ],
)
def test_split_scope_requires_complete_disjoint_verified_partitions(
    monkeypatch, tmp_path, fault
):
    from app.etl_dpe.sources import DpePageProof, sha256_file

    manifest = split_manifest_fixture(monkeypatch, tmp_path)
    entry = manifest.files[0]
    partitions = list(entry.partitions)
    if fault == "missing":
        partitions.pop()
    elif fault == "count":
        partitions[0] = replace(partitions[0], final_total=2)
    elif fault == "filter":
        partitions[0] = replace(partitions[0], filter="unreviewed")
    elif fault == "scope":
        entry = replace(entry, department="85")
    elif fault == "metadata":
        partitions[0] = replace(
            partitions[0], metadata_end=replace(entry.metadata_end, sha256="bad")
        )
    else:
        part = partitions[0]
        page = part.pages[0]
        if fault == "chain":
            partitions[0] = replace(part, pages=(replace(page, after="invented"),))
        else:
            payload = json.loads(Path(page.path).read_text(encoding="utf-8"))
            if fault == "membership":
                payload["results"][0]["type_batiment"] = "appartement"
            else:
                payload["results"][0]["numero_dpe"] = "fixture-split-flat_early"
            Path(page.path).write_text(json.dumps(payload), encoding="utf-8")
            partitions[0] = replace(
                part,
                pages=(
                    DpePageProof(
                        page.path, sha256_file(Path(page.path)), 1, None, None
                    ),
                ),
            )
    altered = replace(entry, partitions=tuple(partitions))
    # Inspect source presence directly for membership/duplicate negatives: do
    # not let an unrelated CSV checksum mismatch mask these safeguards.
    with pytest.raises(ValueError):
        if fault in ("membership", "duplicate"):
            list(altered.source_rows())
        else:
            replace(manifest, files=(altered, *manifest.files[1:])).validate()
