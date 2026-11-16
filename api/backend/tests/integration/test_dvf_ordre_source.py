"""Plan 04.4-05: the ``id`` of ``mutations`` follows the order of the source lines.

The reconstruction reads the address of a mutation on its first line by
``id`` (D-04), and the archive reference models that line as the first source
line. On the work base, 35 of 294 998 mutations served another address: the
``INSERT ... SELECT`` from ``_mutations_staging_flat`` had no ``ORDER BY``, so
``id`` followed the physical order of the flat table, not the COPY order.

Here the physical order is made to differ from the COPY order on purpose: an
``UPDATE`` of the first line of each mutation, just before the transfer, moves
that line after the others in the heap. The fixture goes through the real
runner (download replaced by the fixture writer), then the base is compared
with the reference of ``mesure_dvf_fidelite`` read on the same files. The
resume path (a crash after a checkpoint, the flat table reused) and a resume
over a flat table left by the previous code are covered too. Synthetic data
only (D-31, ``SYN-`` identifiers).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dvf_ordre_source.py
"""

import csv
import inspect
import io
import json
import re
from typing import ClassVar

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl.base import BasePipeline
from app.etl.checkpoint import CheckpointManager
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_dvf import pipeline as pipeline_module
from app.etl_dvf.config import COLUMNS_TO_KEEP
from app.etl_dvf.pipeline import DvfPipeline
from app.scope import load_scope_sync, replace_scope
from scripts import mesure_dvf_fidelite as m
from tests.integration.test_instance_scope import BACKEND
from tests.test_mesure_dvf_fidelite import RUN, write_archive

pytestmark = pytest.mark.destructive

_URL = re.compile(r"/(?P<year>\d{4})/departements/(?P<dept>[0-9AB]+)\.csv\.gz$")


def _parcel(n: int) -> str:
    return f"44SYN0000R{n:04d}"


def _line(id_mutation: str, n: int, street: str, **fields: str) -> dict[str, str]:
    row = dict.fromkeys(COLUMNS_TO_KEEP, "")
    row.update(
        id_mutation=id_mutation,
        date_mutation="2021-06-15",
        numero_disposition="000001",
        nature_mutation="Vente",
        valeur_fonciere="200000",
        adresse_numero=str(n),
        adresse_nom_voie=street,
        code_postal="44000",
        code_commune="44999",
        nom_commune="SYN-COMMUNE",
        id_parcelle=_parcel(n),
        longitude="-1.55",
        latitude="47.21",
    )
    row.update(fields)
    return row


_HOUSE = {
    "code_type_local": "1",
    "type_local": "Maison",
    "surface_reelle_bati": "100",
    "nombre_pieces_principales": "4",
}

# Every mutation has two lines in one commune with two addresses, A then B:
# the address served must be A, the first source line.
_FILES = {
    ("2021", "44"): [
        _line("SYN-ORD-1", 1, "SYN-VOIE-A", **_HOUSE),
        _line("SYN-ORD-1", 2, "SYN-VOIE-B"),
        _line("SYN-ORD-2", 3, "SYN-VOIE-C"),
        _line("SYN-ORD-2", 4, "SYN-VOIE-D", **_HOUSE),
    ],
    ("2022", "44"): [
        _line("SYN-ORD-3", 5, "SYN-VOIE-E", date_mutation="2022-03-01"),
        _line("SYN-ORD-3", 6, "SYN-VOIE-F", date_mutation="2022-03-01"),
    ],
}
# The source order, file by file of the record, line by line.
_SOURCE_ORDER = [row["id_parcelle"] for rows in _FILES.values() for row in rows]
# The first line of each mutation, moved to the end of the heap.
_FIRST_LINES = ("1", "3", "5")
_SERVED_ADDRESS = {
    "SYN-ORD-1": ("1", "SYN-VOIE-A"),
    "SYN-ORD-2": ("3", "SYN-VOIE-C"),
    "SYN-ORD-3": ("5", "SYN-VOIE-E"),
}


async def _fixture_download(client, url, output_path):
    """The fixture for 2021/44 and 2022/44, an empty valid file otherwise."""
    match = _URL.search(url)
    assert match, url
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS_TO_KEEP)
        writer.writeheader()
        writer.writerows(_FILES.get((match["year"], match["dept"]), []))
    return True


class _ReorderingDvfPipeline(DvfPipeline):
    """The real pipeline, with the flat table reordered before the transfer."""

    physical_orders: ClassVar[list[list[str]]] = []

    def swap(self) -> None:
        with psycopg.connect(self.get_sync_dsn()) as conn:
            moved = conn.execute(
                "UPDATE _mutations_staging_flat SET adresse_numero = adresse_numero "
                "WHERE adresse_numero = ANY(%s)",
                (list(_FIRST_LINES),),
            ).rowcount
            assert moved == len(_FIRST_LINES)
            physical = [
                row[0]
                for row in conn.execute(
                    "SELECT id_parcelle FROM _mutations_staging_flat ORDER BY ctid"
                )
            ]
        type(self).physical_orders.append(physical)
        super().swap()


def _scope_loader(dsn):
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        return load_scope_sync(conn)


def _runner() -> PipelineRunner:
    return PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=_scope_loader
    )


@pytest.fixture(scope="module")
def env(isolated_pg_dsn, tmp_path_factory):
    """Migrated base, DVF scope 44, the runner's inputs replaced by the fixture."""
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        context.setattr(settings, "etl_temp_dir", str(tmp_path_factory.mktemp("etl")))
        context.setattr(settings, "source_archive_dir", "")
        command.upgrade(cfg, "head")
        with psycopg.connect(isolated_pg_dsn, connect_timeout=5) as conn:
            replace_scope(conn, ["dvf"], ["44"])
        context.setattr(DvfPipeline, "min_staging_rows", property(lambda _: 1))
        context.setattr("app.etl_dvf.pipeline.verified_download_gz", _fixture_download)
        yield isolated_pg_dsn


@pytest.fixture(scope="module")
def imported(env):
    """One complete import by the real runner, the flat table reordered."""
    _ReorderingDvfPipeline.physical_orders.clear()
    result = _runner().run(_ReorderingDvfPipeline())
    assert result.success, result.error
    return env


@pytest.fixture
def archive(tmp_path):
    files = {f"raw/{y}/{d}.csv": rows for (y, d), rows in _FILES.items()}
    return write_archive(tmp_path / "dvf", files)


def _ids_order(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        return [
            row[0]
            for row in conn.execute("SELECT id_parcelle FROM mutations ORDER BY id")
        ]


def _served_addresses(dsn: str) -> dict[str, tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id_mutation, adresse_numero, adresse_nom_voie "
            "FROM mutations_grain ORDER BY id_mutation"
        ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _base_fingerprints(dsn: str) -> list[list[str]]:
    with psycopg.connect(dsn) as conn:
        return sorted(
            [m.NULL if v is None else str(v) for v in row]
            for row in conn.execute(m.EMPREINTE_SQL).fetchall()
        )


def _reference_fingerprints(archive) -> list[list[str]]:
    out = io.StringIO()
    m.mode_empreintes(str(archive), RUN, out)
    return sorted(row[:-1] for row in csv.reader(io.StringIO(out.getvalue())))


def test_the_transfer_takes_id_from_the_source_ordinal_without_sorting():
    """Structural guard: ``id`` is the COPY ordinal of the flat table, never
    its physical order, and the transfer sorts nothing (WR-1: a sort of the
    whole flat table spills to temporary files nationally)."""
    source = inspect.getsource(pipeline_module.DvfPipeline.swap)
    insert, rest = source.split("FROM _mutations_staging_flat", 1)
    insert = insert.rsplit("INSERT INTO mutations_staging", 1)[1]
    transfer = rest.split('"""', 1)[0]
    assert re.search(r"\(\s*id,", insert)
    assert re.search(r"SELECT\s+source_line,", insert)
    assert "ORDER BY" not in transfer
    assert "setval('mutations_staging_id_seq', max(id))" in rest
    stage = inspect.getsource(pipeline_module.DvfPipeline.stage)
    assert "source_line BIGINT GENERATED ALWAYS AS IDENTITY" in stage


def test_the_fixture_does_reorder_the_flat_table(imported):
    """Precondition of the red: before the transfer, the physical order of the
    flat table is not the COPY order."""
    (physical,) = _ReorderingDvfPipeline.physical_orders
    assert sorted(physical) == sorted(_SOURCE_ORDER)
    assert physical != _SOURCE_ORDER


def test_ids_follow_the_source_order(imported):
    assert _ids_order(imported) == _SOURCE_ORDER


def test_the_served_address_is_that_of_the_first_source_line(imported):
    assert _served_addresses(imported) == _SERVED_ADDRESS


def test_the_base_equals_the_archive_reference(imported, archive):
    """Arrival control: the reference of plan 03 (``qualification`` plus the
    ``empreintes`` mode of the archive) on the same files equals the base."""
    base = _base_fingerprints(imported)
    assert len(base) == len(_SERVED_ADDRESS)
    assert base == _reference_fingerprints(archive)


# -- resume: the flat table of an interrupted run is reused -------------------


class _SimulatedCrash(BaseException):
    """Bypass the runner's per-file error handling, like a process death."""


def _crash_after_2021(monkeypatch) -> None:
    """Interrupt the run right after the checkpoint of file 2021/44.csv."""
    original = CheckpointManager.save

    def save_then_crash(self, loaded_files, **kwargs):
        original(self, loaded_files, **kwargs)
        if kwargs.get("pending") is None and "2021/44.csv" in loaded_files:
            raise _SimulatedCrash

    monkeypatch.setattr(CheckpointManager, "save", save_then_crash)


def _interrupted_run(env) -> None:
    with pytest.MonkeyPatch.context() as interrupt:
        _crash_after_2021(interrupt)
        with pytest.raises(_SimulatedCrash):
            _runner().run(_ReorderingDvfPipeline())
    with psycopg.connect(env) as conn:
        (staged,) = conn.execute(
            "SELECT count(*) FROM _mutations_staging_flat"
        ).fetchone()
    assert staged == len(_FILES[("2021", "44")])


def test_a_resumed_run_keeps_the_source_order(imported, archive):
    _interrupted_run(imported)
    _ReorderingDvfPipeline.physical_orders.clear()

    result = _runner().run(_ReorderingDvfPipeline())

    assert result.success, result.error
    names = [stage.stage_name for stage in result.stages]
    assert "resume_stock" in names
    assert "create_staging" not in names
    assert _ReorderingDvfPipeline.physical_orders[0] != _SOURCE_ORDER
    assert _ids_order(imported) == _SOURCE_ORDER
    assert _served_addresses(imported) == _SERVED_ADDRESS
    assert _base_fingerprints(imported) == _reference_fingerprints(archive)


def test_a_flat_table_left_by_the_previous_code_is_rebuilt_not_resumed(
    imported, archive
):
    """A run interrupted before the deployment left a flat table without the
    ordinal and a checkpoint of the former identity: it is rebuilt."""
    _interrupted_run(imported)
    pipeline = DvfPipeline()
    pipeline.bind_scope(_scope_loader(imported))
    checkpoint = CheckpointManager(pipeline.dataset_name, pipeline.staging_table_name)
    data = json.loads(checkpoint.path.read_text(encoding="utf-8"))
    data["identity_key"] = BasePipeline.checkpoint_identity(
        pipeline, pipeline.checkpoint_scope_key
    )
    checkpoint.path.write_text(json.dumps(data), encoding="utf-8")
    with psycopg.connect(imported) as conn:
        conn.execute(
            "ALTER TABLE _mutations_staging_flat DROP COLUMN IF EXISTS source_line"
        )

    result = _runner().run(_ReorderingDvfPipeline())

    assert result.success, result.error
    names = [stage.stage_name for stage in result.stages]
    assert "create_staging" in names
    assert "resume_stock" not in names
    assert _ids_order(imported) == _SOURCE_ORDER
    assert _base_fingerprints(imported) == _reference_fingerprints(archive)
