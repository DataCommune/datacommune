"""DVF replayed from the source archive, end to end (plan 04.4-03, task 1).

Disposable wrapper database only. The archive is written for real under
``tmp_path`` (sentinel included), as a download run of the producer would
write it; the replay goes through the real runner, the real DVF pipeline and
the real reconstruction. Nothing is downloaded: the replay never calls the
HTTP layer (the substitute fails the test if it is reached).
"""

import csv
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import ClassVar

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl import source_archive as sa
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_dvf.config import COLUMNS_TO_KEEP, YEARS
from app.etl_dvf.pipeline import DvfPipeline
from app.scope import load_scope_sync
from tests.integration.test_instance_scope import BACKEND

pytestmark = pytest.mark.destructive

ARCHIVED_RUN = "48-20261019T171140Z"
# One commune per departement: code, parcel prefix, coordinates.
_COMMUNES = {
    "44": ("44109", "44109000AB", -1.5534, 47.2184),
    "85": ("85191", "85191000AC", -1.4269, 46.6705),
    "57": ("57463", "57463000AD", 6.1757, 49.1193),
}
# Mutation dates of the fixture: the window a download of the same files
# publishes (C24, proven for a download by test_departmental_scope a_bis).
_DATES = {"44": ("2021-02-03", "2021-11-28"), "85": ("2021-01-15", "2021-06-30")}


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


@pytest.fixture
def env(migrated, monkeypatch, tmp_path, instance_scope):
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    root = tmp_path / "archive"
    root.mkdir()
    (root / sa.SENTINEL_NAME).write_text(sa.SENTINEL_CONTENT + "\n", "utf-8")
    monkeypatch.setattr(settings, "source_archive_dir", str(root))
    # Two mutations per file: the floors are covered by the unit suite.
    monkeypatch.setattr(DvfPipeline, "min_staging_rows", property(lambda _: 1))

    def no_download(*_args, **_kwargs):
        pytest.fail("a replay downloads nothing")

    monkeypatch.setattr("app.etl_dvf.pipeline.verified_download_gz", no_download)
    instance_scope(migrated, ["dvf"], ["44", "85"])
    return migrated, root


def _write_source(path, dept, ids):
    commune, parcel, lon, lat = _COMMUNES[dept]
    first, last = _DATES.get(dept, ("2021-05-05", "2021-05-06"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS_TO_KEEP)
        writer.writeheader()
        for index, (id_mutation, date) in enumerate(
            zip(ids, (first, last), strict=True), 1
        ):
            row = dict.fromkeys(COLUMNS_TO_KEEP, "")
            row.update(
                id_mutation=id_mutation,
                date_mutation=date,
                nature_mutation="Vente",
                valeur_fonciere="185000",
                adresse_numero="1",
                adresse_nom_voie="SYN-VOIE",
                code_postal=f"{dept}000",
                code_commune=commune,
                nom_commune="SYN-COMMUNE",
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
            writer.writerow(row)


def _journal(dsn, run_id, status, *, stamp=None):
    """The etl_runs row of the archived run *run_id*, final *status*.

    *stamp* overrides the started_at the run id composes (another run under
    the same id, e.g. a journal rebuilt since). ``status=None`` removes it.
    """
    etl_id, _, run_stamp = run_id.partition("-")
    started_at = datetime.strptime(stamp or run_stamp, "%Y%m%dT%H%M%SZ").replace(
        tzinfo=UTC
    )
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM etl_runs WHERE id = %s", (int(etl_id),))
        if status is not None:
            conn.execute(
                "INSERT INTO etl_runs (id, dataset_name, status, started_at) "
                "VALUES (%s, 'dvf', %s, %s)",
                (int(etl_id), status, started_at),
            )


def _archive_run(
    env,
    tmp_path,
    relpaths,
    *,
    run_id=ARCHIVED_RUN,
    status="success",
    replayed_from=None,
):
    """Archive one run of *relpaths* (``raw/<AAAA>/<dept>.csv``), journaled.

    The run is a download, or a replay of *replayed_from*; its etl_runs row
    ends with *status* (``None``: no row).
    """
    dsn, root = env
    source_dir = tmp_path / "download" / run_id
    files = []
    for n, relpath in enumerate(relpaths):
        path = source_dir / relpath
        dept = path.stem if path.stem in _COMMUNES else "44"
        _write_source(path, dept, [f"SYN-2021-{n}{k}" for k in (1, 2)])
        files.append(path)
    sa.archive_sources(
        root,
        "dvf",
        run_id,
        files,
        source_dir,
        origin="archive" if replayed_from else "telechargement",
        replayed_from=replayed_from,
    )
    _journal(dsn, run_id, status)
    return sa.load_replay(root, "dvf", run_id)


def _objects_state(root, replay):
    state = {}
    for entry in replay.files:
        obj = root / "dvf" / entry["object"]
        stat = obj.stat()
        digest = hashlib.sha256(obj.read_bytes()).hexdigest()
        state[entry["relpath"]] = (stat.st_mode, stat.st_mtime_ns, digest)
    return state


class _SpyDvfPipeline(DvfPipeline):
    """Real DVF pipeline that remembers what its restore returned."""

    restored: ClassVar[list] = []
    file_count_after_restore: ClassVar[int | None] = None

    def restore_source_inputs(self, replay, directory):
        paths = super().restore_source_inputs(replay, directory)
        type(self).restored = [
            (p, p.is_symlink(), p.resolve() if p.is_symlink() else None) for p in paths
        ]
        type(self).file_count_after_restore = self.expected_file_count
        return paths


def _replay(replay, pipeline_cls=DvfPipeline):
    runner = PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=_db_scope_loader
    )
    return runner.run(pipeline_cls(), source_replay=replay)


def _published(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT (SELECT count(*) FROM mutations), "
            "(SELECT count(*) FROM mutations_grain), "
            "(SELECT md5(coalesce(string_agg(m::text, '|' ORDER BY m::text), '')) "
            "FROM mutations m)"
        ).fetchone()


def test_dvf_is_declared_replayable_by_the_cli(env, tmp_path):
    """``run_etl dvf --from-archive <run>`` admits DVF (was refused before 04.4)."""
    import run_etl

    replay = _archive_run(env, tmp_path, ["raw/2021/44.csv", "raw/2021/85.csv"])
    loaded = run_etl._load_replay("dvf", ARCHIVED_RUN)
    assert loaded.record_sha256 == replay.record_sha256


def test_a_two_file_record_replays_end_to_end_with_its_provenance(
    env, tmp_path, caplog
):
    dsn, root = env
    replay = _archive_run(env, tmp_path, ["raw/2021/44.csv", "raw/2021/85.csv"])
    pipeline = DvfPipeline()
    # The fixture proves something only if the theoretical denominator
    # (computed outside a replay) would refuse a two-file record.
    theoretical = len(YEARS) * 2
    assert 2 / theoretical < pipeline.min_success_ratio
    before = _objects_state(root, replay)
    objects = (root / "dvf" / "objects").resolve()

    with caplog.at_level(logging.INFO, logger="etl.dvf"):
        result = _replay(replay, _SpyDvfPipeline)

    assert result.success, result.error
    assert "Success ratio: 2/2 files (100.0%)" in caplog.text
    assert _SpyDvfPipeline.file_count_after_restore == 2
    # Restored inputs are links to the verified objects, never copies.
    assert len(_SpyDvfPipeline.restored) == 2
    for path, is_link, target in _SpyDvfPipeline.restored:
        assert is_link, path
        assert target.is_relative_to(objects)
    assert _objects_state(root, replay) == before

    rows, grain, _ = _published(dsn)
    assert (rows, grain) == (4, 4)
    with psycopg.connect(dsn) as conn:
        (label,) = conn.execute(
            "SELECT source_period_label FROM dataset_metadata "
            "WHERE dataset_name = 'dvf'"
        ).fetchone()
    # The served period is the window of the mutation dates, as a download.
    assert label == "2021-01-15 → 2021-11-28"

    records = sorted((root / "dvf" / "runs").glob("*.json"))
    new = [r for r in records if r.stem != ARCHIVED_RUN]
    assert len(new) == 1
    record = json.loads(new[0].read_text("utf-8"))
    assert record["origin"] == "archive"
    assert record["replayed_from"] == ARCHIVED_RUN
    assert [(f["relpath"], f["sha256"]) for f in record["files"]] == [
        (f["relpath"], f["sha256"]) for f in replay.files
    ]
    assert [f["stored"] for f in record["files"]] == [False, False]
    assert sa.verify(root)["records"] == 2


def test_the_replay_count_override_is_what_lets_the_record_pass(
    env, tmp_path, monkeypatch
):
    """Sensitivity: the denominator computed outside a replay refuses the swap."""
    dsn, _ = env
    replay = _archive_run(env, tmp_path, ["raw/2021/44.csv", "raw/2021/85.csv"])
    before = _published(dsn)
    monkeypatch.setattr(
        DvfPipeline,
        "expected_file_count",
        property(lambda self: len(YEARS) * len(self.expected_department_coverage)),
    )

    result = _replay(replay)

    assert not result.success
    assert "Success ratio 14.3% (2/14) below threshold" in result.error
    assert "swap aborted" in result.error
    assert "swap" not in {stage.stage_name for stage in result.stages}
    assert _published(dsn) == before


def _assert_refused_before_any_write(env, tmp_path, replay, *named):
    """Replay *replay*: refused at the restore, before any link or journal row.

    The error names each run of *named* (default: the replayed record).
    """
    dsn, root = env
    before = _published(dsn)
    records_before = sorted(r.stem for r in (root / "dvf" / "runs").glob("*.json"))
    with psycopg.connect(dsn) as conn:
        (runs_before,) = conn.execute("SELECT count(*) FROM etl_runs").fetchone()

    result = _replay(replay)

    assert not result.success
    # The restore failed: no stage completed, not even the restore.
    assert result.stages == []
    assert "Stage 'restore_sources' failed" in result.error, result.error
    for run in named or (replay.run_id,):
        assert run in result.error, result.error
    assert f"record {replay.run_id}" in result.error
    assert "rejeu refusé" in result.error
    download_dir = tmp_path / "etl"
    assert not any(p.is_symlink() for p in download_dir.rglob("*"))
    assert _published(dsn) == before
    with psycopg.connect(dsn) as conn:
        (runs_after,) = conn.execute("SELECT count(*) FROM etl_runs").fetchone()
    assert runs_after == runs_before
    records = sorted(r.stem for r in (root / "dvf" / "runs").glob("*.json"))
    assert records == records_before
    return result


# The record of run 48 (hypervisor, 2026-10-19): 2021-2025 x {44, 85}, 10 files.
_RUN48_RELPATHS = [
    f"raw/{year}/{dept}.csv" for year in range(2021, 2026) for dept in ("44", "85")
]


def test_the_shape_of_run_48_replays_end_to_end(env, tmp_path, caplog):
    """The replay planned by 04.4-04 stays admitted: a successful download run,
    every hosted departement for each of five contiguous years."""
    replay = _archive_run(env, tmp_path, _RUN48_RELPATHS)

    with caplog.at_level(logging.INFO, logger="etl.dvf"):
        result = _replay(replay, _SpyDvfPipeline)

    assert result.success, result.error
    assert "Success ratio: 10/10 files (100.0%)" in caplog.text
    assert _SpyDvfPipeline.file_count_after_restore == 10


@pytest.mark.parametrize(
    "relpaths",
    [
        pytest.param(["raw/2021/44.csv"], id="hosted-departement-missing"),
        pytest.param(
            ["raw/2021/44.csv", "raw/2021/85.csv", "raw/2021/57.csv"],
            id="departement-not-hosted",
        ),
        pytest.param(["raw/2021/44.csv", "raw/85.csv"], id="path-without-year"),
        pytest.param(["raw/2021/44.csv", "raw/21/85.csv"], id="year-not-four-digits"),
        pytest.param(["raw/2021/44.csv", "raw/2021/85.csv.gz"], id="not-a-csv"),
        pytest.param(["raw/2021/44.csv", "brut/2021/85.csv"], id="not-under-raw"),
        pytest.param(["raw/2021/44.csv", "raw/2021/99.csv"], id="unknown-departement"),
        # WR-01: every hosted departement appears, but not in every year.
        pytest.param(
            ["raw/2021/44.csv", "raw/2021/85.csv", "raw/2022/44.csv"],
            id="year-missing-a-hosted-departement",
        ),
        pytest.param(
            [p for p in _RUN48_RELPATHS if p != "raw/2025/85.csv"],
            id="run-48-without-one-file",
        ),
        # A whole year missing inside the edition: never a legitimate 404.
        pytest.param(
            [p for p in _RUN48_RELPATHS if not p.startswith("raw/2023/")],
            id="year-missing-inside-the-edition",
        ),
        pytest.param(
            ["raw/2019/44.csv", "raw/2019/85.csv"], id="year-download-never-reads"
        ),
    ],
)
def test_an_incomplete_or_foreign_record_is_refused_before_any_write(
    env, tmp_path, relpaths
):
    replay = _archive_run(env, tmp_path, relpaths)

    _assert_refused_before_any_write(env, tmp_path, replay)


@pytest.mark.parametrize(
    ("status", "stamp"),
    [
        pytest.param("failed", None, id="original-run-refused-by-its-gate"),
        pytest.param("running", None, id="original-run-never-finished"),
        pytest.param(None, None, id="original-run-not-in-the-journal"),
        pytest.param("success", "20261019T171141Z", id="another-run-under-its-id"),
    ],
)
def test_a_record_of_a_run_that_did_not_succeed_is_refused_before_any_write(
    env, tmp_path, status, stamp
):
    """WR-01: the record is written before the gate of its own import, so a
    complete-looking record proves nothing until the journal says success."""
    dsn, _ = env
    replay = _archive_run(env, tmp_path, _RUN48_RELPATHS)
    _journal(dsn, ARCHIVED_RUN, status, stamp=stamp)

    result = _assert_refused_before_any_write(env, tmp_path, replay)

    assert f"run d'origine {ARCHIVED_RUN}" in result.error


_ANCESTOR_RUN = "47-20261018T090000Z"


def test_a_replay_record_is_judged_by_the_run_it_replayed(env, tmp_path):
    """A record of a successful replay of a refused run stays refused."""
    dsn, _ = env
    _archive_run(env, tmp_path, _RUN48_RELPATHS, run_id=_ANCESTOR_RUN, status="failed")
    replay = _archive_run(env, tmp_path, _RUN48_RELPATHS, replayed_from=_ANCESTOR_RUN)

    result = _assert_refused_before_any_write(env, tmp_path, replay, _ANCESTOR_RUN)

    assert f"run d'origine {_ANCESTOR_RUN}" in result.error
    _journal(dsn, _ANCESTOR_RUN, None)


def test_a_replay_record_without_its_ancestor_record_is_refused(env, tmp_path):
    """The lineage cannot be proven: refused, never assumed."""
    dsn, _ = env
    _journal(dsn, _ANCESTOR_RUN, "success")
    replay = _archive_run(env, tmp_path, _RUN48_RELPATHS, replayed_from=_ANCESTOR_RUN)

    _assert_refused_before_any_write(env, tmp_path, replay, _ANCESTOR_RUN)
    _journal(dsn, _ANCESTOR_RUN, None)


def test_a_replay_record_of_a_successful_lineage_replays(env, tmp_path):
    dsn, _ = env
    _archive_run(env, tmp_path, _RUN48_RELPATHS, run_id=_ANCESTOR_RUN)
    replay = _archive_run(env, tmp_path, _RUN48_RELPATHS, replayed_from=_ANCESTOR_RUN)

    result = _replay(replay)

    assert result.success, result.error
    _journal(dsn, _ANCESTOR_RUN, None)
