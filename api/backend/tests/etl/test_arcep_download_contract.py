"""What ``ArcepPipeline.download()`` hands to the runner (plan 02-22).

One directory per expected departement, two gz files each; the source archive
and the etl_runs journal expand them into four files. No network: the
verified download of the pipeline module is replaced by a local writer.
"""

from pathlib import Path

import pytest

from app.config import settings
from app.etl import source_archive as sa
from app.etl.download import get_download_dir
from app.etl_arcep.pipeline import ArcepPipeline

pytestmark = pytest.mark.unit

_DEPARTEMENTS = ("44", "85")
_S3 = "https://arcep.s3.rbx.io.cloud.ovh.net/fixe/maconnexioninternet"
# File name -> final URL reached by the download (302 of `last`), per test.
redirections: dict[str, str] = {}


def _redirige(**millesimes: str) -> None:
    """Every file of each departement served from its quarterly folder."""
    for dept, millesime in millesimes.items():
        for dossier, nom in (("base_imb", "base_imb"), ("eligibilite", "actuel")):
            redirections[f"{nom}_{dept[1:]}.csv.gz"] = (
                f"{_S3}/{dossier}/{millesime}/departement/{nom}_{dept[1:]}.csv.gz"
            )


@pytest.fixture
def downloads(tmp_path, monkeypatch, bind_departements):
    bind_departements(_DEPARTEMENTS, ArcepPipeline)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    urls: list[str] = []
    redirections.clear()

    async def fake_verified_download(_client, url, output_path, **kwargs):
        urls.append(url)
        if final := redirections.get(output_path.name):
            kwargs["on_final_url"](final)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(f"{output_path.name}\n".encode())
        return True

    monkeypatch.setattr(
        "app.etl_arcep.pipeline.verified_download", fake_verified_download
    )
    return urls


def test_download_returns_one_directory_per_expected_departement(downloads):
    paths = ArcepPipeline().download()

    raw = get_download_dir("connectivite") / "raw"
    assert paths == [raw / "44", raw / "85"]
    for dept, directory in zip(_DEPARTEMENTS, paths, strict=True):
        assert directory.is_dir()
        assert sorted(p.name for p in directory.iterdir()) == [
            f"actuel_{dept}.csv.gz",
            f"base_imb_{dept}.csv.gz",
        ]
    assert len(downloads) == 4
    assert all("/last/" in url for url in downloads)


def test_the_archive_expands_the_directories_into_four_files(downloads):
    cache = get_download_dir("connectivite")
    paths = ArcepPipeline().download()

    files = sa.expand_source_paths(paths, cache)

    assert [Path(f).relative_to(cache).as_posix() for f in files] == [
        "raw/44/actuel_44.csv.gz",
        "raw/44/base_imb_44.csv.gz",
        "raw/85/actuel_85.csv.gz",
        "raw/85/base_imb_85.csv.gz",
    ]
    assert all(f.is_file() for f in files)


# --- served millésime (C24 a, WINDOWS n° 86) -------------------------------------


def test_the_quarter_reached_through_last_is_the_period_of_the_run(downloads):
    _redirige(d44="2026_T2", d85="2026_T2")
    pipeline = ArcepPipeline()

    pipeline.download()

    assert all("/last/" in url for url in downloads)
    assert pipeline.source_period_label == "2026_T2"


def test_files_from_two_quarters_name_no_period(downloads):
    _redirige(d44="2026_T2", d85="2026_T1")
    pipeline = ArcepPipeline()

    pipeline.download()

    assert pipeline.source_period_label is None


def test_a_final_url_without_quarter_names_no_period(downloads):
    redirections["actuel_44.csv.gz"] = f"{_S3}/eligibilite/last/actuel_44.csv.gz"
    pipeline = ArcepPipeline()

    pipeline.download()

    assert pipeline.source_period_label is None


async def test_the_download_reports_the_url_it_finally_read(tmp_path):
    import httpx

    from app.etl.download import streaming_download

    last = f"{_S3}/eligibilite/last/departement/actuel_44.csv.gz"
    dated = f"{_S3}/eligibilite/2026_T2/departement/actuel_44.csv.gz"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == last:
            return httpx.Response(302, headers={"Location": dated})
        return httpx.Response(200, content=b"ok")

    finales: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await streaming_download(
            client, last, tmp_path / "a.csv.gz", on_final_url=finales.append
        )

    assert ok is True
    assert finales == [dated]


# --- quarter kept by an archive replay (C30, WINDOWS n° 91) ----------------------


def _replay(tmp_path, record_extra: dict) -> sa.SourceReplay:
    """A verified replay of connectivite whose record carries *record_extra*."""
    root = tmp_path / "archive"
    root.mkdir()
    (root / sa.SENTINEL_NAME).write_text(sa.SENTINEL_CONTENT + "\n", "utf-8")
    cache = tmp_path / "cache"
    source = cache / "raw" / "44" / "actuel_44.csv.gz"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"actuel\n")
    sa.archive_sources(
        root,
        "connectivite",
        "36-20260926T201500Z",
        [source],
        cache,
        origin="telechargement",
        **record_extra,
    )
    return sa.load_replay(root, "connectivite", "36-20260926T201500Z")


def test_an_archive_replay_keeps_the_quarter_of_the_record(tmp_path):
    replay = _replay(tmp_path, {"source_period_label": "2026_T2"})
    pipeline = ArcepPipeline()

    pipeline.restore_source_inputs(replay, tmp_path / "restored")

    assert pipeline.source_period_label == "2026_T2"


@pytest.mark.parametrize("extra", [{}, {"source_period_label": "Export 2026-09"}])
def test_an_archive_replay_without_a_recorded_quarter_names_no_period(tmp_path, extra):
    """Records written before C30 (run 36) carry none: nothing is invented."""
    replay = _replay(tmp_path, extra)
    pipeline = ArcepPipeline()

    pipeline.restore_source_inputs(replay, tmp_path / "restored")

    assert pipeline.source_period_label is None


def test_the_downloaded_quarter_is_the_one_the_runner_archives(downloads):
    """The runner archives ``source_period_label`` after the download."""
    _redirige(d44="2026_T2", d85="2026_T2")
    pipeline = ArcepPipeline()

    paths = pipeline.download()

    assert paths and pipeline.source_period_label == "2026_T2"
