"""Real DPE CLI archive replay, native SQL/hooks, synthetic eight-document floor."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl import source_archive as archive
from app.etl.checkpoint import CheckpointManager
from app.etl.download import get_download_dir
from app.etl.runner import PipelineRunner
from app.etl_dpe.pipeline import DpePipeline
from app.scope.model import InstanceScope
from tests.integration.test_frozen_source_cli import CLI_BOOTSTRAP
from tests.integration.test_publication_restore import _command, _owned_container
from tests.test_etl_dpe_sources import install_source_api

pytestmark = pytest.mark.destructive
BACKEND = Path(__file__).resolve().parents[2]
BOOTSTRAP = (
    "from app.etl_dpe.pipeline import DpePipeline\n"
    "DpePipeline.min_staging_rows_final = property(lambda self: 1)\n" + CLI_BOOTSTRAP
)


def documentary_snapshot(dsn):
    """Observe every stored field, with exact geometry; exclude only sequence ID."""
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT (to_jsonb(d)-'id'-'position') || "
            "jsonb_build_object('position_ewkb',encode(ST_AsEWKB(position),'hex')) "
            "FROM diagnostics_dpe d ORDER BY numero_dpe"
        ).fetchall()


def test_cli_replay_after_cache_purge_preserves_complete_documentary_generation(
    isolated_pg_dsn, instance_scope, monkeypatch, tmp_path
):
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    monkeypatch.setenv("DATABASE_URL", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    command.upgrade(cfg, "head")
    instance_scope(isolated_pg_dsn, ["dpe"], ["44", "85"])
    calls = install_source_api(monkeypatch, tmp_path / "initial-cache")
    pipeline = DpePipeline()
    pipeline.bind_scope(InstanceScope(frozenset(["dpe"]), ("44", "85")))
    try:
        units = pipeline.download()
        manifest = pipeline.import_manifest
        manifest_path = tmp_path / "initial-cache" / "datacommune_dpe" / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        originals = {"manifest.json": manifest_bytes}
        for entry in manifest.files:
            for path in (
                Path(entry.path),
                *(
                    Path(item.path)
                    for item in (entry.metadata_start, entry.metadata_end, *entry.pages)
                ),
            ):
                originals[path.relative_to(Path(manifest.directory)).as_posix()] = (
                    path.read_bytes()
                )
    finally:
        pipeline.close()
    assert len(units) == 4 and len(originals) == 21
    root = tmp_path / "archive"
    root.mkdir()
    (root / archive.SENTINEL_NAME).write_text(
        archive.SENTINEL_CONTENT, encoding="utf-8"
    )
    audit = []
    container = _owned_container(isolated_pg_dsn, audit)
    free = int(
        _command(
            [
                "docker",
                "exec",
                container,
                "df",
                "-B1",
                "--output=avail",
                "/var/lib/postgresql/data",
            ],
            audit,
        )
        .decode()
        .splitlines()[-1]
    )
    env = os.environ | {
        "DATABASE_URL": isolated_pg_dsn.replace(
            "postgresql://", "postgresql+asyncpg://"
        ),
        "ETL_TEMP_DIR": str(tmp_path / "initial-cache"),
        "SOURCE_ARCHIVE_DIR": str(root),
        "SOURCE_ARCHIVE_MIN_FREE_BYTES": "0",
        "DEBUG": "true",
        "ALERT_URLS": "[]",
        "FROZEN_TEST_PG_FREE": str(free),
        "PYTHONPATH": str(BACKEND),
    }

    def run_cli(name, *arguments, expected_exit=0):
        completed = subprocess.run(
            [sys.executable, "-c", BOOTSTRAP, "dpe", *arguments],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            timeout=180,
        )
        (tmp_path / f"{name}.stdout").write_bytes(completed.stdout)
        (tmp_path / f"{name}.stderr").write_bytes(completed.stderr)
        assert completed.returncode == expected_exit, completed.stderr.decode(
            errors="replace"
        )[-7000:]
        return completed

    calls.clear()
    run_cli("complete", "--dpe-manifest", str(manifest_path))
    assert not manifest_path.parent.exists()
    complete = documentary_snapshot(isolated_pg_dsn)
    assert len(complete) == 8
    expected_numbers = {
        f"fixture-{slug}-{dept}-{page}"
        for slug in ("dpe03existant", "dpe02neuf")
        for dept in ("44", "85")
        for page in (0, 1)
    }
    assert {row[0]["numero_dpe"] for row in complete} == expected_numbers
    expected_observations = {
        f"fixture-{entry.source_slug}-{entry.department}-{page}": [
            {
                "source_slug": entry.source_slug,
                "department": entry.department,
                "source_version": entry.version_start,
                "observed_at": entry.observed_at.replace("+00:00", "Z"),
                "last_seen_at": entry.observed_at.replace("+00:00", "Z"),
                "state": "present",
                "import_id": manifest.import_id,
            }
        ]
        for entry in manifest.files
        for page in (0, 1)
    }
    for row in complete:
        document = row[0]
        assert document["source_state"] == "present"
        assert document["is_tombstone"] is False
        assert document["position_ewkb"] is not None
        assert (
            document["source_observations"]
            == expected_observations[document["numero_dpe"]]
        )
    first_records = list((root / "dpe" / "runs").glob("*.json"))
    assert len(first_records) == 1
    first = archive.load_replay(root, "dpe", first_records[0].stem)
    first_paths = first.materialize(tmp_path / "byte-observer")
    assert {
        path.relative_to(tmp_path / "byte-observer").as_posix(): path.read_bytes()
        for path in first_paths
    } == originals
    # Seed a clean partial candidate through native transform/COPY and checkpoint v2.
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("TRUNCATE diagnostics_dpe")
    resume_cache = tmp_path / "resume-cache"
    monkeypatch.setattr(settings, "etl_temp_dir", str(resume_cache))
    partial = DpePipeline()
    partial.bind_scope(InstanceScope(frozenset(["dpe"]), ("44", "85")))
    try:
        partial_files = partial.restore_source_inputs(first, get_download_dir("dpe"))
        partial.create_staging_table(isolated_pg_dsn)
        PipelineRunner()._process_file(
            partial, partial_files[0], [], logging.getLogger(__name__)
        )
        stock = PipelineRunner._count_staging_rows(isolated_pg_dsn, partial)
        assert stock == 2
        key = partial.checkpoint_scope_key
        checkpoint_manager = CheckpointManager("dpe", partial.staging_table_name)
        checkpoint_manager.save(
            [partial_files[0].name],
            scope_key=key,
            identity_key=partial.checkpoint_identity(key),
            source_files=PipelineRunner._fingerprint_sources(partial, partial_files),
            staging_rows=stock,
        )
        assert checkpoint_manager.load()["pending"] is None
    finally:
        partial.close()
    assert all(not path.exists() for path in partial_files)
    env["ETL_TEMP_DIR"] = str(resume_cache)
    resumed = run_cli("resume", "--from-archive", first.run_id)
    assert b"RESUME: skipping already-loaded file" in resumed.stderr
    assert documentary_snapshot(isolated_pg_dsn) == complete
    assert not checkpoint_manager.path.exists()
    with psycopg.connect(isolated_pg_dsn) as conn:
        # The second candidate starts from the same empty document history.
        conn.execute("TRUNCATE diagnostics_dpe")
    env["ETL_TEMP_DIR"] = str(tmp_path / "relocated-cache")
    run_cli("replay", "--from-archive", first.run_id)
    assert documentary_snapshot(isolated_pg_dsn) == complete
    assert not (tmp_path / "relocated-cache" / "datacommune_dpe").exists()
    records = [
        archive.load_replay(root, "dpe", path.stem)
        for path in (root / "dpe" / "runs").glob("*.json")
    ]
    second = next(item for item in records if item.run_id != first.run_id)
    assert second.record["replayed_from"] == first.run_id
    second_paths = second.materialize(tmp_path / "second-byte-observer")
    assert {
        path.relative_to(
            tmp_path / "second-byte-observer"
        ).as_posix(): path.read_bytes()
        for path in second_paths
    } == originals
    with psycopg.connect(isolated_pg_dsn) as conn:
        assert conn.execute(
            "SELECT status,row_count FROM etl_publications WHERE dataset_name='dpe'"
        ).fetchall() == [("published", 8), ("published", 8), ("published", 8)]
        assert conn.execute(
            "SELECT count(*) FROM validation_runs WHERE dataset_name='dpe' AND passed"
        ).fetchone() == (3,)
        assert conn.execute(
            "SELECT last_refresh_status FROM matview_metadata "
            "WHERE matview_name='v_commune_synthese'"
        ).fetchone() == ("success",)
    # An intact legacy CSV-only record must stop before touching either candidate.
    legacy_id = "999-20261005T100000Z"
    archive.archive_sources(
        root,
        "dpe",
        legacy_id,
        [path for path in second_paths if path.suffix == ".csv"],
        tmp_path / "second-byte-observer",
        "telechargement",
    )
    rejected_cache = tmp_path / "refused-cache" / "datacommune_dpe"
    rejected_cache.mkdir(parents=True)
    checkpoint = rejected_cache / "checkpoint.json"
    checkpoint.write_bytes(b'{"fixture":"must-remain-unread-before-restore"}')
    checkpoint_bytes = checkpoint.read_bytes()
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("CREATE TABLE diagnostics_dpe_staging(canary integer)")
        conn.execute("INSERT INTO diagnostics_dpe_staging VALUES (917)")
        candidate_oid = conn.execute(
            "SELECT 'diagnostics_dpe_staging'::regclass::oid"
        ).fetchone()[0]
        journal_rows = conn.execute("SELECT count(*) FROM etl_runs").fetchone()[0]
    env["ETL_TEMP_DIR"] = str(rejected_cache.parent)
    refused = run_cli("refused", "--from-archive", legacy_id, expected_exit=1)
    assert b"manifest.json" in refused.stderr
    assert documentary_snapshot(isolated_pg_dsn) == complete
    assert checkpoint.read_bytes() == checkpoint_bytes
    with psycopg.connect(isolated_pg_dsn) as conn:
        assert conn.execute(
            "SELECT 'diagnostics_dpe_staging'::regclass::oid"
        ).fetchone() == (candidate_oid,)
        assert conn.execute("SELECT * FROM diagnostics_dpe_staging").fetchall() == [
            (917,)
        ]
        assert conn.execute("SELECT count(*) FROM etl_runs").fetchone() == (
            journal_rows,
        )
    assert not calls
    if directory := os.environ.get("DC_DPE_ARCHIVE_PROOF_DIR"):
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        for name in ("complete", "replay", "resume", "refused"):
            for stream in ("stdout", "stderr"):
                (output / f"cli-{name}.{stream}").write_bytes(
                    (tmp_path / f"{name}.{stream}").read_bytes()
                )
        (output / "documentary-snapshot.json").write_text(
            json.dumps(complete, indent=2), encoding="utf-8"
        )
        (output / "result.json").write_text(
            json.dumps(
                {
                    "documents": 8,
                    "generation": manifest.import_id,
                    "logical_artifacts": 21,
                    "first_run": first.run_id,
                    "second_run": second.run_id,
                    "source_versions_and_dates_verified": True,
                    "native_checkpoint_v2_resume": {
                        "stock_before": 2,
                        "stock_after": 8,
                        "csv_skipped": 1,
                    },
                    "csv_only_refusal_preserves_existing_state": True,
                    "substitutions": [
                        "synthetic min_staging_rows_final=1",
                        "Windows filesystem capacity observation",
                        "HTTP forbidden in all CLI processes",
                    ],
                    "comparison": (
                        "all stored fields except sequence ID; exact EWKB geometry; "
                        "exact source/lifecycle JSON; manifest and CSV/JSON bytes"
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
