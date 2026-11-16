"""A pinned vintage label never outlives its run (plan 02-11, PROV-03).

``dataset_metadata.source_period_label`` keeps an older authentic value by
COALESCE when a pipeline cannot name its period. A pipeline that can target a
vintage (``--millesime``) always writes its own label instead, None included:
a run on the producer's current vintage after a pinned one must not inherit
the pinned label. Proven on the real SQL, inside a rolled-back transaction.

Run via the wrapper script:
    pwsh -NoProfile -File scripts/test-postgis.ps1 -Paths
        tests/integration/test_millesime_metadata.py
"""

from __future__ import annotations

import logging

import psycopg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from app.config import settings
from app.etl.runner import PipelineRunner
from tests.integration.test_instance_scope import BACKEND

LOG = logging.getLogger("test")


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn: str) -> str:
    # Own database: the shared one of this CI group is left at another
    # revision by earlier modules (DuplicateTable etl_publications on upgrade).
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    return isolated_pg_dsn


@pytest.fixture()
def conn(migrated: str):
    connection = psycopg.connect(migrated, connect_timeout=5)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _write(conn, label, *, replace: bool) -> str | None:
    PipelineRunner._update_dataset_metadata(
        "unused",
        "ban",
        10,
        1.0,
        LOG,
        source_period_label=label,
        replace_source_period_label=replace,
        connection=conn,
    )
    return conn.execute(
        "SELECT source_period_label FROM dataset_metadata WHERE dataset_name = 'ban'"
    ).fetchone()[0]


def test_pinnable_pipeline_replaces_the_label(conn):
    assert _write(conn, "2026-06-17", replace=True) == "2026-06-17"
    # Next run on `latest`: the vintage is unknown again, not 2026-06-17.
    assert _write(conn, None, replace=True) is None


def test_other_pipelines_keep_the_authentic_label(conn):
    assert _write(conn, "Export RNB 2026-08", replace=False) == "Export RNB 2026-08"
    assert _write(conn, None, replace=False) == "Export RNB 2026-08"
