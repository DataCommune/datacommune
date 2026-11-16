"""COR-02 on a disposable PostGIS database with a read-only login role."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from app.config import settings
from app.etl.checkpoint import CheckpointManager
from app.etl.hooks import build_default_hooks
from app.etl.runner import PipelineRunner
from tests.etl.test_runner import FakePipeline

pytestmark = pytest.mark.destructive


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    # FakePipeline is synthetic: its name is not a catalogue key.
    host_test_datasets("test_ds")


TABLES = (
    "test_ds",
    "test_ds_staging",
    "test_ds_old",
    "etl_runs",
    "dataset_metadata",
    "matview_metadata",
    "etl_old_tables",
    "validation_runs",
)


@pytest.fixture
def readonly_database(pg_dsn):
    role = "cor02_reader_" + uuid4().hex[:8]
    schema = "cor02_" + uuid4().hex
    password = uuid4().hex
    # Each scenario owns its relations, even when earlier integration modules
    # have already migrated public. Setup is transactional on partial failure.
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        for table in TABLES:
            conn.execute(f"CREATE TABLE {schema}.{table} (id integer, state text)")
            conn.execute(
                f"INSERT INTO {schema}.{table} VALUES (1, 'previous publication')"
            )
        conn.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                psycopg.sql.Identifier(role), psycopg.sql.Literal(password)
            )
        )
        conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}")
        conn.execute(f"ALTER ROLE {role} SET default_transaction_read_only = on")
    # Exclude public so an unqualified name cannot fall back to another test's
    # migrated relation if this fixture is incomplete.
    readonly_dsn = make_conninfo(
        pg_dsn, user=role, password=password, options=f"-c search_path={schema}"
    )
    try:
        with psycopg.connect(readonly_dsn) as conn:
            assert conn.execute("SHOW transaction_read_only").fetchone() == ("on",)
            assert conn.execute("SELECT current_user").fetchone() == (role,)
            assert conn.execute("SELECT current_schema()").fetchone() == (schema,)
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("UPDATE test_ds_staging SET state = 'forbidden'")
            conn.rollback()
        yield readonly_dsn
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE {role}")


def snapshot(dsn):
    with psycopg.connect(dsn) as conn:
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in TABLES
        }


@pytest.mark.parametrize(
    "failure", [None, "download", "validate", "disk", "incomplete"]
)
@pytest.mark.parametrize("checkpoint_state", ["missing", "matching", "stale"])
def test_dry_run_preserves_database_and_checkpoint(
    readonly_database, tmp_path, monkeypatch, failure, checkpoint_state
):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    pipeline = FakePipeline()
    pipeline.get_sync_dsn = MagicMock(return_value=readonly_database)
    checkpoint = CheckpointManager("test_ds", "test_ds_staging")
    if checkpoint_state != "missing":
        checkpoint.save(
            ["source.csv"],
            scope_key="obsolete" if checkpoint_state == "stale" else "",
            identity_key=pipeline.checkpoint_identity(""),
            staging_rows=1,
            source_files=[{"id": "source.csv", "bytes": 1, "sha256": "a" * 64}],
        )
    before_checkpoint = (
        checkpoint.path.read_bytes() if checkpoint.path.exists() else None
    )
    before = snapshot(readonly_database)

    def download():
        # Exercise an actual read with the restricted role during the run.
        with psycopg.connect(readonly_database) as conn:
            assert conn.execute("SELECT state FROM test_ds").fetchone() == (
                "previous publication",
            )
        if failure == "download":
            raise RuntimeError("synthetic source unavailable")
        return [Path("source.csv")]

    pipeline.download = download
    pipeline.validate_schema = MagicMock()
    if failure == "validate":
        pipeline.validate_schema.side_effect = ValueError("synthetic invalid schema")
    hooks = [MagicMock(wraps=hook) for hook in build_default_hooks()]
    with (
        patch.object(
            FakePipeline,
            "expected_file_count",
            new_callable=PropertyMock,
            return_value=2 if failure == "incomplete" else 1,
        ),
        patch.object(
            FakePipeline,
            "min_success_ratio",
            new_callable=PropertyMock,
            return_value=1.0,
        ),
        patch("app.etl.runner.psycopg.connect", wraps=psycopg.connect) as connect,
        patch("app.etl.runner.check_disk_space") as disk,
    ):
        if failure == "disk":
            disk.side_effect = RuntimeError("synthetic full disk")
        result = PipelineRunner(hooks).run(pipeline, dry_run=True)
    assert result.success is (failure is None)
    assert result.total_rows_loaded == 0
    assert connect.call_count == (0 if failure == "disk" else 1)
    pipeline.get_sync_dsn.assert_not_called()
    assert all(hook.mock_calls == [] for hook in hooks)
    assert snapshot(readonly_database) == before
    after_checkpoint = (
        checkpoint.path.read_bytes() if checkpoint.path.exists() else None
    )
    assert after_checkpoint == before_checkpoint
    if failure not in {"download", "disk"}:
        pipeline.validate_schema.assert_called_once_with(Path("source.csv"))
