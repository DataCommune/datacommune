"""Departmental pipeline on the persisted scope: download list and swap gate.

Disposable wrapper database only. DVF runs end to end through the runner with
an explicit loader reading the real scope tables; the only substitute is the
HTTP download (``verified_download_gz`` as bound in the DVF pipeline), which
records every requested URL and writes a minimal valid geo-DVF CSV instead.
"""

import csv
import re

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_dvf.config import COLUMNS_TO_KEEP, MARQUEUR_ADRESSE_SUFFIXE, YEARS
from app.etl_dvf.pipeline import DvfPipeline
from app.scope import load_scope_sync
from tests.integration.test_instance_scope import BACKEND

pytestmark = pytest.mark.destructive

_URL = re.compile(r"/(?P<year>\d{4})/departements/(?P<dept>[0-9AB]+)\.csv\.gz$")
# One commune per departement: code, parcel prefix, coordinates.
_COMMUNES = {
    "44": ("44109", "44109000AB", -1.5534, 47.2184),
    "85": ("85191", "85191000AC", -1.4269, 46.6705),
    "57": ("57463", "57463000AD", 6.1757, 49.1193),
}


def db_scope_loader(dsn):
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


def _dvf_row(year, dept, index):
    commune, parcel, lon, lat = _COMMUNES[dept]
    row = dict.fromkeys(COLUMNS_TO_KEEP, "")
    row.update(
        id_mutation=f"{year}-{dept}-{index}",
        date_mutation=f"{year}-03-1{index}",
        nature_mutation="Vente",
        valeur_fonciere="185000",
        adresse_numero="1",
        adresse_nom_voie="RUE DU TEST",
        code_postal=f"{dept}000",
        code_commune=commune,
        nom_commune="Commune test",
        id_parcelle=f"{parcel}{index:04d}",
        code_type_local="2",
        type_local="Appartement",
        surface_reelle_bati="60",
        nombre_pieces_principales="3",
        longitude=str(lon),
        latitude=str(lat),
        numero_disposition="000001",
        nombre_lots="1",
    )
    return row


class FakeDvfSource:
    """Records requested URLs; departements in *empty* publish no mutation."""

    def __init__(self, empty=(), per_file=2):
        self.empty = set(empty)
        self.per_file = per_file
        self.requested = []

    async def __call__(self, client, url, output_path):
        self.requested.append(url)
        match = _URL.search(url)
        assert match, url
        year, dept = int(match["year"]), match["dept"]
        with open(output_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS_TO_KEEP)
            writer.writeheader()
            if dept not in self.empty:
                for index in range(1, self.per_file + 1):
                    writer.writerow(_dvf_row(year, dept, index))
        return True

    @property
    def departements(self):
        return {_URL.search(url)["dept"] for url in self.requested}


@pytest.fixture
def env(migrated, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    # Two mutations per (year, departement); the scaled floor of 44+85 is
    # 98 039 rows. Floors are covered by the unit suite: here only the
    # download list and the departement guard are under test.
    monkeypatch.setattr(DvfPipeline, "min_staging_rows", property(lambda _: 1))
    return migrated


def run_dvf(monkeypatch, source):
    monkeypatch.setattr("app.etl_dvf.pipeline.verified_download_gz", source)
    runner = PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=db_scope_loader
    )
    return runner.run(DvfPipeline())


def published(dsn):
    with psycopg.connect(dsn) as conn:
        partitions = conn.execute(
            "SELECT tableoid::regclass::text, code_departement, count(*) "
            "FROM mutations GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        fingerprint = conn.execute(
            "SELECT 'mutations'::regclass::oid, count(*), "
            "md5(coalesce(string_agg(m::text, '|' ORDER BY m::text), '')) "
            "FROM mutations m"
        ).fetchone()
    return partitions, fingerprint


def test_a_download_and_publication_follow_the_scope(env, monkeypatch, instance_scope):
    instance_scope(env, ["dvf"], ["44", "85"])
    source = FakeDvfSource()
    result = run_dvf(monkeypatch, source)
    assert result.success, result.error
    assert source.departements == {"44", "85"}
    assert len(source.requested) == len(YEARS) * 2
    partitions, _ = published(env)
    assert partitions == [
        ("mutations_44", "44", len(YEARS) * 2),
        ("mutations_85", "85", len(YEARS) * 2),
    ]
    # C44 (review 04.1-19 WR-01): the import marks the column it loaded with
    # the suffix, and the reconstruction of that source marks the grain.
    with psycopg.connect(env) as conn:
        marques = conn.execute(
            "SELECT c.relname, col_description(a.attrelid, a.attnum) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.oid IN ('mutations'::regclass, 'mutations_grain'::regclass) "
            "AND a.attname = 'adresse_suffixe' ORDER BY 1"
        ).fetchall()
    assert marques == [
        ("mutations", MARQUEUR_ADRESSE_SUFFIXE),
        ("mutations_grain", MARQUEUR_ADRESSE_SUFFIXE),
    ]


def test_a_bis_the_period_of_the_run_is_the_window_of_mutation_dates(
    env, monkeypatch, instance_scope
):
    """C24 (a), WINDOWS n° 86: the DVF label is the window actually published."""
    instance_scope(env, ["dvf"], ["44", "85"])
    result = run_dvf(monkeypatch, FakeDvfSource())
    assert result.success, result.error
    with psycopg.connect(env) as conn:
        (label,) = conn.execute(
            "SELECT source_period_label FROM dataset_metadata "
            "WHERE dataset_name = 'dvf'"
        ).fetchone()
    # Two mutations per (year, departement): the 11th and the 12th of March.
    assert label == f"{YEARS[0]}-03-11 → {YEARS[-1]}-03-12"


def test_b_departement_outside_source_is_never_requested(
    env, monkeypatch, instance_scope
):
    instance_scope(env, ["dvf"], ["44", "85", "57"])
    source = FakeDvfSource()
    result = run_dvf(monkeypatch, source)
    assert result.success, result.error
    # 57 is configured but DVF does not publish Alsace-Moselle.
    assert source.departements == {"44", "85"}
    partitions, _ = published(env)
    assert {dept for _, dept, _ in partitions} == {"44", "85"}


def test_c_missing_configured_departement_refuses_swap(
    env, monkeypatch, instance_scope
):
    instance_scope(env, ["dvf"], ["44", "85"])
    _, before = published(env)
    assert before[1] > 0
    # 44 doubled: the total stays level with production, so the equal-scope
    # row regression passes and the refusal comes from the departement guard.
    source = FakeDvfSource(empty={"85"}, per_file=4)
    result = run_dvf(monkeypatch, source)
    assert not result.success
    assert result.error == (
        "Department coverage: missing scoped departments ['85'] in "
        "_mutations_staging_flat -- swap aborted"
    )
    assert source.departements == {"44", "85"}
    assert "swap" not in {stage.stage_name for stage in result.stages}
    _, after = published(env)
    assert after == before
