"""The fidelity measure read against a real reconstruction (plan 04.4-03).

Disposable wrapper database only. The fixture files of
``tests/test_mesure_dvf_fidelite.py`` go through the real runner (transform,
load, reconstruction); the only substitute is the HTTP download. The same
files, archived, give the Python fingerprints of ``mesure_dvf_fidelite``;
``EMPREINTE_SQL`` read on ``mutations_grain`` must give the same lines.
"""

import csv
import io
import json
import re

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_dvf.config import COLUMNS_TO_KEEP
from app.etl_dvf.pipeline import DvfPipeline
from app.scope import load_scope_sync
from scripts import mesure_dvf_fidelite as m
from tests.integration.test_instance_scope import BACKEND
from tests.test_mesure_dvf_fidelite import ROWS_44, ROWS_85, RUN, write_archive

pytestmark = pytest.mark.destructive

_URL = re.compile(r"/(?P<year>\d{4})/departements/(?P<dept>[0-9AB]+)\.csv\.gz$")
# The line without commune code (SYN-I) belongs to no hosted departement.
_FILES = {
    ("2021", "44"): ROWS_44,
    ("2021", "85"): [r for r in ROWS_85 if r["code_commune"]],
}


def _db_scope_loader(dsn):
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        return load_scope_sync(conn)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    return isolated_pg_dsn


async def _fixture_download(client, url, output_path):
    match = _URL.search(url)
    assert match, url
    rows = _FILES.get((match["year"], match["dept"]), [])
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS_TO_KEEP)
        writer.writeheader()
        writer.writerows(rows)
    return True


@pytest.fixture(scope="module")
def imported(migrated):
    """The fixture imported once by the real runner; the module reads it."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", migrated)
        mp.setattr(settings, "database_url", migrated)
        mp.setattr(settings, "source_archive_dir", "")
        mp.setattr(DvfPipeline, "min_staging_rows", property(lambda _: 1))
        mp.setattr("app.etl_dvf.pipeline.verified_download_gz", _fixture_download)
        from app.scope import replace_scope

        with psycopg.connect(migrated, connect_timeout=5) as conn:
            replace_scope(conn, ["dvf"], ["44", "85"])
        runner = PipelineRunner(
            [ValidationHook(), DependentMatviewHook()], scope_loader=_db_scope_loader
        )
        result = runner.run(DvfPipeline())
    assert result.success, result.error
    return migrated


@pytest.fixture
def archive(tmp_path):
    files = {f"raw/{y}/{d}.csv": rows for (y, d), rows in _FILES.items()}
    return write_archive(tmp_path / "dvf", files)


def _python_fingerprints(archive):
    out = io.StringIO()
    m.mode_empreintes(str(archive), RUN, out)
    return sorted(row[:-1] for row in csv.reader(io.StringIO(out.getvalue())))


def test_sql_fingerprint_equals_the_python_one_line_by_line(imported, archive):
    with psycopg.connect(imported) as conn:
        base = sorted(list(row) for row in conn.execute(m.EMPREINTE_SQL).fetchall())
    python = _python_fingerprints(archive)
    assert len(base) == 10  # the 10 mutations of the hosted departements
    assert base == python


def test_sql_base_mode_runs_as_a_copy_on_the_base(imported, archive):
    sql = m.mode_sql_base()
    buffer = io.BytesIO()
    with (
        psycopg.connect(imported) as conn,
        conn.cursor() as cur,
        cur.copy(sql.rstrip(";")) as copy,
    ):
        for chunk in copy:
            buffer.write(chunk)
    rows = sorted(csv.reader(io.StringIO(buffer.getvalue().decode("utf-8"))))
    assert rows == _python_fingerprints(archive)


def test_predicted_after_counts_equal_v_commune_dvf(imported, archive):
    """The view rebuilt by the import is the state predicted ``apres``."""
    with psycopg.connect(imported) as conn:
        rows = conn.execute(m.SNAPSHOT_COMMUNES_SQL).fetchall()
    lue = {row[0]: tuple(row[1:4]) for row in rows}
    predite = {
        code: tuple(etat["apres"][k] for k in m.COMPTES_COMMUNES)
        for code, etat in m.mode_archive(str(archive), RUN)["communes"].items()
        if etat["apres"]["nb_mutations_12m"]
    }
    assert lue == predite == {"44109": (4, 3, 1), "85191": (2, 1, 1)}


def test_kpi_reads_the_snapshot_copied_from_the_base(imported, archive, tmp_path):
    buffer = io.BytesIO()
    with (
        psycopg.connect(imported) as conn,
        conn.cursor() as cur,
        cur.copy(m.mode_sql_base(communes=True).rstrip(";")) as copy,
    ):
        for chunk in copy:
            buffer.write(chunk)
    apres = tmp_path / "apres.csv"
    apres.write_bytes(buffer.getvalue())
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps(m.mode_archive(str(archive), RUN)), "utf-8")
    report = m.mode_kpi(str(apres), str(apres), str(reference))
    # The base holds the state after; read as the served one, both differ.
    assert report["ecarts_comptes_apres"] == 0
    assert report["ecarts_comptes_servi"] == 2


@pytest.mark.parametrize(
    "date", ["2025-12-31", "2024-02-29", "2024-03-31", "2021-01-31"]
)
def test_window_start_equals_postgresql(imported, date):
    with psycopg.connect(imported) as conn:
        (debut,) = conn.execute(
            "SELECT (%s::date - INTERVAL '12 months')::date::text", (date,)
        ).fetchone()
    assert m.debut_fenetre(date) == debut
