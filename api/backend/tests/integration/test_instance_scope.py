"""Instance scope in the database -> runner -> filtered LOVAC import -> journal.

Disposable wrapper database only. Every runner here receives an explicit
loader that reads the real scope tables on its own connection; the substitute
installed by ``tests/conftest.py`` for other in-process runners is never used.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from app.etl.errors import DatasetNotHostedError
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_vacance.pipeline import VacancePipeline
from app.scope import load_scope_sync
from tests.integration.test_frozen_source_cli import CLI_BOOTSTRAP, source_manifest
from tests.integration.test_publication_restore import _command, _owned_container
from tests.oracles.loyers_full import FIELDS as RENT_FIELDS

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests/fixtures/cor08_lovac"
SOURCE = FIXTURES / "official-communes26.csv"


def db_scope_loader(dsn):
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        return load_scope_sync(conn)


def alembic_config(dsn):
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(alembic_config(isolated_pg_dsn), "head")
    return isolated_pg_dsn


@pytest.fixture
def env(migrated, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    # The official extract holds 5 communes; the floors of 01 and 44 are 137
    # and 165 rows. Floors are covered by the unit suite; here only the scope
    # filter and the departement guard are under test.
    monkeypatch.setattr(VacancePipeline, "min_staging_rows", property(lambda _: 1))
    monkeypatch.setattr(VacancePipeline, "departement_min_rows", lambda _, code: 1)
    monkeypatch.setattr(VacancePipeline, "national_file_min_rows", None)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "dataset": "vacance",
                "vintage": 2026,
                "source_contract": "lovac-communes-v1:2026:2025",
                "files": [
                    {
                        "kind": "communes",
                        "path": str(SOURCE),
                        "bytes": SOURCE.stat().st_size,
                        "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return migrated, manifest


def run_vacance(manifest, *, pipeline=None):
    pipeline = pipeline or VacancePipeline.from_manifest(manifest)
    runner = PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=db_scope_loader
    )
    return runner.run(pipeline)


def published(dsn):
    with psycopg.connect(dsn) as conn:
        departements = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT code_departement FROM vacance"
            ).fetchall()
        }
        fingerprint = conn.execute(
            "SELECT 'vacance'::regclass::oid, count(*), "
            "md5(coalesce(string_agg(v::text, '|' ORDER BY v::text), '')) "
            "FROM vacance v"
        ).fetchone()
    return departements, fingerprint


def last_run(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT status, scope_snapshot, source_files, stages, error_message "
            "FROM etl_runs WHERE dataset_name = 'vacance' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def run_count(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM etl_runs").fetchone()[0]


def test_a_migration_creates_scope_objects_and_downgrades_exactly(
    migrated, monkeypatch
):
    # migrations/env.py reads the application database URL.
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)

    def objects():
        with psycopg.connect(migrated) as conn:
            return conn.execute(
                "SELECT to_regclass('instance_scope_datasets') IS NOT NULL, "
                "to_regclass('instance_scope_departements') IS NOT NULL, "
                "to_regprocedure('dc_departement_from_insee(text)') IS NOT NULL, "
                "(SELECT count(*) FROM information_schema.columns "
                " WHERE table_name = 'etl_runs' "
                " AND column_name IN ('scope_snapshot', 'source_files'))"
            ).fetchone()

    assert objects() == (True, True, True, 2)
    with psycopg.connect(migrated) as conn:
        # 097 is applied; later revisions (098, 02-09) may follow it.
        head = ScriptDirectory.from_config(alembic_config(migrated)).get_current_head()
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            head,
        )
        assert "097_instance_scope" in {
            revision.revision
            for revision in ScriptDirectory.from_config(
                alembic_config(migrated)
            ).iterate_revisions(head, "base")
        }
        assert conn.execute(
            "SELECT dc_departement_from_insee('2A004'), "
            "dc_departement_from_insee('44109'), dc_departement_from_insee('97411')"
        ).fetchone() == ("2A", "44", "974")
        # Fresh database: nothing hosted.
        assert load_scope_sync(conn).is_empty
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO instance_scope_departements(code_departement) "
                "VALUES ('99')"
            )
    cfg = alembic_config(migrated)
    command.downgrade(cfg, "096_commune_indicator_build_activation")
    assert objects() == (False, False, False, 0)
    command.upgrade(cfg, "head")
    assert objects() == (True, True, True, 2)


def test_b_replace_scope_is_idempotent(migrated, instance_scope):
    def rows():
        with psycopg.connect(migrated) as conn:
            return (
                conn.execute(
                    "SELECT dataset FROM instance_scope_datasets ORDER BY 1"
                ).fetchall(),
                conn.execute(
                    "SELECT code_departement FROM instance_scope_departements "
                    "ORDER BY 1"
                ).fetchall(),
            )

    first = instance_scope(migrated, ["vacance", "dvf"], ["85", "44"])
    before = rows()
    second = instance_scope(migrated, ["dvf", "vacance"], ["44", "85", "44"])
    assert first == second
    assert rows() == before == ([("dvf",), ("vacance",)], [("44",), ("85",)])


def test_c_vacance_import_is_filtered_and_journaled(env, instance_scope):
    dsn, manifest = env
    instance_scope(dsn, ["vacance"], ["01", "44"])
    result = run_vacance(manifest)
    assert result.success, result.error
    departements, fingerprint = published(dsn)
    assert departements == {"01", "44"}
    assert fingerprint[1] == 4  # 02343 excluded
    stages = {stage.stage_name: stage for stage in result.stages}
    assert stages["scope_filter"].rows_affected == 1
    assert result.total_rows_loaded == 4
    status, snapshot, source_files, journal_stages, _ = last_run(dsn)
    assert status == "success"
    assert snapshot == {"datasets": ["vacance"], "departements": ["01", "44"]}
    fixture_manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
    assert source_files == [
        {
            "name": "lovac_communes.csv",
            "bytes": SOURCE.stat().st_size,
            "sha256": fixture_manifest["fixture_sha256"],
            "physical_lines": 5,
        }
    ]
    assert {stage["stage_name"]: stage["rows_affected"] for stage in journal_stages}[
        "scope_filter"
    ] == 1


def test_d_dataset_outside_scope_is_neither_downloaded_nor_journaled(
    env, instance_scope, monkeypatch
):
    dsn, manifest = env
    instance_scope(dsn, ["dvf"], ["01", "44"])
    before_runs = run_count(dsn)
    before = published(dsn)
    downloads = []
    monkeypatch.setattr(
        VacancePipeline, "download", lambda self: downloads.append(1) or []
    )
    with pytest.raises(DatasetNotHostedError, match="vacance"):
        run_vacance(manifest)
    assert downloads == []
    assert run_count(dsn) == before_runs
    assert published(dsn) == before


def test_e_scope_change_during_import_does_not_affect_it(
    env, instance_scope, monkeypatch
):
    dsn, manifest = env
    instance_scope(dsn, ["vacance"], ["01", "44"])
    original = VacancePipeline.download

    def download_then_change_scope(self):
        files = original(self)
        # Another operator narrows the scope between download and swap.
        instance_scope(dsn, ["vacance"], ["44"])
        return files

    monkeypatch.setattr(VacancePipeline, "download", download_then_change_scope)
    result = run_vacance(manifest)
    assert result.success, result.error
    assert published(dsn)[0] == {"01", "44"}
    assert last_run(dsn)[1] == {"datasets": ["vacance"], "departements": ["01", "44"]}
    with psycopg.connect(dsn) as conn:
        assert load_scope_sync(conn).departements == ("44",)


def test_f_missing_configured_departement_refuses_swap(env, instance_scope):
    dsn, manifest = env
    instance_scope(dsn, ["vacance"], ["01", "44"])
    assert run_vacance(manifest).success
    before = published(dsn)
    # The extract has no commune in 85: publishing would be a partial table.
    instance_scope(dsn, ["vacance"], ["44", "85"])
    result = run_vacance(manifest)
    assert not result.success
    assert "85" in result.error and "swap aborted" in result.error
    assert "swap" not in {stage.stage_name for stage in result.stages}
    assert published(dsn) == before
    status, snapshot, *_ = last_run(dsn)
    assert status == "failed"
    assert snapshot == {"datasets": ["vacance"], "departements": ["44", "85"]}


def test_g_unreadable_coverage_guard_refuses_swap(env, instance_scope, monkeypatch):
    dsn, manifest = env
    instance_scope(dsn, ["vacance"], ["01", "44"])
    assert run_vacance(manifest).success
    before = published(dsn)
    coverage_check = PipelineRunner._check_department_coverage
    checks = []

    def unreadable_at_coverage(check_dsn, pipeline, log):
        checks.append(check_dsn)
        # Inject the unreadable relation at this guard only. Earlier source
        # staging, stock counts and scope filtering must remain real and valid.
        with monkeypatch.context() as coverage:
            coverage.setattr(
                VacancePipeline,
                "flat_staging_table_name",
                property(lambda _: "_unreadable"),
            )
            coverage.setattr(
                VacancePipeline,
                "scope_filter_sql",
                property(lambda _: {"_unreadable": "code_departement"}),
            )
            return coverage_check(check_dsn, pipeline, log)

    monkeypatch.setattr(
        PipelineRunner,
        "_check_department_coverage",
        staticmethod(unreadable_at_coverage),
    )
    try:
        result = run_vacance(manifest)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DROP TABLE IF EXISTS _vacance_staging_flat")
    assert not result.success
    assert checks == [dsn]
    stages = {stage.stage_name: stage for stage in result.stages}
    assert stages["staging_stock"].rows_affected == 5
    assert stages["candidate_stock"].rows_affected == 4
    assert result.error == "Department coverage check unreadable -- swap aborted"
    assert "swap" not in {stage.stage_name for stage in result.stages}
    assert published(dsn) == before


def test_h_narrower_scope_compares_at_equal_scope(env, instance_scope):
    dsn, manifest = env
    instance_scope(dsn, ["vacance"], ["01", "44"])
    assert run_vacance(manifest).success
    assert published(dsn)[1][1] == 4
    # 1 staged row in 44 against 4 published rows overall: every gate must
    # compare with the 1 published row of 44 (D-23), not with the 4.
    instance_scope(dsn, ["vacance"], ["44"])
    result = run_vacance(manifest)
    assert result.success, result.error
    assert result.previous_row_count == 1
    departements, fingerprint = published(dsn)
    assert departements == {"44"} and fingerprint[1] == 1


# -- Operator command and run_etl.py in their own processes (plan 02-05) -----


def subprocess_env(dsn, **extra):
    return os.environ | {
        "DATABASE_URL": dsn.replace("postgresql://", "postgresql+asyncpg://"),
        "DEBUG": "true",
        "ALERT_URLS": "[]",
        "PYTHONPATH": str(BACKEND),
        # Refusal messages are French; read them as UTF-8 on every host.
        "PYTHONIOENCODING": "utf-8",
        **extra,
    }


def scope_cli(dsn, *args):
    return subprocess.run(
        [sys.executable, "-m", "app.scope", *args],
        cwd=BACKEND,
        env=subprocess_env(dsn),
        capture_output=True,
        timeout=60,
    )


def scope_rows(dsn):
    with psycopg.connect(dsn) as conn:
        return (
            conn.execute(
                "SELECT dataset FROM instance_scope_datasets ORDER BY 1"
            ).fetchall(),
            conn.execute(
                "SELECT code_departement FROM instance_scope_departements ORDER BY 1"
            ).fetchall(),
        )


def lines(completed):
    return completed.stdout.decode().splitlines()


def test_i_scope_command_sets_then_shows_and_replays_identically(migrated):
    first = scope_cli(
        migrated, "set", "--datasets", "vacance,loyers", "--departements", "85,44"
    )
    assert first.returncode == 0, first.stderr.decode(errors="replace")
    # No schema lock left (migration 102, 04.1-08): no warning for 85.
    assert lines(first) == ["SCOPE set datasets=2 departements=44,85"]
    written = scope_rows(migrated)
    assert written == ([("loyers",), ("vacance",)], [("44",), ("85",)])

    replay = scope_cli(
        migrated, "set", "--datasets", "loyers,vacance", "--departements", "44,85"
    )
    assert replay.returncode == 0
    assert lines(replay) == lines(first)
    assert scope_rows(migrated) == written

    shown = scope_cli(migrated, "show")
    assert shown.returncode == 0
    assert lines(shown) == [
        "SCOPE datasets=2 departements=44,85",
        "loyers",
        "vacance",
        # No dpe lock since migration 101 (04.1-07), no view lock since 102.
        "SCOPE lock: aucun verrou de schéma",
    ]
    as_json = scope_cli(migrated, "show", "--json")
    assert json.loads(as_json.stdout) == {
        "datasets": ["loyers", "vacance"],
        "departements": ["44", "85"],
    }

    for args, named in (
        (("--datasets", "loyers", "--departements", "44,99"), "99"),
        (("--datasets", "loyers,inconnu", "--departements", "44"), "inconnu"),
    ):
        refused = scope_cli(migrated, "set", *args)
        assert refused.returncode == 2
        assert named in refused.stderr.decode()
        assert scope_rows(migrated) == written


def run_etl_cli(dsn, tmp_path, *args, timeout=180):
    audit = []
    container = _owned_container(dsn, audit)
    free = _command(
        [
            "docker",
            "exec",
            container,
            "df",
            "-B1",
            "--output=avail",
            "/var/lib/postgresql/data",
        ],
        audit,
    )
    temp = tmp_path / "etl"
    temp.mkdir(exist_ok=True)
    env = subprocess_env(
        dsn,
        ETL_TEMP_DIR=str(temp),
        FROZEN_TEST_PG_FREE=free.decode().splitlines()[-1].strip(),
    )
    return subprocess.run(
        [sys.executable, "-c", CLI_BOOTSTRAP, *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        timeout=timeout,
    )


def loyers_run(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT status, scope_snapshot, stages FROM etl_runs "
            "WHERE dataset_name = 'loyers' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def test_j_run_etl_loyers_publishes_only_hosted_departements(
    migrated, tmp_path, instance_scope
):
    manifest, _, oracle, _ = source_manifest(tmp_path, "loyers")
    departement = RENT_FIELDS.index("code_departement")
    hosted = [row for row in oracle if row[departement] in {"44", "85"}]
    assert len(hosted) == 1840 and len(oracle) == 139600
    instance_scope(migrated, ["loyers"], ["44", "85"])
    with psycopg.connect(migrated) as conn:
        # Same synthetic view spine as test_frozen_source_cli: public keys only.
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT code_insee FROM demographie WHERE annee=2025"
            ).fetchall()
        }
        with conn.cursor().copy(
            "COPY demographie(code_insee,annee) FROM STDIN"
        ) as copy:
            for code in sorted({row[0] for row in hosted} - existing):
                copy.write_row((code, 2025))

    completed = run_etl_cli(
        migrated, tmp_path, "loyers", "--source-manifest", str(manifest)
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-6000:]

    columns = ",".join(RENT_FIELDS)
    exact = ",".join(field for field in RENT_FIELDS if field != "r2_ajuste")
    with psycopg.connect(migrated) as conn:
        assert conn.execute(
            "SELECT DISTINCT code_departement FROM indicateurs_loyers ORDER BY 1"
        ).fetchall() == [("44",), ("85",)]
        conn.execute(
            f"CREATE TEMP TABLE hosted_source AS SELECT {columns} "
            "FROM indicateurs_loyers WITH NO DATA"
        )
        with conn.cursor().copy(f"COPY hosted_source({columns}) FROM STDIN") as copy:
            for row in hosted:
                copy.write_row(row)
        differences = conn.execute(f"""SELECT count(*) FROM (
            (SELECT {exact} FROM indicateurs_loyers
             EXCEPT ALL SELECT {exact} FROM hosted_source)
            UNION ALL (SELECT {exact} FROM hosted_source
                       EXCEPT ALL SELECT {exact} FROM indicateurs_loyers)
        ) d""").fetchone()[0]
        assert differences == 0
    status, snapshot, stages = loyers_run(migrated)
    assert status == "success"
    assert snapshot == {"datasets": ["loyers"], "departements": ["44", "85"]}
    rows = {stage["stage_name"]: stage["rows_affected"] for stage in stages}
    assert rows["scope_filter"] > 0


def test_k_run_etl_refuses_a_dataset_outside_the_scope(
    migrated, tmp_path, instance_scope
):
    manifest, *_ = source_manifest(tmp_path, "loyers")
    instance_scope(migrated, ["vacance"], ["44", "85"])
    before = run_count(migrated)
    completed = run_etl_cli(
        migrated, tmp_path, "loyers", "--source-manifest", str(manifest), timeout=60
    )
    assert completed.returncode == 2
    assert "dataset non hébergé: loyers" in completed.stderr.decode()
    assert run_count(migrated) == before
    assert not (tmp_path / "etl" / "datacommune_loyers").exists()


# -- Every national departement expression against the real schema ----------


def test_l_national_departement_expressions_run_on_head_schema(migrated, monkeypatch):
    import logging

    from app.etl_transport.pipeline import TransportPipeline
    from app.scope import InstanceScope
    from tests.test_scope_filter_declarations import NATIONAL, _new

    monkeypatch.setattr(settings, "database_url", migrated)
    scope = InstanceScope(frozenset(), ("44", "85"))
    for cls in NATIONAL:
        pipeline = _new(cls)
        pipeline.bind_scope(scope)
        # Equal-scope count of the published table (swap safety check); the
        # module database may already hold rows published by earlier tests.
        with psycopg.connect(migrated) as conn, conn.cursor() as cur:
            published = pipeline.count_production_rows(
                cur, pipeline.production_table_name
            )
            if cls is TransportPipeline:
                assert (
                    pipeline.count_production_rows(
                        cur, "gares", pipeline.production_departement_sql
                    )
                    >= 0
                )
        # Runner regression guard reads the same expression: no "unreadable".
        error, previous = PipelineRunner._check_row_regression(
            migrated, pipeline, max(published, 1), logging.getLogger("test")
        )
        assert (error, previous) == (None, published or None), cls
    # Data quality reads the published tables with its own declared
    # departement expression: every one must run on the head schema.
    from app.data_quality.profiles import PROFILES, departement_volume_shortfalls

    with psycopg.connect(migrated) as conn:
        for key, profile in PROFILES.items():
            if profile.volume is not None and profile.volume.is_national:
                assert isinstance(
                    departement_volume_shortfalls(conn, profile, scope), list
                ), key


def test_m_stops_and_stations_are_filtered_by_departement_polygon(
    migrated, monkeypatch, tmp_path
):
    import logging

    from app.etl.errors import ScopePrerequisiteError
    from app.etl_transport.config import ARRETS_OUTPUT_COLUMNS, GARES_OUTPUT_COLUMNS
    from app.etl_transport.pipeline import TransportPipeline
    from app.scope import InstanceScope

    monkeypatch.setattr(settings, "database_url", migrated)
    # One stop per departement here: only presence is under test; the
    # departement floors are covered by the unit suite.
    monkeypatch.setattr(TransportPipeline, "departement_min_rows", lambda _, code: 1)
    monkeypatch.setattr(TransportPipeline, "national_file_min_rows", None)
    log = logging.getLogger("test")
    arrets = tmp_path / "arrets.csv"
    arrets.write_text(
        ",".join(ARRETS_OUTPUT_COLUMNS)
        + "\nnantes,Commerce,47.2130,-1.5560,0,TAN"
        + "\nlyon,Bellecour,45.7578,4.8320,0,TCL"
        + "\nsea,Offshore,47.0000,-3.5000,0,NONE\n",
        encoding="utf-8",
    )
    gares = tmp_path / "gares.csv"
    # SNCF 2025+ file: no CODE_INSEE, coordinates only (as transform writes).
    gares.write_text(
        ",".join(GARES_OUTPUT_COLUMNS)
        + "\nNantes,Nantes,,-1.54,47.21,87481002,,NTS"
        + "\nLyon Part-Dieu,Lyon,,4.86,45.76,87723197,,LPD\n",
        encoding="utf-8",
    )
    pipeline = TransportPipeline()
    try:
        with psycopg.connect(migrated) as conn:
            # Reference polygons: a box around Nantes (44), one around Lyon (69).
            conn.execute(
                "INSERT INTO geo_departements (code, nom, geometry) VALUES "
                "('44', 'test-44', "
                "ST_Multi(ST_MakeEnvelope(-2.6, 46.9, -0.9, 47.8, 4326))), "
                "('69', 'test-69', "
                "ST_Multi(ST_MakeEnvelope(4.2, 45.4, 5.2, 46.3, 4326)))"
            )
        pipeline.bind_scope(InstanceScope(frozenset(), ("44", "85")))
        # 85 has no polygon: refused before any download, with the reason.
        with pytest.raises(ScopePrerequisiteError, match=r"\['85'\]"):
            pipeline.check_scope_prerequisites(migrated)

        pipeline._stage_arrets(arrets)
        pipeline._stage_gares(gares)
        pipeline.bind_scope(InstanceScope(frozenset(), ("44",)))
        pipeline.check_scope_prerequisites(migrated)
        # Lyon stop and station outside the scope; the offshore stop has no
        # departement: all three removed, never published.
        assert (
            PipelineRunner._apply_scope_filter(migrated, pipeline, pipeline.scope, log)
            == 3
        )
        with psycopg.connect(migrated) as conn:
            assert conn.execute(
                "SELECT stop_id FROM _arrets_transport_staging"
            ).fetchall() == [("nantes",)]
            assert conn.execute("SELECT nom_gare FROM _gares_staging").fetchall() == [
                ("Nantes",)
            ]
        assert (
            PipelineRunner._check_department_coverage(migrated, pipeline, log) is None
        )
        # Every filtered table is checked: stations missing for 44 refuse the
        # swap even though stops are there.
        with psycopg.connect(migrated) as conn:
            conn.execute("DELETE FROM _gares_staging")
        assert PipelineRunner._check_department_coverage(migrated, pipeline, log) == (
            "Department coverage: missing scoped departments ['44'] in "
            "_gares_staging -- swap aborted"
        )
        pipeline.bind_scope(InstanceScope(frozenset(), ("44", "85")))
        assert PipelineRunner._check_department_coverage(migrated, pipeline, log) == (
            "Department coverage: missing scoped departments ['85'] in "
            "_arrets_transport_staging -- swap aborted"
        )
    finally:
        with psycopg.connect(migrated) as conn:
            conn.execute("DROP TABLE IF EXISTS _arrets_transport_staging")
            conn.execute("DROP TABLE IF EXISTS _gares_staging")
            conn.execute("DELETE FROM geo_departements WHERE nom LIKE 'test-%'")


def test_n_full_scope_removes_unhostable_departements_and_keeps_unattributed(
    migrated, monkeypatch
):
    """First national import, 2026-10-29: the full scope filtered nothing.

    Monuments refused its swap on '01;', '12;' (a malformed source value)
    and 975 (never hosted, D-123). The full scope now removes the rows of a
    departement outside the hostable list, counted and journaled as on a
    restricted scope; a row without departement stays (documented rule).
    """
    from unittest.mock import MagicMock

    from app.etl_monuments.pipeline import MonumentsPipeline
    from app.scope import InstanceScope
    from app.scope.model import DEPARTEMENTS_HEBERGEABLES

    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(MonumentsPipeline, "national_file_min_rows", None)
    # A logger double: an earlier module of the CI call may reconfigure the
    # logging tree, which empties caplog.
    log = MagicMock()
    pipeline = MonumentsPipeline()
    scope = InstanceScope(frozenset({"monuments"}), DEPARTEMENTS_HEBERGEABLES)
    pipeline.bind_scope(scope)
    assert scope.covers_all_departements
    try:
        with psycopg.connect(migrated) as conn:
            conn.execute(
                "CREATE UNLOGGED TABLE _monuments_staging_flat "
                "(reference TEXT, code_departement TEXT)"
            )
            conn.execute(
                "INSERT INTO _monuments_staging_flat VALUES ('a', '01'), "
                "('b', '44'), ('c', '975'), ('d', '01;'), ('e', '12;'), "
                "('f', NULL), ('g', '2A'), ('h', '971')"
            )
        removed = PipelineRunner._apply_scope_filter(migrated, pipeline, scope, log)
        assert removed == 3
        with psycopg.connect(migrated) as conn:
            kept = conn.execute(
                "SELECT reference FROM _monuments_staging_flat ORDER BY 1"
            ).fetchall()
        assert kept == [("a",), ("b",), ("f",), ("g",), ("h",)]
        journal = [call.args[0] % call.args[1:] for call in log.info.call_args_list]
        assert (
            "Scope filter for monuments, _monuments_staging_flat: 3 rows removed, "
            "of which 0 without departement" in journal
        )
        assert (
            "Scope filter for monuments: 1 rows without departement kept "
            "(full scope)" in journal
        )
        # Per hosted departement only; the row without departement is not one.
        assert pipeline.scope_file_rows == {"01": 1, "44": 1, "2A": 1, "971": 1}
        # No departement outside the scope left: the guard now reports the
        # departements this five-row table lacks, never an extra one.
        error = PipelineRunner._check_department_coverage(migrated, pipeline, log)
        assert error is not None
        assert error.startswith("Department coverage: missing scoped departments")
        # The rows of every hosted departement stay untouched on a rerun.
        assert PipelineRunner._apply_scope_filter(migrated, pipeline, scope, log) == 0
    finally:
        with psycopg.connect(migrated) as conn:
            conn.execute("DROP TABLE IF EXISTS _monuments_staging_flat")
