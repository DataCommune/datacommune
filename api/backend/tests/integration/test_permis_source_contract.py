"""COR-07: real migrations, COPY/swap and HTTP, disposable PostGIS only."""

import csv
import hashlib
import importlib.util
import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from app.config import settings
from app.etl_permis.pipeline import PermisPipeline
from tests.integration.instance_scope_fixture import install_instance_scope


def apply_migration(conn, name, direction="upgrade"):
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Run the actual migration SQL with the isolated schema connection.
    module.op = SimpleNamespace(execute=conn.execute)
    getattr(module, direction)()


@pytest.fixture()
def published(isolated_pg_dsn, monkeypatch, tmp_path):
    # Production validation explicitly qualifies public. Own the whole child DB
    # rather than redirecting only unqualified SQL through search_path.
    pg_dsn = isolated_pg_dsn
    monkeypatch.setattr(settings, "database_url", pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        apply_migration(conn, "032_permis.py")
        apply_migration(conn, "049_etl_old_tables.py")
        conn.execute(
            "INSERT INTO permis(numero_dossier, annee, date_depot, code_commune) "
            "VALUES ('COR07-LEGACY', 2024, '2025-03-15', '93999')"
        )
        apply_migration(conn, "093_permis_source_dates.py")
        apply_migration(conn, "094_publication_generations.py")
        apply_migration(conn, "109_permis_dossier_destination_key.py")
        apply_migration(conn, "054_validation_runs.py")
        legacy = conn.execute(
            "SELECT date_depot, date_commencement, source_sha256 FROM permis"
        ).fetchone()
        assert legacy == (date(2025, 3, 15), None, None)
        # /v1/permis reads the scope first (DATA-03): host permis where the
        # route is exercised (44, 85) and where the source lives (93).
        install_instance_scope(conn, ["permis"], ("44", "85", "93"))

    monkeypatch.setattr("app.etl_permis.pipeline.get_download_dir", lambda _: tmp_path)
    pipeline = PermisPipeline()
    monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: pg_dsn)
    yield pipeline, pg_dsn


def write_source(tmp_path, contradictory=False, first=None, **second):
    row = dict(
        nom_dossier="COR07-SYNTHETIC",
        type_dossier="PC",
        date_commencement="2025-03-15",
        date_decision="2024-12-01",
        n_commune="93999",
        nom_commune="Commune test",
        departement="93",
        nature_construction="Habitation",
        nb_logements="2",
        sdp_creee="100",
        designation="Logement",
        annee="2024",
    )
    row.update(first or {})
    path = tmp_path / "source.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        if contradictory:
            second = {"nb_logements": "3", **second}
        writer.writerow({**row, **second})
    return path


def test_correct_dates_and_provenance_real_swap(published, tmp_path):
    pipeline, dsn = published
    source = write_source(tmp_path)
    for _ in range(2):
        assert pipeline.stage(pipeline.transform(source)) == 2
        pipeline.swap()
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "SELECT date_depot, date_commencement, date_decision, "
                "source_sha256, nb_logements_crees FROM permis"
            ).fetchall() == [
                (
                    None,
                    date(2025, 3, 15),
                    date(2024, 12, 1),
                    hashlib.sha256(source.read_bytes()).hexdigest(),
                    2,
                )
            ]
            assert (
                conn.execute(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid='permis'::regclass AND contype IN ('p','u')"
                ).fetchone()[0]
                == 2
            )
            assert (
                conn.execute(
                    "SELECT relpersistence FROM pg_class WHERE oid='permis'::regclass"
                ).fetchone()[0]
                == "p"
            )


def test_conflicting_duplicate_preserves_legacy(published, tmp_path):
    pipeline, dsn = published
    with psycopg.connect(dsn) as conn:
        before = conn.execute("SELECT to_jsonb(p) FROM permis p").fetchall()
    pipeline.stage(pipeline.transform(write_source(tmp_path, contradictory=True)))
    with pytest.raises(psycopg.errors.UniqueViolation):
        pipeline.swap()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT to_jsonb(p) FROM permis p").fetchall() == before


def test_one_dossier_with_two_destinations_publishes_both_rows(published, tmp_path):
    # Shape of PC 093 031 22 A0015 in the frozen source (run 77): one row per
    # destination, each with its own floor area.
    pipeline, dsn = published
    source = write_source(tmp_path, designation="Hébergement", sdp_creee="250")
    assert pipeline.stage(pipeline.transform(source)) == 2
    pipeline.swap()
    with psycopg.connect(dsn) as conn:
        assert conn.execute(
            "SELECT destination, surface_plancher FROM permis ORDER BY destination"
        ).fetchall() == [("Hébergement", 250), ("Logement", 100)]


def test_conflicting_duplicate_without_destination_is_refused(published, tmp_path):
    # NULLS NOT DISTINCT: a missing destination never lets two versions in.
    pipeline, dsn = published
    with psycopg.connect(dsn) as conn:
        before = conn.execute("SELECT to_jsonb(p) FROM permis p").fetchall()
    source = write_source(tmp_path, True, {"designation": ""}, designation="")
    pipeline.stage(pipeline.transform(source))
    with pytest.raises(psycopg.errors.UniqueViolation):
        pipeline.swap()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT to_jsonb(p) FROM permis p").fetchall() == before


@pytest.mark.parametrize("code", ["44999", "85999", "93999"])
async def test_real_endpoint_quarantines_old_and_new_stock(
    published, tmp_path, client, code, monkeypatch
):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import get_db
    from app.main import app
    from app.middleware import settings

    pipeline, dsn = published
    # Use the existing smoke-header seam to omit unrelated asynchronous
    # request-log writes. This route's DB dependency stays real and local.
    monkeypatch.setattr(settings, "smoke_test_api_key", "cor07-local-fixture")
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    sessions = async_sessionmaker(engine)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    try:
        for generation in ("legacy", "corrected"):
            response = await client.get(
                "/v1/permis",
                params={"code_insee": code},
                headers={"X-Smoke-Test": "cor07-local-fixture"},
            )
            assert response.status_code == 503
            body = response.json()
            assert body["statut"] == "indisponible"
            assert body["donnees"] == []
            assert "ne signifie pas absence" in body["raison"]
            if evidence := os.environ.get("COR07_EVIDENCE_DIR"):
                Path(evidence, "cor07-api-fixture.json").write_text(
                    json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            if generation == "legacy":
                pipeline.stage(pipeline.transform(write_source(tmp_path)))
                pipeline.swap()
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.parametrize(
    "source_kind",
    ["redacted"] + (["private"] if os.environ.get("COR07_FROZEN_SOURCE") else []),
)
def test_frozen_audit_generation(published, source_kind):
    fixture_dir = Path(__file__).parents[1] / "fixtures" / "permis"
    manifest = json.loads(
        (fixture_dir / "audit-generation-manifest.json").read_text(encoding="utf-8")
    )
    public_source = fixture_dir / "audit-generation-redacted.csv"
    source = public_source
    expected_hash = manifest["redacted_sha256"]
    if source_kind == "private":
        source = Path(os.environ["COR07_FROZEN_SOURCE"])
        expected_hash = manifest["original_sha256"]
        spec = importlib.util.spec_from_file_location(
            "cor07_redaction", fixture_dir / "prepare_frozen_fixture.py"
        )
        preparation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preparation)
        regenerated, regenerated_manifest = preparation.redact(source)
        assert regenerated == public_source.read_bytes(), "Redaction changed"
        assert regenerated_manifest == manifest
    assert manifest["original_sha256"] == (
        "9bcda940fa5b9449932a00ecec5308822144d57547e6e679722c01d3833e4881"
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_hash
    originals = list(csv.DictReader(source.open(encoding="utf-8-sig")))
    assert len(originals) == 753
    assert len({row["nom_dossier"] for row in originals}) == 752
    preserved_stream = json.dumps(
        [
            [row[column] for column in manifest["preserved_columns"]]
            for row in originals
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert (
        hashlib.sha256(preserved_stream).hexdigest()
        == manifest["preserved_values_sha256"]
    )
    if source_kind == "redacted":
        assert set(originals[0]) == set(
            manifest["preserved_columns"] + manifest["tokenized_columns"]
        )
        assert all(
            row[column].startswith(f"COR07-{column}-")
            for row in originals
            for column in manifest["tokenized_columns"]
            if column != "n_commune"
        ), "Redacted fixture contains an unmasked categorical identity"
    pipeline, dsn = published
    transformed = pipeline.transform(source)
    transformed_rows = list(csv.DictReader(transformed.open(encoding="utf-8")))
    mismatches = sum(
        r["date_depot"] != ""
        or r["date_commencement"]
        != ("" if src["date_commencement"] == "N/C" else src["date_commencement"][:10])
        or r["date_decision"] != src["date_decision"]
        or r["source_sha256"] != expected_hash
        for src, r in zip(originals, transformed_rows, strict=True)
    )
    assert mismatches == 0, "Independent frozen source/transform comparison failed"
    from collections import defaultdict

    groups = defaultdict(list)
    for row in transformed_rows:
        groups[row["numero_dossier"]].append(row)
    conflicts = [
        {
            "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
            "differing_columns": [
                col for col in rows[0] if len({r[col] for r in rows}) > 1
            ],
        }
        for key, rows in groups.items()
        if len(rows) > 1
    ]
    assert len(conflicts) == manifest["expected_duplicate_groups"]
    assert (
        conflicts[0]["differing_columns"]
        == manifest["expected_differing_transformed_columns"]
    )
    assert pipeline.stage(transformed) == 753
    try:
        pipeline.swap()
        outcome = "published"
    except psycopg.errors.UniqueViolation:
        outcome = "conflicting_duplicates_refused"
    assert outcome == manifest["expected_outcome"]
    with psycopg.connect(dsn) as conn:
        if outcome == "published":
            rows = conn.execute(
                "SELECT numero_dossier, date_depot, date_commencement, "
                "date_decision, source_sha256 FROM permis"
            ).fetchall()
            mismatches = sum(
                not any(
                    r[0] == s["nom_dossier"]
                    and r[1] is None
                    and (r[2].isoformat() if r[2] else None)
                    == (
                        None
                        if s["date_commencement"] == "N/C"
                        else s["date_commencement"][:10]
                    )
                    and r[3].isoformat() == s["date_decision"]
                    and r[4] == expected_hash
                    for s in originals
                )
                for r in rows
            )
            assert mismatches == 0, "Independent source/SQL date comparison failed"
            # One row per (dossier, destination): the dossier published with
            # two destinations keeps both, each with its own floor area.
            assert len(rows) == 753
            split = conn.execute(
                "SELECT count(DISTINCT destination), count(DISTINCT surface_plancher) "
                "FROM permis GROUP BY numero_dossier HAVING count(*) > 1"
            ).fetchall()
            assert split == [(2, 2)]
        else:
            assert (
                conn.execute(
                    "SELECT count(*) FROM permis WHERE numero_dossier='COR07-LEGACY'"
                ).fetchone()[0]
                == 1
            )
    if evidence := os.environ.get("COR07_EVIDENCE_DIR"):
        filename = (
            "cor07-source-chain-redacted.json"
            if source_kind == "redacted"
            else "cor07-source-chain.json"
        )
        Path(evidence, filename).write_text(
            json.dumps(
                {
                    "source_sha256": expected_hash,
                    "bytes": source.stat().st_size,
                    "source_rows": len(originals),
                    "distinct_dossiers": 752,
                    "observed_departments": ["93"],
                    "outcome": outcome,
                    "source_transform_date_mismatches": mismatches,
                    "duplicate_groups": conflicts,
                    "sql_dates_compared": outcome == "published",
                    "raw_data_versioned": False,
                    "source_kind": source_kind,
                    "original_source_sha256": manifest["original_sha256"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )


@pytest.mark.parametrize("checkpoint_state", ["absent", "old", "current"])
def test_runner_restarts_old_flat_and_current_contract(
    published, tmp_path, monkeypatch, checkpoint_state, full_scope_loader
):
    from unittest.mock import Mock

    from app.config import settings
    from app.etl.checkpoint import CheckpointManager
    from app.etl.hooks import DependentMatviewHook, ValidationHook
    from app.etl.runner import PipelineRunner
    from app.etl_permis.config import OUTPUT_COLUMNS

    pipeline, dsn = published
    pipeline.bind_scope(full_scope_loader(dsn))
    source = write_source(tmp_path)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    checkpoint = CheckpointManager("permis", "permis_staging")
    if checkpoint_state == "current":
        pipeline.create_staging_table(dsn)
        pipeline.stage(pipeline.transform(source))
        key = pipeline.checkpoint_scope_key
        checkpoint.save(
            [source.name],
            scope_key=key,
            identity_key=pipeline.checkpoint_identity(key),
            source_files=PipelineRunner._fingerprint_sources(pipeline, [source]),
            staging_rows=PipelineRunner._count_staging_rows(dsn, pipeline),
        )
    else:
        old_columns = [
            c for c in OUTPUT_COLUMNS if c not in {"date_commencement", "source_sha256"}
        ]
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "CREATE TABLE _permis_staging_flat ("
                + ",".join(f"{c} TEXT" for c in old_columns)
                + ")"
            )
            conn.execute("CREATE TABLE permis_staging (LIKE permis INCLUDING ALL)")
            conn.execute(
                "INSERT INTO _permis_staging_flat(numero_dossier, annee, date_depot) "
                "VALUES ('COR07-STALE', '2024', '2000-01-01')"
            )
        if checkpoint_state == "old":
            checkpoint.path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.path.write_bytes(
                json.dumps(
                    {
                        "dataset_name": "permis",
                        "staging_table": "permis_staging",
                        "scope_key": "",
                        "loaded_files": [source.name],
                    }
                ).encode()
            )
    runner = PipelineRunner([ValidationHook(), DependentMatviewHook()])
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "CREATE TABLE dataset_metadata(dataset_name TEXT PRIMARY KEY, "
            "row_count BIGINT, status TEXT, last_updated_at TIMESTAMPTZ, "
            "last_import_duration_seconds DOUBLE PRECISION, source_period_label TEXT, "
            "source_updated_at TIMESTAMPTZ, effective_departments TEXT[])"
        )
        conn.execute(
            "INSERT INTO dataset_metadata(dataset_name,row_count) VALUES ('permis', 1)"
        )
    # Keep real checkpoint, stage, COPY, swap, constraints, advisory lock and
    # safety gates. Only unrelated operational telemetry/disk probes are stubbed.
    for name in (
        "_insert_etl_run",
        "_update_etl_run",
        "_check_preflight_disk",
    ):
        monkeypatch.setattr(runner, name, Mock(return_value=None))

    def exists(_dsn, table):
        with psycopg.connect(_dsn) as conn:
            return conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL", (table,)
            ).fetchone()[0]

    monkeypatch.setattr(runner, "_staging_table_exists", exists)
    monkeypatch.setattr("app.etl.runner.check_disk_space", lambda *_: None)
    monkeypatch.setattr("app.etl.runner.cleanup_download_dir", lambda *_: None)
    monkeypatch.setattr(pipeline, "download", lambda: [source])
    result = runner.run(pipeline)
    assert result.success, result.error
    assert result.total_rows_loaded == 2
    assert pipeline.checkpoint_scope_key != ""
    assert not checkpoint.path.exists()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM permis").fetchone()[0] == 1
        assert conn.execute(
            "SELECT status,row_count FROM etl_publications ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("published", 1)
        assert conn.execute(
            "SELECT status,row_count FROM dataset_metadata WHERE dataset_name='permis'"
        ).fetchone() == ("disponible", 1)
        assert conn.execute(
            "SELECT passed,row_count_after FROM validation_runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone() == (True, 1)
        assert (
            conn.execute(
                "SELECT count(*) FROM permis "
                "WHERE date_depot IS NULL AND source_sha256 IS NOT NULL"
            ).fetchone()[0]
            == 1
        )


def test_real_permis_dry_run_keeps_old_flat_and_checkpoint(
    published, tmp_path, monkeypatch
):
    from unittest.mock import Mock

    from app.config import settings
    from app.etl.checkpoint import CheckpointManager
    from app.etl.runner import PipelineRunner

    pipeline, dsn = published
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE TABLE _permis_staging_flat (legacy TEXT)")
        conn.execute("INSERT INTO _permis_staging_flat VALUES ('retained')")
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    checkpoint = CheckpointManager("permis", "permis_staging")
    checkpoint.path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.path.write_bytes(
        json.dumps(
            {
                "dataset_name": "permis",
                "staging_table": "permis_staging",
                "scope_key": "",
                "loaded_files": ["source.csv"],
            }
        ).encode()
    )
    before = checkpoint.path.read_bytes()
    source = write_source(tmp_path)
    monkeypatch.setattr(pipeline, "download", lambda: [source])
    monkeypatch.setattr("app.etl.runner.check_disk_space", lambda *_: None)
    spy = Mock(wraps=psycopg.connect)
    with monkeypatch.context() as scoped:
        scoped.setattr(psycopg, "connect", spy)
        assert PipelineRunner([]).run(pipeline, dry_run=True).success
    spy.assert_not_called()
    assert checkpoint.path.read_bytes() == before
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT * FROM _permis_staging_flat").fetchall() == [
            ("retained",)
        ]
