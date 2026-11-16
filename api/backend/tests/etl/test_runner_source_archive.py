"""Runner step ``archive_sources`` and the ``--from-archive`` CLI (plan 02-14).

The real ``PipelineRunner.run`` executes with the fake database of
``tests/etl/test_runner.py``; only the read of the run's own ``started_at``
is substituted. The archive is written for real on ``tmp_path``.
"""

import gzip
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest

from app.config import settings
from app.etl import source_archive as sa
from app.etl.download import cleanup_download_dir
from app.etl.runner import PipelineRunner
from app.etl_vacance.pipeline import VacancePipeline
from tests.etl.test_runner import FakePipeline, _patch_all

pytestmark = pytest.mark.unit

ETL_ID = 1  # _patch_all makes _insert_etl_run return 1
STARTED = datetime(2026, 9, 24, 10, 15, 0, 123456, tzinfo=UTC)
RUN_ID = "1-20260924T101500Z"
_LOVAC_COLUMN_PROOF = (
    "Logements vacants : 2026 via pp_vacant_26 ; parc privé : 2025 via ff_pp_total_25"
)


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    host_test_datasets("test_ds")


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    (root / sa.SENTINEL_NAME).write_text(sa.SENTINEL_CONTENT + "\n", "utf-8")
    monkeypatch.setattr(settings, "source_archive_dir", str(root))
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    return root


class _Pipeline(FakePipeline):
    """FakePipeline whose download writes one file in its real cache dir."""

    def __init__(self, calls: list[str], data: bytes = b"id\n1\n") -> None:
        super().__init__()
        self._calls = calls
        self._data = data

    def download(self) -> list[Path]:
        from app.etl.download import get_download_dir

        self._calls.append("download")
        path = get_download_dir(self.dataset_name) / "raw" / "source.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._data)
        return [path]

    def drop_staging_table(self, dsn: str) -> None:
        self._calls.append("drop_staging")

    def validate_schema(self, file_path: Path) -> None:
        self._calls.append("validate")

    def transform(self, file_path: Path) -> Path:
        self._calls.append("transform")
        return file_path


def _run(pipeline, *, started=STARTED, **kwargs):
    with (
        _patch_all(staging_exists=False, checkpoint_data=None) as mocks,
        patch.object(PipelineRunner, "_read_run_started_at", return_value=started),
    ):
        result = PipelineRunner().run(pipeline, **kwargs)
    return result, mocks


def test_download_is_archived_before_the_first_validation(archive):
    calls: list[str] = []
    real = sa.archive_sources

    def spy(*args, **kwargs):
        calls.append("archive")
        return real(*args, **kwargs)

    with patch("app.etl.runner.archive_sources", side_effect=spy):
        result, _ = _run(_Pipeline(calls))

    assert result.success is True, result.error
    assert calls[:4] == ["download", "archive", "validate", "transform"]
    names = [s.stage_name for s in result.stages]
    assert names.index("download") + 1 == names.index("archive_sources")
    record = json.loads(
        (archive / "test_ds" / "runs" / f"{RUN_ID}.json").read_text("utf-8")
    )
    assert record["origin"] == "telechargement"
    assert record["files"][0]["relpath"] == "raw/source.csv"
    assert record["files"][0]["stored"] is True


class _DirectoryPipeline(_Pipeline):
    """download() returns a directory, as ArcepPipeline does (plan 02-22)."""

    def __init__(self, calls: list[str]) -> None:
        super().__init__(calls)
        self.validated: list[Path] = []

    def download(self) -> list[Path]:
        from app.etl.download import get_download_dir

        self._calls.append("download")
        directory = get_download_dir(self.dataset_name) / "raw" / "44"
        directory.mkdir(parents=True, exist_ok=True)
        # Real gzip: the journal counts the lines of a .csv.gz.
        (directory / "base_imb_44.csv.gz").write_bytes(gzip.compress(b"a\n1\n"))
        (directory / "actuel_44.csv.gz").write_bytes(gzip.compress(b"b\n1\n2\n"))
        return [directory]

    def validate_schema(self, file_path: Path) -> None:
        self._calls.append("validate")
        self.validated.append(file_path)


class _Journal:
    """psycopg connection double: keeps the statements of the etl_runs journal."""

    def __init__(self, executed: list[tuple[str, dict]]) -> None:
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, query, params=None):
        self._executed.append((query, params))

    def commit(self):
        pass


def test_a_downloaded_directory_is_journaled_and_archived_file_by_file(archive):
    calls: list[str] = []
    real_archive = sa.archive_sources
    real_record = PipelineRunner._record_source_files
    executed: list[tuple[str, dict]] = []

    def spy(*args, **kwargs):
        calls.append("archive")
        return real_archive(*args, **kwargs)

    def record(*args, **kwargs):
        with patch("app.etl.runner.psycopg.connect", return_value=_Journal(executed)):
            return real_record(*args, **kwargs)

    pipeline = _DirectoryPipeline(calls)
    with (
        _patch_all(staging_exists=False, checkpoint_data=None) as mocks,
        patch.object(PipelineRunner, "_read_run_started_at", return_value=STARTED),
        patch("app.etl.runner.archive_sources", side_effect=spy),
    ):
        mocks["record_source_files"].side_effect = record
        result = PipelineRunner().run(pipeline)

    assert result.success is True, result.error
    assert calls[:3] == ["download", "archive", "validate"]
    directory = Path(settings.etl_temp_dir) / "datacommune_test_ds" / "raw" / "44"
    assert pipeline.validated == [directory]
    journal = [p for q, p in executed if "source_files" in q]
    assert len(journal) == 1
    entries = json.loads(journal[0]["files"])
    # Plan 04-15 (C8): physical CSV lines minus the header, never "rows"
    # (a quoted field may span several lines: not a count of records).
    assert all("rows" not in entry for entry in entries)
    assert [(f["name"], f["physical_lines"]) for f in entries] == [
        ("actuel_44.csv.gz", 2),
        ("base_imb_44.csv.gz", 1),
    ]
    record_file = archive / "test_ds" / "runs" / f"{RUN_ID}.json"
    archived = json.loads(record_file.read_text("utf-8"))
    assert [f["relpath"] for f in archived["files"]] == [
        "raw/44/actuel_44.csv.gz",
        "raw/44/base_imb_44.csv.gz",
    ]
    assert [f["sha256"] for f in archived["files"]] == [
        f["sha256"] for f in json.loads(journal[0]["files"])
    ]


@pytest.mark.parametrize("cause", ["sentinelle_absente", "espace_insuffisant"])
def test_archive_failure_fails_the_run_before_any_transform(
    archive, cause, monkeypatch
):
    if cause == "sentinelle_absente":
        (archive / sa.SENTINEL_NAME).unlink()
    else:
        monkeypatch.setattr(settings, "source_archive_min_free_bytes", 10**18)
    calls: list[str] = []

    result, mocks = _run(_Pipeline(calls))

    assert result.success is False
    assert "Stage 'archive_sources' failed" in result.error
    assert cause in result.error
    assert "transform" not in calls and "validate" not in calls
    assert "drop_staging" in calls
    mocks["update_metadata"].assert_not_called()
    assert not (archive / "test_ds" / "runs").exists()


def test_missing_journal_row_is_never_archived_anonymously(archive):
    calls: list[str] = []
    with (
        _patch_all(staging_exists=False, checkpoint_data=None) as mocks,
        patch.object(PipelineRunner, "_read_run_started_at", return_value=None),
    ):
        mocks["insert_etl_run"].return_value = None
        result = PipelineRunner().run(_Pipeline(calls))

    assert result.success is False
    assert "run_id_absent" in result.error
    assert "transform" not in calls
    assert not (archive / "test_ds").exists()


def test_empty_archive_dir_keeps_the_runner_unchanged(archive, monkeypatch):
    monkeypatch.setattr(settings, "source_archive_dir", "")
    calls: list[str] = []

    result, _ = _run(_Pipeline(calls))

    assert result.success is True
    assert "archive_sources" not in [s.stage_name for s in result.stages]
    assert not (archive / "test_ds").exists()


def test_dry_run_archives_nothing(archive):
    calls: list[str] = []
    with _patch_all(staging_exists=False, checkpoint_data=None):
        result = PipelineRunner().run(_Pipeline(calls), dry_run=True)

    assert result.success is True
    assert "archive_sources" not in [s.stage_name for s in result.stages]
    assert not (archive / "test_ds").exists()


def test_replay_restores_the_sources_without_downloading(archive, tmp_path):
    data = b"id\n1\n2\n"
    calls: list[str] = []
    first, _ = _run(_Pipeline(calls, data))
    assert first.success is True, first.error
    replay = sa.load_replay(archive, "test_ds", RUN_ID)

    calls.clear()
    later = datetime(2026, 9, 24, 11, 0, 0, tzinfo=UTC)
    result, _ = _run(
        _Pipeline(calls, b"never used"), started=later, source_replay=replay
    )

    assert result.success is True, result.error
    assert "download" not in calls
    names = [s.stage_name for s in result.stages]
    assert "download" not in names
    assert names[:3] == ["restore_sources", "create_staging", "archive_sources"]
    record = json.loads(
        (archive / "test_ds" / "runs" / "1-20260924T110000Z.json").read_text("utf-8")
    )
    assert record["origin"] == "archive"
    assert record["replayed_from"] == RUN_ID
    assert [f["stored"] for f in record["files"]] == [False]
    assert (
        record["files"][0]["sha256"]
        == sa.load_replay(archive, "test_ds", RUN_ID).files[0]["sha256"]
    )


class _LinkedPipeline(_Pipeline):
    """Replays by links to the archived objects, as BdTopoPipeline (04.1-05)."""

    def restore_source_inputs(self, replay, directory):
        self._calls.append("link")
        return replay.link(directory)


def test_linked_replay_is_fingerprinted_archived_and_leaves_objects_intact(archive):
    data = b"id\n1\n2\n"
    calls: list[str] = []
    first, _ = _run(_Pipeline(calls, data))
    assert first.success is True, first.error
    replay = sa.load_replay(archive, "test_ds", RUN_ID)
    obj = archive / "test_ds" / replay.files[0]["object"]
    before = obj.stat()
    executed: list[tuple[str, dict]] = []
    real_record = PipelineRunner._record_source_files

    def record(*args, **kwargs):
        with patch("app.etl.runner.psycopg.connect", return_value=_Journal(executed)):
            return real_record(*args, **kwargs)

    later = datetime(2026, 9, 24, 11, 0, 0, tzinfo=UTC)
    calls.clear()
    with (
        _patch_all(staging_exists=False, checkpoint_data=None) as mocks,
        patch.object(PipelineRunner, "_read_run_started_at", return_value=later),
    ):
        mocks["record_source_files"].side_effect = record
        result = PipelineRunner().run(
            _LinkedPipeline(calls, b"never used"), source_replay=replay
        )

    assert result.success is True, result.error
    assert calls[0] == "link"
    assert "download" not in calls
    link = Path(settings.etl_temp_dir) / "datacommune_test_ds" / "raw" / "source.csv"
    assert link.is_symlink()  # the first run's file was replaced by the link
    assert link.resolve() == obj.resolve()
    record_file = archive / "test_ds" / "runs" / "1-20260924T110000Z.json"
    archived = json.loads(record_file.read_text("utf-8"))
    assert [(f["relpath"], f["stored"]) for f in archived["files"]] == [
        ("raw/source.csv", False)
    ]
    (journal,) = [p for q, p in executed if "source_files" in q]
    assert [f["name"] for f in json.loads(journal["files"])] == ["source.csv"]
    after = obj.stat()
    assert (after.st_mode, after.st_mtime_ns) == (before.st_mode, before.st_mtime_ns)
    assert obj.read_bytes() == data

    cleanup_download_dir("test_ds")  # the real cleanup of a published run

    assert not link.parent.exists()
    assert obj.read_bytes() == data
    assert obj.stat().st_mode == before.st_mode
    assert sa.verify(archive)["records"] == 2


class _ReplayVacancePipeline(VacancePipeline):
    """Real LOVAC restore/transform with database stages replaced by test doubles."""

    _files: ClassVar[list[Path]] = []

    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self.calls = calls
        self.transformed_row: dict[str, str] | None = None

    @property
    def min_staging_rows(self) -> int:
        return 0

    @property
    def min_success_ratio(self) -> float:
        return 0.0

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        return True

    def restore_source_inputs(self, replay, directory):
        self.calls.append("restore")
        return super().restore_source_inputs(replay, directory)

    def transform(self, file_path: Path) -> Path:
        self.calls.append("transform")
        output = super().transform(file_path)
        import csv

        with output.open(encoding="utf-8", newline="") as handle:
            self.transformed_row = next(csv.DictReader(handle))
        return output

    def stage(self, file_path: Path) -> int:
        return 1

    def create_staging_table(self, dsn: str) -> None:
        pass

    def drop_staging_table(self, dsn: str) -> None:
        pass

    def swap(self) -> None:
        pass


@pytest.mark.parametrize(
    ("archived_label", "expected_label"),
    [
        (None, _LOVAC_COLUMN_PROOF),
        ("LOVAC 2026", f"Libellé archivé : LOVAC 2026 ; {_LOVAC_COLUMN_PROOF}"),
        (_LOVAC_COLUMN_PROOF, _LOVAC_COLUMN_PROOF),
        (
            f"Libellé archivé : LOVAC 2026 ; {_LOVAC_COLUMN_PROOF}",
            f"Libellé archivé : LOVAC 2026 ; {_LOVAC_COLUMN_PROOF}",
        ),
    ],
    ids=(
        "old-record-without-label",
        "historical-label",
        "same-canonical-label",
        "already-composed-label",
    ),
)
def test_lovac_replay_prepares_proof_before_archive_and_reuses_pair(
    archive,
    tmp_path,
    monkeypatch,
    host_test_datasets,
    archived_label,
    expected_label,
):
    host_test_datasets("test_ds", "vacance")
    source_root = tmp_path / "old-source"
    source = source_root / "raw" / "lovac.csv"
    source.parent.mkdir(parents=True)
    source.write_text(
        "CODGEO_26;LIBGEO_26;pp_vacant_26;pp_vacant_plus_2ans_26;ff_pp_total_25\n"
        "44001;Commune test;12;4;80\n",
        encoding="latin-1",
    )
    old_run_id = "9-20260923T101500Z"
    sa.archive_sources(
        archive,
        "vacance",
        old_run_id,
        [source],
        source_root,
        origin="telechargement",
        source_period_label=archived_label,
    )
    old_record = json.loads(
        (archive / "vacance" / "runs" / f"{old_run_id}.json").read_text("utf-8")
    )
    if archived_label is None:
        assert "source_period_label" not in old_record
    else:
        assert old_record["source_period_label"] == archived_label
    replay = sa.load_replay(archive, "vacance", old_run_id)
    calls: list[str] = []
    real_archive = sa.archive_sources

    def spy(*args, **kwargs):
        calls.append("archive")
        return real_archive(*args, **kwargs)

    later = datetime(2026, 9, 24, 11, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.etl_vacance.pipeline.get_download_dir",
        lambda _: Path(settings.etl_temp_dir) / "datacommune_vacance",
    )
    pipeline = _ReplayVacancePipeline(calls)
    with patch("app.etl.runner.archive_sources", side_effect=spy):
        result, _ = _run(pipeline, started=later, source_replay=replay)

    assert result.success is True, result.error
    assert calls[:3] == ["restore", "archive", "transform"]
    record = json.loads(
        (archive / "vacance" / "runs" / "1-20260924T110000Z.json").read_text("utf-8")
    )
    assert record["source_period_label"] == expected_label
    unchanged_old_record = json.loads(
        (archive / "vacance" / "runs" / f"{old_run_id}.json").read_text("utf-8")
    )
    assert unchanged_old_record == old_record
    assert pipeline.transformed_row is not None
    assert float(pipeline.transformed_row["nb_logements_prives"]) == 80
    assert float(pipeline.transformed_row["taux_vacance"]) == 15


class _DatedPipeline(_Pipeline):
    """A run that knows the period of what it downloaded (C30)."""

    @property
    def source_period_label(self) -> str | None:
        return "2026_T2"


def test_the_record_of_a_run_keeps_the_period_it_declared(archive):
    result, _ = _run(_DatedPipeline([]))

    assert result.success is True, result.error
    record = json.loads(
        (archive / "test_ds" / "runs" / f"{RUN_ID}.json").read_text("utf-8")
    )
    assert record["source_period_label"] == "2026_T2"


def test_the_record_of_a_run_without_period_names_none(archive):
    result, _ = _run(_Pipeline([]))

    assert result.success is True, result.error
    record = json.loads(
        (archive / "test_ds" / "runs" / f"{RUN_ID}.json").read_text("utf-8")
    )
    assert "source_period_label" not in record


# ------------------------------------------------------------------ CLI --


def _cli(monkeypatch, argv):
    import run_etl

    built = []
    monkeypatch.setattr(
        run_etl, "PipelineRunner", lambda *a, **k: built.append(True) or None
    )
    monkeypatch.setattr(
        run_etl.etl_runner,
        "default_scope_loader",
        lambda _dsn: pytest.fail("no connection may be opened"),
    )
    monkeypatch.setattr(run_etl.sys, "argv", ["run_etl", *argv])
    with pytest.raises(SystemExit) as exit_info:
        run_etl.main()
    return exit_info.value.code, built


@pytest.mark.parametrize(
    "argv",
    [
        ["vacance", "loyers", "--from-archive", RUN_ID],
        ["--from-archive", RUN_ID],
        ["vacance", "--from-archive", RUN_ID, "--dry-run"],
        ["vacance", "--from-archive", RUN_ID, "--source-manifest", "/m.json"],
        ["dpe", "--from-archive", RUN_ID, "--dpe-manifest", "/m.json"],
    ],
)
def test_from_archive_option_conflicts_are_usage_errors(monkeypatch, argv):
    code, built = _cli(monkeypatch, argv)
    assert code == 2
    assert built == []


def test_pipeline_without_archive_replay_is_refused(monkeypatch, archive, capsys):
    code, built = _cli(monkeypatch, ["loyers", "--from-archive", RUN_ID])
    assert (code, built) == (2, [])
    assert "archive_replay_non_supporte: loyers" in capsys.readouterr().err


def test_unconfigured_archive_is_refused(monkeypatch, archive, capsys):
    monkeypatch.setattr(settings, "source_archive_dir", "")
    code, built = _cli(monkeypatch, ["vacance", "--from-archive", RUN_ID])
    assert (code, built) == (2, [])
    assert "source_archive_non_configuree" in capsys.readouterr().err


def test_refused_archive_prints_its_reason_before_any_runner(
    monkeypatch, archive, capsys
):
    code, built = _cli(monkeypatch, ["vacance", "--from-archive", RUN_ID])
    assert (code, built) == (2, [])
    out = capsys.readouterr().out
    assert out.startswith("SOURCE_ARCHIVE_REFUSED record_absent ")


def test_verified_replay_reaches_the_runner(monkeypatch, archive):
    import run_etl

    cache = Path(settings.etl_temp_dir) / "datacommune_vacance" / "raw"
    cache.mkdir(parents=True)
    (cache / "lovac_communes.csv").write_bytes(b"CODGEO_26\n44001\n")
    sa.archive_sources(
        archive,
        "vacance",
        RUN_ID,
        [cache / "lovac_communes.csv"],
        cache.parent,
        origin="telechargement",
    )
    seen = {}

    class _Runner:
        def __init__(self, *a, **k):
            pass

        def run(self, pipeline, *, dry_run=False, source_replay=None):
            seen.update(
                dataset=pipeline.dataset_name, dry=dry_run, replay=source_replay
            )
            from app.etl.schemas import EtlResult

            return EtlResult(
                dataset_name="vacance",
                stages=[],
                total_rows_loaded=0,
                total_duration_seconds=0,
                success=True,
            )

    monkeypatch.setattr(run_etl, "PipelineRunner", _Runner)
    monkeypatch.setattr(run_etl, "_hosted_selection", lambda requested, _log: requested)
    monkeypatch.setattr(
        run_etl.sys, "argv", ["run_etl", "vacance", "--from-archive", RUN_ID]
    )
    logging.disable(logging.CRITICAL)
    try:
        run_etl.main()
    finally:
        logging.disable(logging.NOTSET)

    assert seen["dataset"] == "vacance" and seen["dry"] is False
    assert seen["replay"].run_id == RUN_ID
