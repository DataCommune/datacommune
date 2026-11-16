"""Import manifest of the work base (plan 02-10, PERF-00, D-33, DATA-01).

Pure parts of ``scripts/export_import_manifest.py`` plus its command line up
to the database connection: the exporter never connects before it has
refused an existing receipt, an unknown dataset or a malformed run id.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import psycopg
import pytest

from run_etl import ALL_PIPELINES
from scripts import export_import_manifest as eim

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_ID = "7-20260923T001512Z"
UNKNOWN_SHA = "f" * 40


def _head_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _stage(name, seconds, rows):
    return {"stage_name": name, "duration_seconds": seconds, "rows_affected": rows}


def _etl_run(**overrides):
    run = {
        "id": 7,
        "dataset_name": "vacance",
        "status": "success",
        "started_at": "2026-09-23T00:15:12.345678+00:00",
        "completed_at": "2026-09-23T00:15:14.000000+00:00",
        "total_rows_loaded": 460,
        "total_duration_seconds": 2.0,
        "error_message": None,
        "stages": [
            _stage("download", 1.2, None),
            _stage("scope_filter", 0.03, 34452),
            _stage("swap", 0.07, 460),
        ],
        "scope_snapshot": {"datasets": ["vacance"], "departements": ["44", "85"]},
        "source_files": [
            {
                "name": "lovac_communes.csv",
                "bytes": 10,
                "sha256": "ab",
                "physical_lines": 34912,
            }
        ],
    }
    run.update(overrides)
    return run


def _table_info(kind="table"):
    info = {
        "kind": kind,
        "count_method": {"method": "code_departement", "column": "code_departement"},
        "total_rows": 460,
        "rows_by_departement": {"44": 207, "85": 253},
        "sizes": {
            "total_bytes": 196608,
            "table_bytes": 65536,
            "indexes_bytes": 114688,
            "toast_bytes": 16384,
        },
    }
    if kind == "partitioned":
        info["partitions"] = {
            "44": {"name": "t_44", "total_bytes": 100, "indexes_bytes": 40},
            "85": {"name": "t_85", "total_bytes": 90, "indexes_bytes": 30},
        }
    else:
        info["row_bytes_by_departement"] = {"44": 30000, "85": 36000}
    return info


def _manifest(**overrides):
    kwargs = {
        "run_id": RUN_ID,
        "dataset": "vacance",
        "etl_run": _etl_run(),
        "dataset_metadata": {"dataset_name": "vacance", "row_count": 460},
        "tables": {"vacance": _table_info()},
        "repo_sha": "a" * 40,
        "gabarit": {"cores": 8, "memory_mb": 16384},
        "disk_peak": eim.disk_peak_block(123456, "rootfs du CT (/)", 30),
        "scenario": "rejeu",
        "sources_locales": "absentes",
        "scope_filter_log": {
            "_vacance_staging_flat": {"removed": 34452, "without_departement": 0}
        },
        "exported_at": "2026-09-23T00:20:00Z",
    }
    kwargs.update(overrides)
    return eim.build_manifest(**kwargs)


# ------------------------------------------------------------ DATASET_TABLES --


def test_dataset_tables_cover_every_pipeline_and_the_geo_referentials():
    names = {cls().dataset_name for cls in ALL_PIPELINES}
    missing = sorted(names - set(eim.DATASET_TABLES))
    assert missing == []
    assert set(eim.SCRIPT_TABLES) == {
        "geo-boundaries",
        "refresh-commune-synthese",
        "refresh-communes-lookup",
    }
    assert not set(eim.SCRIPT_TABLES) & set(eim.DATASET_TABLES)


def test_every_entry_names_its_published_table_and_a_unit_per_table():
    for dataset, entry in eim.DATASET_TABLES.items():
        assert entry["published"] in entry["tables"], dataset
        for table, unit in entry["tables"].items():
            assert unit in (eim.UNIT_CSV_LINE, eim.UNIT_PUBLISHED), (dataset, table)


def test_every_pipeline_production_table_is_listed():
    for cls in ALL_PIPELINES:
        pipeline = cls()
        entry = eim.DATASET_TABLES[pipeline.dataset_name]
        assert pipeline.production_table_name in entry["tables"], pipeline.dataset_name


# ---------------------------------------------------------------- manifest --


def _assert_keys_sorted(pairs):
    keys = [key for key, _ in pairs]
    assert keys == sorted(keys), keys
    return dict(pairs)


def test_manifest_is_deterministic_with_sorted_keys():
    first = eim.dumps_sorted(_manifest())
    second = eim.dumps_sorted(_manifest())
    assert first == second
    # Property, not a copy of the serializer: keys in order at every level,
    # exactly one final newline.
    json.loads(first, object_pairs_hook=_assert_keys_sorted)
    assert first.endswith("}\n")
    assert not first.endswith("\n\n")


def test_key_order_check_detects_unsorted_output():
    with pytest.raises(AssertionError):
        json.loads('{"b": 1, "a": {"y": 2}}', object_pairs_hook=_assert_keys_sorted)
    with pytest.raises(AssertionError):
        json.loads('{"a": {"y": 2, "x": 1}}', object_pairs_hook=_assert_keys_sorted)


def test_manifest_top_level_keys():
    manifest = _manifest()
    for key in (
        "run_id",
        "dataset",
        "etl_run",
        "dataset_metadata",
        "tables",
        "repo_sha",
        "gabarit",
        "disk_peak",
        "scenario",
        "sources_locales",
        "counting",
    ):
        assert key in manifest, key
    assert manifest["run_id"] == RUN_ID


def test_non_partitioned_table_carries_row_bytes_marked_as_row_data():
    table = _manifest()["tables"]["vacance"]
    assert table["row_bytes_by_departement"]["44"] == {
        "bytes": 30000,
        "nature": "données de ligne, hors index",
    }
    assert table["published_rows_by_departement"] == {"44": 207, "85": 253}


def test_partitioned_table_carries_sizes_per_partition():
    manifest = _manifest(tables={"vacance": _table_info("partitioned")})
    table = manifest["tables"]["vacance"]
    assert table["partitions"]["85"]["total_bytes"] == 90
    assert "row_bytes_by_departement" not in table


def test_disk_peak_is_a_qualified_sampled_maximum():
    block = _manifest()["disk_peak"]
    assert block["bytes"] == 123456
    assert block["volume"] == "rootfs du CT (/)"
    assert block["sample_interval_s"] == 30
    assert "échantillonné" in block["nature"]


def test_unmeasured_counters_are_written_non_mesure():
    run = _etl_run(source_files=[{"name": "x.zip", "bytes": 1, "sha256": "cd"}])
    counting = _manifest(etl_run=run, scope_filter_log=None)["counting"]
    assert counting["source_rows"] == eim.NOT_MEASURED
    assert counting["scope_filter"] == eim.NOT_MEASURED
    assert counting["stages_rows_affected"]["download"] == eim.NOT_MEASURED
    assert counting["stages_rows_affected"]["swap"] == 460


def test_legacy_receipt_key_rows_still_counts_physical_lines():
    """Runs journaled before plan 04-15 (C8) wrote the same count as "rows"."""
    run = _etl_run(
        source_files=[
            {"name": "a.csv", "bytes": 1, "sha256": "ab", "rows": 3},
            {"name": "b.csv", "bytes": 1, "sha256": "cd", "physical_lines": 4},
        ]
    )
    counting = _manifest(etl_run=run)["counting"]
    assert counting["source_rows"] == 7
    assert counting["source_rows_unit"] == eim.UNIT_CSV_LINE


def test_counting_declares_unit_and_reference_table_per_table():
    counting = _manifest()["counting"]
    assert counting["source_rows"] == 34912
    assert "physical_lines" in counting["source_rows_origin"]
    assert counting["tables"]["vacance"] == {
        "unit": eim.UNIT_CSV_LINE,
        "published_table": "vacance",
    }
    assert counting["scope_filter"]["_vacance_staging_flat"]["removed"] == 34452


RUNNER_LOG_LINE = (
    "00:18:22 etl.vacance          INFO    Scope filter for vacance, "
    "_vacance_staging_flat: 34452 rows removed, of which 0 without departement"
)


def test_scope_filter_log_reads_the_runner_format():
    found = eim.parse_scope_filter_log(RUNNER_LOG_LINE + "\n", "vacance")
    assert found == {
        "_vacance_staging_flat": {"removed": 34452, "without_departement": 0}
    }


@pytest.mark.parametrize(
    ("text", "dataset"),
    [
        ("", "vacance"),
        ("00:18:22 etl.vacance INFO Stage [swap] done in 0.07s\n", "vacance"),
        (RUNNER_LOG_LINE, "loyers"),
    ],
    ids=["empty", "no-scope-line", "other-dataset"],
)
def test_log_without_a_recognised_line_is_not_measured(text, dataset):
    found = eim.parse_scope_filter_log(text, dataset)
    assert found is None
    counting = _manifest(scope_filter_log=found)["counting"]
    assert counting["scope_filter"] == eim.NOT_MEASURED


def test_disk_peak_absent_is_non_mesure():
    block = eim.disk_peak_block(None, None, None)
    assert block["bytes"] == eim.NOT_MEASURED


# ----------------------------------------------------------------- scenario --


def test_scenario_comes_from_the_database_only():
    assert eim.deduce_scenario(0) == "initial"
    assert eim.deduce_scenario(3) == "rejeu"


def test_published_dataset_without_cache_is_a_replay():
    # cleanup_download_dir purges the cache after each successful publication:
    # a published dataset with an absent cache is a replay, never an initial load.
    manifest = _manifest(
        scenario=eim.deduce_scenario(1),
        sources_locales=eim.sources_locales_state(cache_present=False),
    )
    assert manifest["scenario"] == "rejeu"
    assert manifest["sources_locales"] == "absentes"


def test_declared_scenario_contradicting_the_database_is_refused():
    with pytest.raises(eim.ExportError, match="scenario"):
        eim.check_scenario("initial", successful_runs_before=1)
    with pytest.raises(eim.ExportError, match="scenario"):
        eim.check_scenario("rejeu", successful_runs_before=0)
    eim.check_scenario("amorcage", successful_runs_before=0)


# ------------------------------------------------------------------ run id --


def test_run_id_round_trip():
    assert eim.parse_run_id(RUN_ID) == (7, "20260923T001512Z")
    assert eim.parse_run_id("run-inexistant-00000000") is None
    assert eim.parse_run_id("123") is None


# ------------------------------------------------------- pointer, repo_sha --


def test_pointer_names_the_receipt_of_its_run_id(tmp_path):
    receipt = tmp_path / f"{RUN_ID}.json"
    receipt.write_text("{}", encoding="utf-8")
    pointer = eim.pointer_for(_manifest(), generated_at="2026-09-23T00:20:00Z")
    eim.write_pointer(tmp_path / "latest.json", pointer, receipt)
    written = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert written["manifest"] == f"{RUN_ID}.json"
    assert written["run_id"] == RUN_ID
    assert (tmp_path / written["manifest"]).exists()
    for key in ("dataset", "repo_sha", "scenario", "generated_at"):
        assert key in written


def test_self_referencing_pointer_is_refused(tmp_path):
    pointer = eim.pointer_for(_manifest(), generated_at="x")
    pointer["manifest"] = "latest.json"
    with pytest.raises(eim.ExportError, match="pointeur autoréférent ou nom divergent"):
        eim.write_pointer(tmp_path / "latest.json", pointer, tmp_path / "x.json")
    assert not (tmp_path / "latest.json").exists()


def test_pointer_written_over_its_receipt_is_refused(tmp_path):
    pointer = eim.pointer_for(_manifest(), generated_at="x")
    receipt = tmp_path / f"{RUN_ID}.json"
    with pytest.raises(eim.ExportError, match="pointeur autoréférent ou nom divergent"):
        eim.write_pointer(receipt, pointer, receipt)


def test_receipt_name_diverging_from_run_id_is_refused(tmp_path):
    pointer = eim.pointer_for(_manifest(), generated_at="x")
    wrong = tmp_path / "wrong-name.json"
    with pytest.raises(eim.ExportError, match="pointeur autoréférent ou nom divergent"):
        eim.write_pointer(tmp_path / "latest.json", pointer, wrong)


def test_repo_sha_must_belong_to_the_git_history():
    eim.verify_repo_sha(_head_sha(), REPO_ROOT)
    with pytest.raises(eim.ExportError, match="repo_sha hors historique git"):
        eim.verify_repo_sha(UNKNOWN_SHA, REPO_ROOT)
    with pytest.raises(eim.ExportError, match="repo_sha hors historique git"):
        eim.verify_repo_sha("abc", REPO_ROOT)


# ------------------------------------------------------------- command line --


def _cli(tmp_path, out, *extra, run_id=RUN_ID, dataset="vacance"):
    return [
        "--dataset",
        dataset,
        "--run-id",
        run_id,
        "--repo-sha",
        _head_sha(),
        "--out",
        str(out),
        *extra,
    ]


def test_existing_receipt_is_refused_with_exit_3_and_left_untouched(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    out = tmp_path / f"{RUN_ID}.json"
    out.write_bytes(b'{"kept": true}\n')
    before = out.read_bytes()
    code = eim.main(_cli(tmp_path, out))
    assert code == eim.EXIT_RECEIPT_EXISTS == 3
    assert "reçu existant, écrasement refusé" in capsys.readouterr().err
    assert out.read_bytes() == before


@pytest.mark.parametrize(
    ("dataset", "run_id", "motif"),
    [
        ("inconnu", RUN_ID, "dataset inconnu"),
        ("vacance", "run-inexistant-00000000", "run_id inconnu"),
    ],
)
def test_other_errors_exit_2_with_their_own_message(
    tmp_path, capsys, monkeypatch, dataset, run_id, motif
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    out = tmp_path / f"{run_id}.json"
    code = eim.main(_cli(tmp_path, out, run_id=run_id, dataset=dataset))
    err = capsys.readouterr().err
    assert code == eim.EXIT_ERROR == 2
    assert motif in err
    assert "reçu existant" not in err
    assert not out.exists()


def test_unreachable_database_exits_2_without_the_dsn(tmp_path, capsys, monkeypatch):
    dsn = "postgresql://nobody:s3cr3t@127.0.0.1:1/none"
    monkeypatch.setenv("DATABASE_URL", dsn)
    attempts = []

    def unreachable(received_dsn, **kwargs):
        attempts.append(received_dsn)
        raise psycopg.OperationalError(f"Synthetic connection failure: {dsn}")

    monkeypatch.setattr(psycopg, "connect", unreachable)
    out = tmp_path / f"{RUN_ID}.json"
    code = eim.main(_cli(tmp_path, out))
    err = capsys.readouterr().err
    assert code == 2
    assert "base injoignable" in err
    assert attempts == [dsn]
    assert dsn not in err
    assert "s3cr3t" not in err
    assert not out.exists()


def test_invalid_option_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        eim.main(["--dataset", "vacance"])
    assert exc.value.code == 2


def test_self_referencing_receipt_name_is_refused_before_existence(tmp_path, capsys):
    # --out named latest.json exists: the name is refused (2), not "exists" (3).
    out = tmp_path / "latest.json"
    out.write_text("{}", encoding="utf-8")
    code = eim.main(_cli(tmp_path, out))
    err = capsys.readouterr().err
    assert code == 2
    assert "pointeur autoréférent ou nom divergent" in err


def _write_receipt(tmp_path, repo_sha):
    receipt = tmp_path / f"{RUN_ID}.json"
    text = eim.dumps_sorted(_manifest(repo_sha=repo_sha))
    receipt.write_text(text, encoding="utf-8")
    return receipt


def _pointer_cli(receipt, pointer):
    return [
        "--dataset",
        "vacance",
        "--run-id",
        RUN_ID,
        "--out",
        str(receipt),
        "--pointer",
        str(pointer),
        "--pointer-only",
    ]


def test_pointer_only_rebuilds_a_lost_pointer_without_touching_the_receipt(
    tmp_path, capsys
):
    receipt = _write_receipt(tmp_path, _head_sha())
    before = receipt.read_bytes()
    pointer = tmp_path / "latest.json"
    assert eim.main(_pointer_cli(receipt, pointer)) == 0
    written = json.loads(pointer.read_text(encoding="utf-8"))
    assert written["manifest"] == f"{RUN_ID}.json"
    assert written["repo_sha"] == _head_sha()
    assert written["scenario"] == "rejeu"
    assert receipt.read_bytes() == before


def test_pointer_only_refuses_a_missing_receipt_or_a_foreign_sha(tmp_path, capsys):
    pointer = tmp_path / "latest.json"
    missing = tmp_path / f"{RUN_ID}.json"
    assert eim.main(_pointer_cli(missing, pointer)) == 2
    assert "reçu absent" in capsys.readouterr().err
    receipt = _write_receipt(tmp_path, UNKNOWN_SHA)
    assert eim.main(_pointer_cli(receipt, pointer)) == 2
    assert "repo_sha hors historique git" in capsys.readouterr().err
    assert not pointer.exists()


def test_export_without_repo_sha_is_an_option_error(tmp_path, capsys):
    out = tmp_path / f"{RUN_ID}.json"
    argv = ["--dataset", "vacance", "--run-id", RUN_ID, "--out", str(out)]
    with pytest.raises(SystemExit) as exc:
        eim.main(argv)
    assert exc.value.code == 2
    assert "--repo-sha requis" in capsys.readouterr().err
    assert not out.exists()


# ----------------------------------------------------------- source archive --


def _archive_record(root, run_id=RUN_ID, **overrides):
    record = {
        "version": 1,
        "dataset": "vacance",
        "run_id": run_id,
        "origin": "telechargement",
        "replayed_from": None,
        "files": [
            {
                "relpath": "raw/lovac_communes.csv",
                "name": "lovac_communes.csv",
                "bytes": 10,
                "sha256": "ab",
                "object": "objects/ab/ab",
                "stored": True,
            }
        ],
    }
    record.update(overrides)
    path = root / "vacance" / "runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return path, record


def test_unconfigured_source_archive_is_declared():
    block = eim.source_archive_block("", "vacance", RUN_ID, _etl_run())
    assert block == "non_configuree"


def test_absent_archive_record_is_declared(tmp_path):
    block = eim.source_archive_block(str(tmp_path), "vacance", RUN_ID, _etl_run())
    assert block == "absente"


def test_archive_record_names_the_origin_of_the_sources(tmp_path):
    path, record = _archive_record(
        tmp_path, origin="archive", replayed_from="2-20260923T001821Z"
    )
    block = eim.source_archive_block(str(tmp_path), "vacance", RUN_ID, _etl_run())
    assert block == {
        "record": f"vacance/runs/{RUN_ID}.json",
        "record_sha256": eim.file_sha256(path),
        "origin": "archive",
        "replayed_from": "2-20260923T001821Z",
        "files": record["files"],
    }
    manifest = _manifest(source_archive=block)
    assert manifest["source_archive"] == block
    assert eim.dumps_sorted(manifest) == eim.dumps_sorted(
        _manifest(source_archive=block)
    )


def test_archive_record_diverging_from_etl_runs_is_refused(tmp_path):
    _archive_record(tmp_path)
    run = _etl_run(
        source_files=[{"name": "lovac_communes.csv", "bytes": 11, "sha256": "ab"}]
    )
    with pytest.raises(eim.ExportError, match="archive incohérente avec etl_runs"):
        eim.source_archive_block(str(tmp_path), "vacance", RUN_ID, run)


def test_an_extra_file_in_an_ordinary_record_is_still_refused(tmp_path):
    extra = {
        "relpath": "raw/notes.json",
        "name": "notes.json",
        "bytes": 3,
        "sha256": "cd",
    }
    _, record = _archive_record(tmp_path)
    path = tmp_path / "vacance" / "runs" / f"{RUN_ID}.json"
    path.chmod(0o644)
    path.write_text(
        json.dumps({**record, "files": [*record["files"], extra]}), encoding="utf-8"
    )
    with pytest.raises(eim.ExportError, match="archive incohérente avec etl_runs"):
        eim.source_archive_block(str(tmp_path), "vacance", RUN_ID, _etl_run())


# C23 (Ianis, 2026-10-08; WINDOWS n° 79): a DPE generation is archived with its
# manifest and the evidence it names (metadata, page proofs), while
# etl_runs.source_files journals the four CSV processing units only.
# Run 37-20261008T100627Z: 125 files archived, 4 journaled.

DPE_RUN_ID = "37-20261008T100627Z"
DPE_DIR = "/etl_tmp/datacommune_dpe/raw/gen"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dpe_generation(root, *, mutate_manifest=None):
    """Record files and journal of a DPE generation, as 04-15 archives them."""
    contents: dict[str, bytes] = {}
    units = []
    for slug, dep in [
        ("dpe03existant", "44"),
        ("dpe03existant", "85"),
        ("dpe02neuf", "44"),
        ("dpe02neuf", "85"),
    ]:
        stem = f"gen-{slug}-{dep}"
        csv, meta = f"{stem}.csv", b'{"version": "v"}'
        contents[csv] = f"numero_dpe\n{stem}\n".encode()
        contents[f"{stem}-start.json"] = meta
        contents[f"{stem}-end.json"] = meta  # same bytes twice: one object, two paths
        contents[f"{stem}-page-1.json"] = f"[{stem}]".encode()

        def artifact(name):
            return {"path": f"{DPE_DIR}/{name}", "sha256": _sha(contents[name])}

        units.append(
            {
                **artifact(csv),
                "source_slug": slug,
                "department": dep,
                "metadata_start": artifact(f"{stem}-start.json"),
                "metadata_end": artifact(f"{stem}-end.json"),
                "pages": [{**artifact(f"{stem}-page-1.json"), "rows": 1}],
                "partitions": [],
            }
        )
    manifest = {
        "import_id": "gen",
        "directory": DPE_DIR,
        "schema_version": 1,
        "files": units,
    }
    if mutate_manifest:
        mutate_manifest(manifest)
    contents["manifest.json"] = json.dumps(manifest).encode()
    base = root / "dpe"
    files = []
    for name, data in contents.items():
        sha = _sha(data)
        obj = base / "objects" / sha[:2] / sha
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(data)
        files.append(
            {
                "relpath": name,
                "name": name,
                "bytes": len(data),
                "sha256": sha,
                "object": f"objects/{sha[:2]}/{sha}",
                "stored": True,
            }
        )
    journal = [
        {
            "name": f["name"],
            "bytes": f["bytes"],
            "sha256": f["sha256"],
            "physical_lines": 1,
        }
        for f in files
        if f["name"].endswith(".csv")
    ]
    return files, journal


def _dpe_record(root, files):
    path = root / "dpe" / "runs" / f"{DPE_RUN_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "version": 1,
        "dataset": "dpe",
        "run_id": DPE_RUN_ID,
        "origin": "telechargement",
        "replayed_from": None,
        "files": files,
    }
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return path


def _dpe_block(root, journal):
    return eim.source_archive_block(
        str(root), "dpe", DPE_RUN_ID, _etl_run(dataset_name="dpe", source_files=journal)
    )


def test_dpe_record_with_its_generation_manifest_matches_the_journaled_csv(tmp_path):
    files, journal = _dpe_generation(tmp_path)
    path = _dpe_record(tmp_path, files)
    assert len(files) == 17 and len(journal) == 4
    block = _dpe_block(tmp_path, journal)
    assert block["record"] == f"dpe/runs/{DPE_RUN_ID}.json"
    assert block["record_sha256"] == eim.file_sha256(path)
    assert block["files"] == files


def test_dpe_record_with_a_file_its_manifest_does_not_name_is_refused(tmp_path):
    files, journal = _dpe_generation(tmp_path)
    extra = dict(files[1], relpath="notes.json", name="notes.json")
    _dpe_record(tmp_path, [*files, extra])
    with pytest.raises(eim.ExportError, match="manifeste de génération"):
        _dpe_block(tmp_path, journal)


def test_dpe_record_missing_a_proof_named_by_its_manifest_is_refused(tmp_path):
    files, journal = _dpe_generation(tmp_path)
    proof = "gen-dpe02neuf-85-page-1.json"
    _dpe_record(tmp_path, [f for f in files if f["name"] != proof])
    with pytest.raises(eim.ExportError, match="manifeste de génération"):
        _dpe_block(tmp_path, journal)


def test_dpe_proof_whose_digest_differs_from_its_manifest_is_refused(tmp_path):
    files, journal = _dpe_generation(tmp_path)
    proof = next(f for f in files if f["name"].endswith("-page-1.json"))
    proof["sha256"] = "0" * 64
    _dpe_record(tmp_path, files)
    with pytest.raises(eim.ExportError, match="manifeste de génération"):
        _dpe_block(tmp_path, journal)


def test_dpe_journaled_csv_absent_or_different_is_refused(tmp_path):
    files, journal = _dpe_generation(tmp_path)
    _dpe_record(tmp_path, files)
    with pytest.raises(eim.ExportError, match="archive incohérente avec etl_runs"):
        altered = dict(journal[0], bytes=journal[0]["bytes"] + 1)
        _dpe_block(tmp_path, [altered, *journal[1:]])
    with pytest.raises(eim.ExportError, match="archive incohérente avec etl_runs"):
        _dpe_block(tmp_path, journal[:3])


def test_dpe_altered_or_absent_archived_manifest_is_refused(tmp_path):
    files, journal = _dpe_generation(tmp_path)
    _dpe_record(tmp_path, files)
    manifest = next(f for f in files if f["relpath"] == "manifest.json")
    obj = tmp_path / "dpe" / manifest["object"]
    obj.write_bytes(obj.read_bytes() + b" ")
    with pytest.raises(eim.ExportError, match="manifeste de génération"):
        _dpe_block(tmp_path, journal)
    obj.unlink()
    with pytest.raises(eim.ExportError, match="manifeste de génération"):
        _dpe_block(tmp_path, journal)


def test_dpe_manifest_naming_a_file_outside_its_generation_is_refused(tmp_path):
    def outside(manifest):
        manifest["files"][0]["pages"][0]["path"] = "/etc/elsewhere/page.json"

    files, journal = _dpe_generation(tmp_path, mutate_manifest=outside)
    _dpe_record(tmp_path, files)
    with pytest.raises(eim.ExportError, match="manifeste de génération"):
        _dpe_block(tmp_path, journal)


def test_dpe_record_of_csv_only_keeps_the_strict_comparison(tmp_path):
    files, journal = _dpe_generation(tmp_path)
    csv_only = [f for f in files if f["name"].endswith(".csv")]
    _dpe_record(tmp_path, csv_only)
    assert _dpe_block(tmp_path, journal)["files"] == csv_only
    with pytest.raises(eim.ExportError, match="archive incohérente avec etl_runs"):
        _dpe_block(tmp_path, journal[:3])


def test_manifest_without_archive_block_declares_it_unconfigured():
    assert _manifest()["source_archive"] == "non_configuree"


# ------------------------------------------------------------ script receipts --

SCRIPT_RUN_ID = "20260924T220000Z"
SHA_A = "a" * 64


def test_source_file_lines_are_read_from_the_script_log():
    log = "\n".join(
        [
            "[22:00:01] Scope departements: 44,85",
            f"SOURCE_FILE departements.geojson 120 {SHA_A}",
            "SOURCE_FILE bad-line 12 nothex",
            f"SOURCE_FILE sections-85.json.gz 7 {SHA_A}",
            "",
        ]
    )
    assert eim.parse_source_files_log(log) == [
        {"name": "departements.geojson", "bytes": 120, "sha256": SHA_A},
        {"name": "sections-85.json.gz", "bytes": 7, "sha256": SHA_A},
    ]
    assert eim.parse_source_files_log("no source here") == []


def test_script_without_external_source_declares_it():
    assert (
        eim.script_source_archive("/x", "refresh-communes-lookup", SCRIPT_RUN_ID, [])
        == "sans_source_externe"
    )


def test_script_archive_is_checked_against_the_files_it_printed(tmp_path):
    files = [{"name": "departements.geojson", "bytes": 3, "sha256": SHA_A}]
    record = {"origin": "telechargement", "replayed_from": None, "files": files}
    path = tmp_path / "geo_boundaries" / "runs" / f"{SCRIPT_RUN_ID}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    block = eim.script_source_archive(
        str(tmp_path), "geo-boundaries", SCRIPT_RUN_ID, files
    )
    assert block["origin"] == "telechargement"
    assert block["record"] == f"geo_boundaries/runs/{SCRIPT_RUN_ID}.json"
    with pytest.raises(eim.ExportError, match="archive incohérente"):
        eim.script_source_archive(
            str(tmp_path),
            "geo-boundaries",
            SCRIPT_RUN_ID,
            [{"name": "departements.geojson", "bytes": 4, "sha256": SHA_A}],
        )


def test_script_receipt_carries_the_identity_keys_of_a_pipeline_receipt():
    manifest = eim.build_script_manifest(
        run_id=SCRIPT_RUN_ID,
        name="geo-boundaries",
        status="success",
        duration_s=12.5,
        tables={},
        repo_sha="b" * 40,
        gabarit=None,
        disk_peak=eim.disk_peak_block(10, "rootfs", 30, baseline_bytes=9, samples=2),
        scenario="initial",
        sources_locales="absentes",
        source_files=[],
        source_archive="absente",
        command=["python", "-m", "scripts.import_geo_boundaries"],
        exported_at="2026-09-24T22:01:00Z",
    )
    for key in ("run_id", "dataset", "repo_sha", "status", "duration_s"):
        assert key in manifest
    assert manifest["dataset"] == "geo-boundaries"
    assert manifest["disk_peak"]["bytes"] == 10 and manifest["scenario"] == "initial"
    pointer = eim.pointer_for(manifest, generated_at="2026-09-24T22:01:00Z")
    assert pointer["manifest"] == f"{SCRIPT_RUN_ID}.json"


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--dataset", "vacance"], "script inconnu"),
        (["--dataset", "geo-boundaries", "--run-id", RUN_ID], "run_id inconnu"),
    ],
)
def test_script_mode_refuses_unknown_names_before_the_database(
    tmp_path, capsys, argv, message
):
    args = {"--dataset": "geo-boundaries", "--run-id": SCRIPT_RUN_ID}
    args.update(dict(zip(argv[::2], argv[1::2], strict=True)))
    flat = [x for kv in args.items() for x in kv]
    rc = eim.main(
        [
            *flat,
            "--script",
            "--repo-sha",
            _head_sha(),
            "--out",
            str(tmp_path / f"{args['--run-id']}.json"),
        ]
    )
    assert rc == eim.EXIT_ERROR
    assert message in capsys.readouterr().err
