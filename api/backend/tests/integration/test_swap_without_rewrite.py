"""Swap without rewrite (plan 04.1-04) on a disposable PostGIS database.

A real runner import goes through stage, swap and publication:
- the file loaded into the candidate is the file published (same
  ``relfilenode`` on every leaf before and after the swap), and production is
  logged;
- after the publication ``<prod>_old`` is gone, with its ``etl_old_tables``
  row, in the same transaction as the repair of the dependent materialized
  view, which stays present, readable and bound to the new production;
- a failed view repair rolls the drop back (same rule as cleanup_old_tables).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest

from app.common import matviews
from app.etl.base import DEPARTEMENTS, BasePipeline
from app.etl.publication import (
    Publication,
    dataset_execution_lock,
    requires_fresh_start,
)
from app.etl.runner import PipelineRunner
from app.scope.model import InstanceScope
from tests.etl.test_runner import FakePipeline, _patch_all
from tests.integration.publication_fixture import install_publication_journal

# _patch_all() stubs the post-publication drop for unit tests: restore it.
_REAL_DROP = PipelineRunner.__dict__["_drop_published_old_tables"]

DATASET = "swap_rewrite_probe"
PROD = "swap_prod"
STAGING = "swap_prod_staging"
VIEW = "communes_lookup"  # a registered matview: recreate_missing_matviews owns it
VIEW_SQL = (
    f"CREATE MATERIALIZED VIEW {VIEW} AS "
    f"SELECT code_departement, sum(valeur)::int AS total FROM {PROD} "
    "GROUP BY code_departement"
)


# pg_partition_tree() returns no row for a table outside any partitioning:
# such a table is its own (single) leaf.
_TREE_SQL = """
    SELECT c.relfilenode, c.relpersistence, t.isleaf
    FROM pg_partition_tree(%(t)s::regclass) t JOIN pg_class c ON c.oid = t.relid
    UNION ALL
    SELECT c.relfilenode, c.relpersistence, true FROM pg_class c
    WHERE c.oid = %(t)s::regclass
      AND NOT EXISTS (SELECT 1 FROM pg_partition_tree(%(t)s::regclass))
"""


def leaf_files(conn: psycopg.Connection, table: str) -> list[int]:
    """Storage files of every leaf relation of *table* (itself if flat)."""
    rows = conn.execute(_TREE_SQL, {"t": table}).fetchall()
    files = sorted(row[0] for row in rows if row[2])
    assert files and all(files), f"no storage file found for {table}"
    return files


def tree_persistence(conn: psycopg.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(_TREE_SQL, {"t": table}).fetchall()}


def view_sources(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT d.refobjid::regclass::text FROM pg_rewrite r "
        "JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass "
        "AND d.objid = r.oid AND d.refclassid = 'pg_class'::regclass "
        "WHERE r.ev_class = %s::regclass AND d.refobjid <> r.ev_class",
        (VIEW,),
    ).fetchall()
    return {row[0] for row in rows}


class SwapProbePipeline(FakePipeline):
    """Generic import path: base candidate, stage from files, base swap."""

    dataset_name = DATASET
    production_table_name = PROD
    staging_table_name = STAGING

    def __init__(self, dsn: str, files: list[Path], partitioned: bool) -> None:
        self.dsn = dsn
        self._paths = files
        self._partitioned = partitioned
        self.loaded_files: list[int] | None = None
        self.candidate_persistence: set[str] | None = None

    @property
    def staging_is_partitioned(self) -> bool:
        return self._partitioned

    def get_sync_dsn(self) -> str:
        return self.dsn

    def download(self) -> list[Path]:
        return list(self._paths)

    def create_staging_table(self, dsn: str) -> None:
        BasePipeline.create_staging_table(self, dsn)

    def stage(self, file_path: Path) -> int:
        rows = [
            line.split(",")
            for line in file_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        with psycopg.connect(self.dsn) as conn:
            for code, valeur in rows:
                conn.execute(
                    f"INSERT INTO {STAGING} (code_departement, valeur) VALUES (%s, %s)",
                    (code, int(valeur)),
                )
        return len(rows)

    def swap(self) -> None:
        index = self.staging_index_name("idx_swap_prod_valeur")
        with psycopg.connect(self.dsn) as conn:
            conn.execute(f"CREATE INDEX {index} ON {STAGING} (valeur)")
            conn.commit()
            self.loaded_files = leaf_files(conn, STAGING)
            self.candidate_persistence = tree_persistence(conn, STAGING)
        self.swap_tables(self.dsn)
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP SEQUENCE IF EXISTS {PROD}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {STAGING}_id_seq RENAME TO {PROD}_id_seq"
                )
                if self._partitioned:
                    self.rename_partitions_after_swap(cur)
            conn.commit()
        self.register_old_table(self.dsn, f"{PROD}_old")


class ViewHook:
    """Required completion hook rebinding the view, as DependentMatviewHook does."""

    required_on_complete = True

    def __init__(self, dsn: str, rebind: bool) -> None:
        self.dsn = dsn
        self.rebind = rebind

    def on_run_start(self, *_args) -> None:
        pass

    def on_run_failure(self, *_args) -> None:
        pass

    def on_run_complete(self, *_args) -> None:
        if not self.rebind:
            return
        with psycopg.connect(self.dsn) as conn:
            conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {VIEW}")
            conn.execute(VIEW_SQL)


@pytest.fixture()
def probe_db(isolated_pg_dsn):
    """Fresh public schema: published table, dependent view, journals."""
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        install_publication_journal(conn)
        conn.execute(
            "CREATE TABLE etl_old_tables (id SERIAL PRIMARY KEY, "
            "table_name TEXT NOT NULL UNIQUE, dataset_name TEXT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        conn.execute(
            "CREATE TABLE matview_metadata (matview_name text PRIMARY KEY, "
            "last_refresh_at timestamptz, last_refresh_duration_seconds float, "
            "last_refresh_status text, last_error text)"
        )
    return isolated_pg_dsn


def install_production(dsn: str, partitioned: bool) -> None:
    with psycopg.connect(dsn) as conn:
        clause = " PARTITION BY LIST (code_departement)" if partitioned else ""
        conn.execute(
            f"CREATE TABLE {PROD} (id bigserial, code_departement text NOT NULL, "
            f"valeur integer NOT NULL){clause}"
        )
        if partitioned:
            for dept in ("44", "85"):
                conn.execute(
                    f"CREATE TABLE {PROD}_{dept} PARTITION OF {PROD} "
                    f"FOR VALUES IN ('{dept}')"
                )
            conn.execute(f"CREATE TABLE {PROD}_default PARTITION OF {PROD} DEFAULT")
        conn.execute(
            f"INSERT INTO {PROD} (code_departement, valeur) VALUES ('44',1),('85',2)"
        )
        conn.execute(VIEW_SQL)


def run_import(dsn: str, tmp_path: Path, partitioned: bool, rebind: bool):
    source = tmp_path / "source.csv"
    source.write_text("44,10\n85,20\n85,5\n", encoding="utf-8")
    pipeline = SwapProbePipeline(dsn, [source], partitioned)
    scope = InstanceScope(frozenset({DATASET}), tuple(DEPARTEMENTS))
    with (
        _patch_all(staging_exists=False, checkpoint_data=None),
        patch("app.etl.runner.Publication", Publication),
        patch("app.etl.runner.dataset_execution_lock", dataset_execution_lock),
        patch("app.etl.runner.requires_fresh_start", requires_fresh_start),
        patch("app.etl.runner.settings.source_archive_dir", ""),
        patch.object(PipelineRunner, "_drop_published_old_tables", _REAL_DROP),
    ):
        runner = PipelineRunner([ViewHook(dsn, rebind)], scope_loader=lambda _: scope)
        result = runner.run(pipeline)
    return pipeline, result


@pytest.mark.parametrize("rebind", [True, False], ids=["view-rebound", "view-repaired"])
@pytest.mark.parametrize("partitioned", [False, True], ids=["flat", "partitioned"])
def test_publication_keeps_loaded_file_and_drops_old(
    probe_db, tmp_path, partitioned, rebind
):
    install_production(probe_db, partitioned)
    pipeline, result = run_import(probe_db, tmp_path, partitioned, rebind)

    assert result.success, result.error
    with psycopg.connect(probe_db) as conn:
        # Same storage file before and after the swap: nothing was rewritten.
        assert leaf_files(conn, PROD) == pipeline.loaded_files
        assert pipeline.candidate_persistence == {"p"}
        assert tree_persistence(conn, PROD) == {"p"}
        assert conn.execute("SELECT to_regclass(%s)", (f"{PROD}_old",)).fetchone() == (
            None,
        )
        assert conn.execute("SELECT count(*) FROM etl_old_tables").fetchone() == (0,)
        assert conn.execute(
            "SELECT status FROM etl_publications ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("published",)
        # The dependent view exists, answers and reads the new production.
        assert view_sources(conn) == {PROD}
        assert dict(
            conn.execute(f"SELECT code_departement, total FROM {VIEW}").fetchall()
        ) == {"44": 10, "85": 25}


def test_failed_view_repair_keeps_old_table(probe_db, tmp_path, monkeypatch):
    install_production(probe_db, partitioned=False)

    def broken(conn):
        conn.execute("SELECT 1/0")

    monkeypatch.setattr(matviews, "EXPECTED_MATVIEWS", {VIEW: broken})
    _pipeline, result = run_import(probe_db, tmp_path, partitioned=False, rebind=False)

    # The publication is committed; only the drop of the previous generation
    # is undone, so the view never disappears.
    assert result.success, result.error
    with psycopg.connect(probe_db) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (f"{PROD}_old",)).fetchone()[0]
        assert conn.execute("SELECT table_name FROM etl_old_tables").fetchall() == [
            (f"{PROD}_old",)
        ]
        assert view_sources(conn) == {f"{PROD}_old"}
        assert conn.execute(f"SELECT count(*) FROM {VIEW}").fetchone() == (2,)
        assert conn.execute(f"SELECT sum(valeur) FROM {PROD}").fetchone() == (35,)


def test_swap_refuses_unlogged_candidate(probe_db):
    from app.etl.errors import StagingNotLoggedError

    install_production(probe_db, partitioned=False)
    pipeline = SwapProbePipeline(probe_db, [], partitioned=False)
    with psycopg.connect(probe_db) as conn:
        conn.execute(
            f"CREATE UNLOGGED TABLE {STAGING} (LIKE {PROD} INCLUDING DEFAULTS)"
        )
        conn.execute(
            f"INSERT INTO {STAGING} (code_departement, valeur) VALUES ('44',9)"
        )
        before = leaf_files(conn, PROD)

    with pytest.raises(StagingNotLoggedError):
        pipeline.swap_tables(probe_db)

    with psycopg.connect(probe_db) as conn:
        assert leaf_files(conn, PROD) == before
        assert tree_persistence(conn, STAGING) == {"u"}
        assert conn.execute("SELECT to_regclass(%s)", (f"{PROD}_old",)).fetchone() == (
            None,
        )


def test_purge_old_tables_drops_every_tracked_old_and_repairs_views(
    probe_db, monkeypatch
):
    from app.scheduler import jobs

    install_production(probe_db, partitioned=False)
    with psycopg.connect(probe_db) as conn:
        # A view still bound to a tracked _old table, as the old code left them.
        conn.execute(f"ALTER TABLE {PROD} RENAME TO {PROD}_old")
        conn.execute(f"CREATE TABLE {PROD} (LIKE {PROD}_old INCLUDING DEFAULTS)")
        conn.execute(f"INSERT INTO {PROD} (code_departement, valeur) VALUES ('44',7)")
        conn.execute("CREATE TABLE other_old (id int)")
        conn.execute(
            "INSERT INTO etl_old_tables (table_name, dataset_name, created_at) "
            f"VALUES ('{PROD}_old', %s, NOW()), ('other_old', 'other', NOW())",
            (DATASET,),
        )
    monkeypatch.setattr(jobs.settings, "database_url", probe_db)

    jobs.purge_old_tables()

    with psycopg.connect(probe_db) as conn:
        assert conn.execute("SELECT count(*) FROM etl_old_tables").fetchone() == (0,)
        for name in (f"{PROD}_old", "other_old"):
            assert conn.execute("SELECT to_regclass(%s)", (name,)).fetchone() == (None,)
        assert view_sources(conn) == {PROD}
        assert conn.execute(f"SELECT total FROM {VIEW}").fetchall() == [(7,)]


@pytest.fixture(autouse=True)
def _registered_view(monkeypatch):
    """Only the probe view is expected; it is rebuilt from the probe table."""

    def rebuild(conn):
        conn.execute(VIEW_SQL)

    monkeypatch.setattr(matviews, "EXPECTED_MATVIEWS", {VIEW: rebuild})
