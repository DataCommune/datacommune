"""A run row left 'running' by a killed import is closed by the next run (audit ETL-12).

The runner inserts its ``etl_runs`` row as ``'running'`` and finalizes it at the
end. A SIGKILL or a power cut leaves it ``'running'`` for ever: run 119 of
``batiments``, stopped by the power key of the hypervisor on 2026-10-31. The next run of
the same dataset holds the dataset execution lock, so no other run of it can
be live: it closes those rows as failed, interrupted.
"""

import logging
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl.publication import dataset_execution_lock
from app.etl.runner import PipelineRunner

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    return isolated_pg_dsn


def _insert(dsn: str, dataset: str, status: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO etl_runs (dataset_name, status) VALUES (%s, %s) RETURNING id",
            (dataset, status),
        ).fetchone()
        conn.commit()
    assert row is not None
    return row[0]


def _row(dsn: str, run_id: int) -> tuple:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT status, error_message, completed_at FROM etl_runs WHERE id = %s",
            (run_id,),
        ).fetchone()
    assert row is not None
    return row


def test_next_run_closes_the_interrupted_runs_of_its_dataset_only(migrated):
    orphan = _insert(migrated, "batiments", "running")
    done = _insert(migrated, "batiments", "success")
    other = _insert(migrated, "dvf", "running")

    with dataset_execution_lock(migrated, "batiments"):
        run_id = PipelineRunner._insert_etl_run(
            migrated, "batiments", logging.getLogger("test")
        )

    assert run_id is not None
    assert _row(migrated, run_id)[0] == "running"
    status, error, completed_at = _row(migrated, orphan)
    assert status == "failed"
    assert error == f"interrupted: still running when run {run_id} started"
    # Its real end is unknown: no time is made up.
    assert completed_at is None
    assert _row(migrated, done) == ("success", None, None)
    # Another dataset holds its own lock: its run may be live, it is left alone.
    assert _row(migrated, other)[0] == "running"


def test_a_run_with_no_interrupted_row_changes_nothing_else(migrated):
    done = _insert(migrated, "cadastre", "success")
    failed = _insert(migrated, "cadastre", "failed")

    with dataset_execution_lock(migrated, "cadastre"):
        run_id = PipelineRunner._insert_etl_run(
            migrated, "cadastre", logging.getLogger("test")
        )

    assert run_id is not None
    assert _row(migrated, done) == ("success", None, None)
    assert _row(migrated, failed) == ("failed", None, None)
