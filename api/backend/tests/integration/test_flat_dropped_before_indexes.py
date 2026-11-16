"""Flat table dropped before the candidate's indexes (plan 04.1-04, task 2).

Real ``stage()`` and ``swap()`` of ARCEP (connectivite) and cadastre on a
migrated disposable PostGIS database:
- the flat table is unlogged while it exists;
- when the candidate's index build starts, the flat table no longer exists and
  the candidate is logged (A1 committed: transfer + flat drop; A2: indexes);
- an interruption between A1 and A2, or inside A1, followed by a new run
  publishes the same stock as an uninterrupted run.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl.checkpoint import CheckpointManager
from app.etl.publication import (
    Publication,
    dataset_execution_lock,
    requires_fresh_start,
)
from app.etl.runner import PipelineRunner
from app.etl_arcep.config import OUTPUT_COLUMNS
from app.etl_arcep.pipeline import ArcepPipeline
from app.etl_cadastre.pipeline import CadastrePipeline
from app.scope.model import InstanceScope
from tests.etl.test_runner import _patch_all

# _patch_all() stubs the resume evidence for unit tests: the real one is under
# test here, so it is put back inside the patched block.
_REAL_STAGING_EXISTS = PipelineRunner.__dict__["_staging_table_exists"]
_REAL_CHECKPOINT = {
    name: CheckpointManager.__dict__[name] for name in ("load", "save", "clear")
}

ARCEP_FLAT = "_eligibilite_fixe_staging_flat"
CADASTRE_FLAT = "_cadastre_staging_flat"


class SimulatedCrashError(RuntimeError):
    """Simulated crash of the import process."""


def relation_persistence(dsn: str, name: str) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT relpersistence FROM pg_class WHERE oid = to_regclass(%s)",
            (name,),
        ).fetchone()
    return row[0] if row else None


def tree_persistence(dsn: str, name: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT c.relpersistence FROM pg_class c WHERE c.oid = to_regclass(%(t)s) "
            "OR c.oid IN (SELECT relid FROM pg_partition_tree(to_regclass(%(t)s)))",
            {"t": name},
        ).fetchall()
    return {row[0] for row in rows}


class IndexProbe:
    """Wraps ``_create_staging_indexes``: the start of phase A2."""

    def __init__(self, pipeline, dsn: str, flat: str, interrupt: bool = False):
        self.observed: list[tuple[str | None, set[str]]] = []
        original = pipeline._create_staging_indexes
        staging = pipeline.staging_table_name

        def probe(cur):
            self.observed.append(
                (relation_persistence(dsn, flat), tree_persistence(dsn, staging))
            )
            if interrupt:
                raise SimulatedCrashError("between A1 and A2")
            return original(cur)

        pipeline._create_staging_indexes = probe


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    # migrations/env.py reads settings.database_url: point it at the child DB.
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
        )
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    return isolated_pg_dsn


@pytest.fixture()
def db(migrated):
    """Empty production tables, no candidate, flat, previous generation or journal."""
    with psycopg.connect(migrated) as conn:
        for table in (
            ARCEP_FLAT,
            "eligibilite_fixe_staging",
            "eligibilite_fixe_old",
            CADASTRE_FLAT,
            "parcelles_staging",
            "parcelles_old",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        conn.execute("TRUNCATE eligibilite_fixe, parcelles")
        conn.execute("DELETE FROM etl_publications")
        conn.execute("DELETE FROM etl_old_tables")
    return migrated


# -- ARCEP through the real runner -------------------------------------------


class TinyArcep(ArcepPipeline):
    """ARCEP stage/swap unchanged; local transformed files instead of a download."""

    def __init__(self, dsn: str, files: list[Path]) -> None:
        super().__init__()
        self._dsn = dsn
        self._paths = files
        self.flat_before_swap: str | None = None

    def get_sync_dsn(self) -> str:
        return self._dsn

    def download(self) -> list[Path]:
        return list(self._paths)

    def validate_schema(self, file_path: Path) -> None:
        pass

    def transform(self, file_path: Path) -> Path:
        return file_path

    @property
    def min_staging_rows(self) -> int:
        return 0  # tiny synthetic stock

    def swap(self) -> None:
        self.flat_before_swap = relation_persistence(self._dsn, ARCEP_FLAT)
        super().swap()


class NoOpRequiredHook:
    required_on_complete = True

    def on_run_start(self, *_args) -> None:
        pass

    def on_run_failure(self, *_args) -> None:
        pass

    def on_run_complete(self, *_args) -> None:
        pass


def arcep_sources(tmp_path: Path) -> list[Path]:
    files = []
    for dept, insee in (("44", "44109"), ("85", "85191")):
        path = tmp_path / f"arcep_{dept}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            for n in range(3):
                writer.writerow(
                    {
                        "id_imb": f"IMB/{insee}/X/{n:04d}",
                        "code_insee": insee,
                        "voie": "RUE DE LA GARE",
                        "numero": str(n + 1),
                        "complement": "",
                        "technologie": "FTTH",
                        "debit_max": "",
                        "operateur": "OP",
                        "code_departement": dept,
                    }
                )
        files.append(path)
    return files


def run_arcep(pipeline: TinyArcep, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    scope = InstanceScope(frozenset({"connectivite"}), ("44", "85"))
    with (
        _patch_all(staging_exists=False, checkpoint_data=None),
        patch("app.etl.runner.Publication", Publication),
        patch("app.etl.runner.dataset_execution_lock", dataset_execution_lock),
        patch("app.etl.runner.requires_fresh_start", requires_fresh_start),
        patch("app.etl.runner.settings.source_archive_dir", ""),
        patch.object(PipelineRunner, "_staging_table_exists", _REAL_STAGING_EXISTS),
        patch.object(CheckpointManager, "load", _REAL_CHECKPOINT["load"]),
        patch.object(CheckpointManager, "save", _REAL_CHECKPOINT["save"]),
        patch.object(CheckpointManager, "clear", _REAL_CHECKPOINT["clear"]),
    ):
        runner = PipelineRunner([NoOpRequiredHook()], scope_loader=lambda _: scope)
        return runner.run(pipeline)


def published_arcep(dsn: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        # One row per building since plan 04.1-16: its offers read back.
        return conn.execute(
            "SELECT e.id_imb, e.code_insee, e.voie, e.numero, e.complement, "
            "o.technologie, o.operateur, e.code_departement FROM eligibilite_fixe e "
            "CROSS JOIN LATERAL unnest(e.offres) AS u(code) "
            "JOIN eligibilite_offres o ON o.code = u.code ORDER BY e.id_imb, u.code"
        ).fetchall()


def test_arcep_flat_is_gone_and_candidate_logged_when_indexes_start(
    db, tmp_path, monkeypatch
):
    pipeline = TinyArcep(db, arcep_sources(tmp_path))
    probe = IndexProbe(pipeline, db, ARCEP_FLAT)

    result = run_arcep(pipeline, tmp_path, monkeypatch)

    assert result.success, result.error
    assert pipeline.flat_before_swap == "u"
    assert probe.observed == [(None, {"p"})]
    assert len(published_arcep(db)) == 6
    assert tree_persistence(db, "eligibilite_fixe") == {"p"}


@pytest.mark.parametrize("where", ["between-a1-a2", "inside-a1"])
def test_arcep_interrupted_import_republishes_the_same_stock(
    db, tmp_path, monkeypatch, where
):
    sources = arcep_sources(tmp_path)
    reference = TinyArcep(db, sources)
    assert run_arcep(reference, tmp_path, monkeypatch).success
    expected = published_arcep(db)
    assert len(expected) == 6

    interrupted = TinyArcep(db, sources)
    if where == "between-a1-a2":
        IndexProbe(interrupted, db, ARCEP_FLAT, interrupt=True)
    else:

        def crash(*_args, **_kwargs):
            raise SimulatedCrashError("inside A1, after the transfer")

        interrupted.count_production_rows = crash
    failed = run_arcep(interrupted, tmp_path, monkeypatch)
    assert not failed.success
    if where == "between-a1-a2":
        # A1 committed: candidate loaded and logged, flat table gone, so the
        # runner's resume evidence (candidate AND flat) cannot be reused.
        assert relation_persistence(db, ARCEP_FLAT) is None
        assert tree_persistence(db, "eligibilite_fixe_staging") == {"p"}
    else:
        # A1 rolled back: the flat rows of the failed run are still there.
        assert relation_persistence(db, ARCEP_FLAT) == "u"
    assert published_arcep(db) == expected

    resumed = TinyArcep(db, sources)
    result = run_arcep(resumed, tmp_path, monkeypatch)

    assert result.success, result.error
    assert published_arcep(db) == expected


# -- cadastre through its real stage() and swap() ----------------------------


class TinyCadastre(CadastrePipeline):
    def __init__(self, dsn: str) -> None:
        super().__init__()
        self._dsn = dsn
        # The runner binds the scope; here one departement, as on 44/85.
        self.bind_scope(InstanceScope(frozenset({"cadastre"}), ("44",)))

    def get_sync_dsn(self) -> str:
        return self._dsn


def fake_ogr2ogr(dsn: str):
    """Stand-in for ogr2ogr -append: loads the JSON rows named in argv."""

    def run(cmd, **_kwargs):
        source = next(arg for arg in cmd if str(arg).startswith("/vsigzip/"))
        rows = json.loads(Path(source.removeprefix("/vsigzip/")).read_text())
        with psycopg.connect(dsn) as conn:
            for row in rows:
                conn.execute(
                    f"INSERT INTO {CADASTRE_FLAT} (id, commune, prefixe, section, "
                    "numero, contenance, created, updated, geometrie) VALUES "
                    "(%s, %s, '000', 'AB', %s, 100, '2024-01-01', '2024-01-02', "
                    "ST_Multi(ST_GeomFromText(%s, 4326)))",
                    (row["id"], row["commune"], row["numero"], row["wkt"]),
                )

        class Done:
            stderr = ""

        return Done()

    return run


def cadastre_source(tmp_path: Path) -> Path:
    rows = [
        {
            "id": f"44109000AB{n:04d}",
            "commune": "44109",
            "numero": f"{n:04d}",
            "wkt": f"POLYGON(({-1.55 + n / 1000} 47.2, {-1.549 + n / 1000} 47.2, "
            f"{-1.549 + n / 1000} 47.201, {-1.55 + n / 1000} 47.2))",
        }
        for n in range(4)
    ]
    path = tmp_path / "cadastre-44109-parcelles.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def stage_cadastre(pipeline: TinyCadastre, dsn: str, source: Path) -> None:
    pipeline.create_staging_table(dsn)
    with patch("app.etl_cadastre.pipeline.subprocess.run", fake_ogr2ogr(dsn)):
        assert pipeline.stage(source) == 4


def published_cadastre(dsn: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT id_parcelle, commune, numero, code_departement, "
            "ST_AsText(geometrie) FROM parcelles ORDER BY id_parcelle"
        ).fetchall()


def test_cadastre_flat_is_gone_and_candidate_logged_when_indexes_start(db, tmp_path):
    source = cadastre_source(tmp_path)
    pipeline = TinyCadastre(db)
    stage_cadastre(pipeline, db, source)
    assert relation_persistence(db, CADASTRE_FLAT) == "u"
    probe = IndexProbe(pipeline, db, CADASTRE_FLAT)

    pipeline.swap()

    assert probe.observed == [(None, {"p"})]
    assert len(published_cadastre(db)) == 4
    assert tree_persistence(db, "parcelles") == {"p"}
    assert relation_persistence(db, CADASTRE_FLAT) is None


def test_cadastre_interruption_between_a1_and_a2_rebuilds_from_sources(db, tmp_path):
    source = cadastre_source(tmp_path)
    reference = TinyCadastre(db)
    stage_cadastre(reference, db, source)
    reference.swap()
    expected = published_cadastre(db)

    interrupted = TinyCadastre(db)
    stage_cadastre(interrupted, db, source)
    IndexProbe(interrupted, db, CADASTRE_FLAT, interrupt=True)
    with pytest.raises(SimulatedCrashError):
        interrupted.swap()
    # The runner reuses a candidate only when it AND its flat table exist.
    assert PipelineRunner._staging_table_exists(db, "parcelles_staging")
    assert not PipelineRunner._staging_table_exists(db, CADASTRE_FLAT)
    assert published_cadastre(db) == expected

    rebuilt = TinyCadastre(db)
    stage_cadastre(rebuilt, db, source)
    rebuilt.swap()

    assert published_cadastre(db) == expected
