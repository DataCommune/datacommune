"""Candidate equality across durable SQL interruptions on owned PostGIS only.

Publication itself is covered by its separate transaction suites. These cases
keep the runner's source admission, staging, filtering and quality gates real.
"""

import csv
import json
import os
import time
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock

import psycopg
import pytest

from app.config import settings
from app.etl.base import BasePipeline
from app.etl.checkpoint import CheckpointManager
from app.etl.runner import PipelineRunner
from app.etl.schemas import EtlResult
from app.scope import InstanceScope

pytestmark = pytest.mark.destructive

# A repeated source fact is legitimate: replay must neither add nor erase it.
ROWS_A = [
    ("44", 2024, "x", "generation-A", "same"),
    ("44", 2024, "x", "generation-A", "same"),
    ("44", 2024, "y", "generation-A", "other"),
    ("85", 2024, "z", "generation-A", "outside"),
]
ROWS_B = [
    ("44", 2025, "p", "generation-A", "new"),
    ("44", 2025, "q", "generation-A", "new"),
    ("85", 2025, "r", "generation-A", "other"),
    ("85", 2025, "s", "generation-A", "other"),
]


class SimulatedCrash(BaseException):
    """Bypass ordinary per-file error handling exactly like process death."""


class SqlPipeline(BasePipeline):
    dataset_name = "test_ds"
    expected_columns: ClassVar = ["row_key"]
    staging_table_name = "test_ds_staging"
    production_table_name = "test_ds"
    has_department_partitions = False
    skip_runner_schema_drift_check = True
    min_staging_rows = 2
    min_success_ratio = 1.0
    scope_filter_sql: ClassVar = {"test_ds_staging": "code_departement"}
    # Frozen/current national pipelines can keep a specialized source key
    # unchanged across territories. Only the common identity binds their scope.
    checkpoint_scope_key = "synthetic-national-source-v1"

    def __init__(self, dsn, files):
        super().__init__()
        self.dsn = dsn
        self.files = files
        self.creations = 0
        self.staged = []

    def download(self):
        return self.files

    def validate_schema(self, file_path):
        json.loads(file_path.read_bytes())

    def transform(self, file_path):
        return file_path

    def create_staging_table(self, dsn):
        self.creations += 1
        with psycopg.connect(dsn) as conn:
            conn.execute("DROP TABLE IF EXISTS test_ds_staging")
            conn.execute("CREATE UNLOGGED TABLE test_ds_staging (LIKE test_ds)")

    def stage(self, file_path):
        rows = json.loads(file_path.read_bytes())
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO test_ds_staging VALUES (%s,%s,%s,%s,%s)", rows
            )
            conn.commit()
        self.staged.append(file_path)
        return len(rows)

    def swap(self):
        raise AssertionError("Candidate-only tests must not publish")

    def checkpoint_file_id(self, file_path):
        # Exercise the real DVF identity rule on annual departmental paths.
        from app.etl_dvf.pipeline import DvfPipeline

        return DvfPipeline().checkpoint_file_id(file_path)


def snapshot(dsn):
    with psycopg.connect(dsn) as observer:
        rows = observer.execute(
            "SELECT code_departement,annee,row_key,source_generation,payload,count(*) "
            "FROM test_ds_staging GROUP BY 1,2,3,4,5"
        ).fetchall()
    return Counter({tuple(row[:5]): row[5] for row in rows})


@pytest.fixture
def candidate(isolated_pg_dsn, tmp_path, monkeypatch, host_test_datasets):
    host_test_datasets("test_ds")
    dsn = isolated_pg_dsn
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "source_archive_dir", "")
    monkeypatch.setattr(BasePipeline, "get_sync_dsn", staticmethod(lambda: dsn))
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS test_ds_staging, test_ds")
        conn.execute(
            "CREATE TABLE test_ds (code_departement text,annee int,row_key text,"
            "source_generation text,payload text)"
        )
    files = [tmp_path / "sources" / str(year) / "44.csv" for year in (2024, 2025)]
    for path, rows in zip(files, (ROWS_A, ROWS_B), strict=True):
        path.parent.mkdir(parents=True)
        path.write_bytes(json.dumps(rows).encode())
    pipeline = SqlPipeline(dsn, files)
    scope = ["44", "85"]
    runner = PipelineRunner(
        scope_loader=lambda _dsn: InstanceScope(frozenset(["test_ds"]), tuple(scope))
    )
    # Only unrelated journals, archives and publication are replaced. COUNT,
    # checkpoint disk IO, COPY-like SQL commits, coverage/filter and gates run.
    monkeypatch.setattr("app.etl.runner.check_disk_space", lambda *a: None)
    monkeypatch.setattr(runner, "_check_preflight_disk", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_insert_etl_run", lambda *a: 1)
    monkeypatch.setattr(runner, "_update_etl_run", lambda *a: None)
    monkeypatch.setattr(runner, "_record_source_files", lambda *a: None)
    monkeypatch.setattr("app.etl.runner.requires_fresh_start", lambda *a: False)

    def retain_candidate(
        pipeline,
        dsn,
        stages,
        total_rows,
        previous_count,
        started,
        checkpoint,
        run_id,
        log,
    ):
        return EtlResult(
            dataset_name=pipeline.dataset_name,
            stages=stages,
            total_rows_loaded=total_rows,
            total_duration_seconds=0,
            success=True,
            previous_row_count=previous_count,
        )

    monkeypatch.setattr(runner, "_publish", retain_candidate)
    yield pipeline, runner, scope, dsn
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS test_ds_staging, test_ds")


def expected_rows(scope, rows=ROWS_A + ROWS_B):
    return Counter(row for row in rows if row[0] in scope)


def fresh(candidate):
    pipeline, runner, _, dsn = candidate
    CheckpointManager("test_ds", pipeline.staging_table_name).clear()
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS test_ds_staging")
    result = runner.run(pipeline)
    assert result.success, result.error
    return snapshot(dsn), result.total_rows_loaded


@pytest.mark.parametrize(
    "point", ["checkpoint-a", "checkpoint-all", "commit-b", "filter", "rollback-b"]
)
def test_interrupted_resume_equals_complete_candidate(candidate, monkeypatch, point):
    pipeline, runner, scope, dsn = candidate
    complete, total = fresh(candidate)
    assert complete == expected_rows(scope)
    assert total == sum(complete.values()) == 8
    CheckpointManager("test_ds", pipeline.staging_table_name).clear()
    pipeline.create_staging_table(dsn)

    with monkeypatch.context() as interrupt:
        if point.startswith("checkpoint-"):
            original = CheckpointManager.save
            target_count = 1 if point == "checkpoint-a" else 2

            def save_then_crash(self, loaded_files, **kwargs):
                original(self, loaded_files, **kwargs)
                if kwargs.get("pending") is None and len(loaded_files) == target_count:
                    raise SimulatedCrash(point)

            interrupt.setattr(CheckpointManager, "save", save_then_crash)
        elif point in ("commit-b", "rollback-b"):
            original = pipeline.stage

            def stage_then_crash(path):
                if path != pipeline.files[1]:
                    return original(path)
                if point == "rollback-b":
                    with psycopg.connect(dsn) as conn:
                        conn.execute(
                            "INSERT INTO test_ds_staging "
                            "VALUES ('44',2025,'rollback','bad','bad')"
                        )
                        raise SimulatedCrash(point)
                original(path)
                # A fresh observer proves the preceding commit is durable.
                assert snapshot(dsn) == expected_rows(scope)
                raise SimulatedCrash(point)

            interrupt.setattr(pipeline, "stage", stage_then_crash)
        else:
            original = runner._apply_scope_filter

            def filter_then_crash(*args, **kwargs):
                original(*args, **kwargs)
                assert snapshot(dsn) == expected_rows(scope)
                raise SimulatedCrash(point)

            interrupt.setattr(runner, "_apply_scope_filter", filter_then_crash)
        with pytest.raises(SimulatedCrash):
            runner.run(pipeline)
    result = runner.run(pipeline)
    assert result.success, result.error
    assert snapshot(dsn) == complete == expected_rows(scope)
    assert result.total_rows_loaded == total


def test_existing_unlogged_table_emptied_after_checkpoint_is_reconstructed(
    candidate, monkeypatch
):
    pipeline, runner, scope, dsn = candidate
    original = CheckpointManager.save

    def save_then_crash(self, loaded_files, **kwargs):
        original(self, loaded_files, **kwargs)
        if kwargs.get("pending") is None and len(loaded_files) == 1:
            raise SimulatedCrash("checkpoint durable")

    with monkeypatch.context() as interrupt:
        interrupt.setattr(CheckpointManager, "save", save_then_crash)
        with pytest.raises(SimulatedCrash):
            runner.run(pipeline)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE test_ds_staging")
        assert conn.execute(
            "SELECT to_regclass('test_ds_staging') IS NOT NULL"
        ).fetchone() == (True,)
    creations = pipeline.creations
    result = runner.run(pipeline)
    assert result.success, result.error
    assert pipeline.creations > creations
    assert snapshot(dsn) == expected_rows(scope)
    assert result.total_rows_loaded == 8


def test_scope_expansion_after_committed_filter_rebuilds_removed_department(
    candidate, monkeypatch
):
    pipeline, runner, scope, dsn = candidate
    scope[:] = ["44"]
    original = CheckpointManager.save

    def clean_filter_checkpoint_then_crash(self, loaded_files, **kwargs):
        original(self, loaded_files, **kwargs)
        if kwargs.get("pending") is None and kwargs.get("staging_rows") == 5:
            assert snapshot(dsn) == expected_rows(["44"])
            raise SimulatedCrash("filter and completion checkpoint committed cleanly")

    with monkeypatch.context() as interrupt:
        interrupt.setattr(CheckpointManager, "save", clean_filter_checkpoint_then_crash)
        with pytest.raises(SimulatedCrash):
            runner.run(pipeline)
    checkpoint = CheckpointManager("test_ds", pipeline.staging_table_name).load()
    assert checkpoint["pending"] is None and checkpoint["staging_rows"] == 5
    assert checkpoint["scope_key"] == pipeline.checkpoint_scope_key
    assert checkpoint["source_files"] == runner._fingerprint_sources(
        pipeline, pipeline.files
    )
    creations = pipeline.creations
    scope[:] = ["44", "85"]
    result = runner.run(pipeline)
    assert result.success, result.error
    assert snapshot(dsn) == expected_rows(scope)
    assert result.total_rows_loaded == 8
    assert pipeline.creations == creations + 1


def test_resume_after_a_committed_filter_does_not_recheck_the_filtered_table(
    candidate, monkeypatch
):
    """04.1-REVIEW WR-R1: the whole national file is checked once, before the
    filter. A resume finds the table already filtered (5 rows of 44 for a file
    of 8): checking it again would refuse a legitimate import as truncated."""
    pipeline, runner, scope, dsn = candidate
    scope[:] = ["44"]
    monkeypatch.setattr(SqlPipeline, "national_min_rows", 8, raising=False)
    original = CheckpointManager.save

    def filtered_checkpoint_then_crash(self, loaded_files, **kwargs):
        original(self, loaded_files, **kwargs)
        if kwargs.get("pending") is None and kwargs.get("staging_rows") == 5:
            raise SimulatedCrash("filter and completion checkpoint committed")

    with monkeypatch.context() as interrupt:
        interrupt.setattr(CheckpointManager, "save", filtered_checkpoint_then_crash)
        with pytest.raises(SimulatedCrash):
            runner.run(pipeline)
    result = runner.run(pipeline)
    assert result.success, result.error
    assert snapshot(dsn) == expected_rows(["44"])
    # A truncated national file is still refused on a fresh run.
    monkeypatch.setattr(SqlPipeline, "national_min_rows", 9, raising=False)
    CheckpointManager("test_ds", pipeline.staging_table_name).clear()
    refused = runner.run(pipeline)
    assert not refused.success
    assert "National file volume" in refused.error


def test_changed_source_generation_rebuilds_all_files(candidate, monkeypatch):
    pipeline, runner, scope, dsn = candidate
    original = CheckpointManager.save

    def save_then_crash(self, loaded_files, **kwargs):
        original(self, loaded_files, **kwargs)
        if kwargs.get("pending") is None and len(loaded_files) == 1:
            raise SimulatedCrash("generation-A partially staged")

    with monkeypatch.context() as interrupt:
        interrupt.setattr(CheckpointManager, "save", save_then_crash)
        with pytest.raises(SimulatedCrash):
            runner.run(pipeline)
    new_rows = [tuple([*row[:3], "generation-B", row[4]]) for row in ROWS_A + ROWS_B]
    for path, rows in zip(pipeline.files, (new_rows[:4], new_rows[4:]), strict=True):
        path.write_bytes(json.dumps(rows).encode())
    result = runner.run(pipeline)
    assert result.success, result.error
    assert snapshot(dsn) == expected_rows(scope, new_rows)
    assert all(row[3] == "generation-B" for row in snapshot(dsn))


@pytest.mark.parametrize("gate", ["minimum", "regression", "coverage"])
def test_resume_keeps_negative_quality_gates(candidate, monkeypatch, gate):
    pipeline, runner, scope, dsn = candidate
    fresh(candidate)
    if gate == "minimum":
        pipeline.min_staging_rows = 9
    elif gate == "regression":
        with psycopg.connect(dsn) as conn:
            conn.execute("INSERT INTO test_ds SELECT * FROM test_ds_staging")
            conn.execute("INSERT INTO test_ds SELECT * FROM test_ds_staging")
    else:
        scope.append("49")
    publish = Mock(wraps=runner._publish)
    monkeypatch.setattr(runner, "_publish", publish)
    result = runner.run(pipeline)
    assert not result.success
    publish.assert_not_called()
    assert {"minimum": "GATE-01", "regression": "regression", "coverage": "49"}[
        gate
    ] in result.error
    with psycopg.connect(dsn) as conn:
        expected = 16 if gate == "regression" else 0
        assert conn.execute("SELECT count(*) FROM test_ds").fetchone() == (expected,)


def test_real_ban_copy_commit_before_checkpoint_is_replayed_once(
    candidate, monkeypatch
):
    from app.etl_ban.config import BAN_OUTPUT_COLUMNS
    from app.etl_ban.pipeline import BanPipeline

    _, runner, scope, dsn = candidate
    rows_a = [
        ("44", "ban-duplicate", "1", "", "Rue A", "44000", "44001", "Test", "-1", "47"),
        ("44", "ban-duplicate", "1", "", "Rue A", "44000", "44001", "Test", "-1", "47"),
    ]
    rows_b = [("85", "ban-b", "2", "", "Rue B", "85000", "85001", "Test", "-1", "47")]
    files = []
    for dept, rows in (("44", rows_a), ("85", rows_b)):
        buffer = StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(BAN_OUTPUT_COLUMNS)
        writer.writerows(rows)
        path = candidate[0].files[0].parents[1] / f"adresses-{dept}.csv"
        path.write_bytes(buffer.getvalue().encode())
        files.append(path)

    class BanCopyPipeline(BanPipeline):
        def download(self):
            return files

        def validate_schema(self, path):
            # Input is already transformed; this smoke proves the native COPY.
            assert next(csv.reader(path.read_text().splitlines())) == BAN_OUTPUT_COLUMNS

        def transform(self, path):
            return path

        def create_staging_table(self, dsn):
            self.creation_count = getattr(self, "creation_count", 0) + 1
            with psycopg.connect(dsn) as conn:
                conn.execute("DROP TABLE IF EXISTS _ban_staging_flat, adresses_staging")
                conn.execute("CREATE TABLE adresses_staging (code_departement text)")

    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE TABLE adresses (code_departement text)")
    pipeline = BanCopyPipeline()
    pipeline.pin_millesime("2025-01-01")
    runner._scope_loader = lambda _: InstanceScope(frozenset(["ban"]), tuple(scope))

    def ban_snapshot():
        with psycopg.connect(dsn) as observer:
            rows = observer.execute(
                "SELECT code_departement,ban_id,nom_voie,count(*) "
                "FROM _ban_staging_flat GROUP BY 1,2,3"
            ).fetchall()
        return Counter({tuple(row[:3]): row[3] for row in rows})

    expected = Counter(
        {("44", "ban-duplicate", "Rue A"): 2, ("85", "ban-b", "Rue B"): 1}
    )
    try:
        complete = runner.run(pipeline)
        assert complete.success, complete.error
        assert ban_snapshot() == expected
        CheckpointManager("ban", pipeline.staging_table_name).clear()
        pipeline.create_staging_table(dsn)
        stage = pipeline.stage

        def copy_then_crash(path):
            count = stage(path)  # Actual BanPipeline.stage COPY and commit.
            if path == files[1]:
                assert ban_snapshot() == expected  # New observer sees commit.
                raise SimulatedCrash("BAN COPY committed, no completion checkpoint")
            return count

        with monkeypatch.context() as interrupt:
            interrupt.setattr(pipeline, "stage", copy_then_crash)
            with pytest.raises(SimulatedCrash):
                runner.run(pipeline)
        resumed = runner.run(pipeline)
        assert resumed.success, resumed.error
        assert ban_snapshot() == expected
        assert resumed.total_rows_loaded == complete.total_rows_loaded == 3
        checkpoint = CheckpointManager("ban", pipeline.staging_table_name)
        previous = checkpoint.load()
        assert previous["pending"] is None and previous["staging_rows"] == 3
        creations = pipeline.creation_count
        pipeline.pin_millesime("2025-02-01")
        repinned = runner.run(pipeline)
        assert repinned.success, repinned.error
        assert pipeline.creation_count == creations + 1
        assert checkpoint.load()["identity_key"] != previous["identity_key"]
        assert ban_snapshot() == expected
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DROP TABLE IF EXISTS _ban_staging_flat, adresses_staging, adresses"
            )


def test_full_partial_and_complete_resume_report_observed_cost(candidate, monkeypatch):
    pipeline, runner, scope, dsn = candidate
    calls = Counter()
    count = runner._count_staging_rows
    fingerprint = runner._fingerprint_sources

    def counted_stock(*args):
        calls["count"] += 1
        return count(*args)

    def counted_sources(*args):
        calls["fingerprint"] += 1
        return fingerprint(*args)

    monkeypatch.setattr(runner, "_count_staging_rows", counted_stock)
    monkeypatch.setattr(runner, "_fingerprint_sources", counted_sources)
    observations = []

    def measured(label):
        calls.clear()
        pipeline.staged.clear()
        started = time.perf_counter()
        result = runner.run(pipeline)
        elapsed = time.perf_counter() - started
        assert result.success, result.error
        assert snapshot(dsn) == expected_rows(scope)
        assert result.total_rows_loaded == 8
        observation = {
            "mode": label,
            "rows": 8,
            "files_staged": len(pipeline.staged),
            "seconds": elapsed,
            "calls": dict(calls),
            "invocation_rows": sum(
                step.rows_affected or 0
                for step in result.stages
                if step.stage_name.startswith("stage:")
            ),
        }
        observations.append(observation)
        return observation

    full = measured("full")
    assert (full["files_staged"], full["invocation_rows"]) == (2, 8)
    CheckpointManager("test_ds", pipeline.staging_table_name).clear()
    pipeline.create_staging_table(dsn)
    save = CheckpointManager.save

    def pause_after_first_checkpoint(self, loaded_files, **kwargs):
        save(self, loaded_files, **kwargs)
        if len(loaded_files) == 1 and kwargs.get("pending") is None:
            raise SimulatedCrash("clean partial candidate")

    with monkeypatch.context() as interrupt:
        interrupt.setattr(CheckpointManager, "save", pause_after_first_checkpoint)
        with pytest.raises(SimulatedCrash):
            runner.run(pipeline)
    partial = measured("partial_resume")
    assert (partial["files_staged"], partial["invocation_rows"]) == (1, 4)
    completed = measured("complete_resume")
    assert (completed["files_staged"], completed["invocation_rows"]) == (0, 0)
    destination = os.environ.get("DC_ETL_PROOF_DIR")
    if destination:
        report = {
            "sample": "two local sources, eight rows; no production-scale benchmark",
            "observations": observations,
        }
        (Path(destination) / "resume-observed-cost.json").write_bytes(
            json.dumps(report, indent=2).encode()
        )
