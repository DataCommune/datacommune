"""BD TOPO overseas: own URL and projection, franc failure (D-122, plan 04.1-06).

The producer publishes each DROM in its own projection, named in the URL
(``LAMB93`` answers 400 for a DROM); the GPKG geometries are in that
projection, so ``ogr2ogr -s_srs`` must follow the departement. A hosted
departement the producer does not deliver (404, refused download, empty or
unextractable archive) ends the import: no "n succeeded, m failed" warning,
nothing published (ROADMAP 4.1, criterion 4).

Subprocesses, HTTP and the database are faked; the real URLs are checked by
the HEAD command of the plan, the real projection on the témoin (plan 21).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from app.config import settings
from app.etl.errors import BdTopoDepartementMissingError, FatalInputError
from app.etl.runner import PipelineRunner
from app.etl_bdtopo.config import (
    BDTOPO_TERRITORIES,
    bdtopo_territory,
    bdtopo_url,
)
from app.etl_bdtopo.pipeline import BdTopoPipeline
from tests.etl.test_runner import FakePipeline, _patch_all

pytestmark = pytest.mark.unit

DROM = {
    "971": ("RGAF09UTM20", "EPSG:5490"),
    "972": ("RGAF09UTM20", "EPSG:5490"),
    "973": ("UTM22RGFG95", "EPSG:2972"),
    "974": ("RGR92UTM40S", "EPSG:2975"),
    "976": ("RGM04UTM38S", "EPSG:4471"),
}


@pytest.fixture(autouse=True)
def _host_synthetic_dataset(host_test_datasets):
    host_test_datasets("test_ds")


# ------------------------------------------------------------ territories --


@pytest.mark.parametrize(("dept", "expected"), sorted(DROM.items()))
def test_each_drom_has_its_own_url_token_and_epsg(dept, expected):
    token, epsg = expected
    assert bdtopo_territory(dept) == (token, epsg)
    url = bdtopo_url(dept, None)
    assert "LAMB93" not in url
    assert f"_GPKG_{token}_D{dept}_2025-12-15/" in url
    assert url.endswith(f"_GPKG_{token}_D{dept}_2025-12-15.7z")


def test_table_covers_exactly_the_five_drom():
    # 975 is a collectivity (D-123): never hosted, never in the table.
    assert set(BDTOPO_TERRITORIES) == set(DROM)


@pytest.mark.parametrize("dept", ["44", "044", "2A", "02A", "85"])
def test_metropolitan_departements_stay_lambert_93(dept):
    assert bdtopo_territory(dept) == ("LAMB93", "EPSG:2154")
    assert "_GPKG_LAMB93_D" in bdtopo_url(dept.zfill(3), None)


def test_pinned_edition_keeps_the_territory_token():
    url = bdtopo_url("971", "2025-09-15")
    assert "_GPKG_RGAF09UTM20_D971_2025-09-15/" in url


# ------------------------------------------------------- source projection --


class _Conn:
    """psycopg double: every count reads the rows "loaded" so far."""

    rows = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return (_Conn.rows,)

    def commit(self):
        return None


@pytest.fixture
def ogr_calls(monkeypatch):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        _Conn.rows += 3
        return subprocess.CompletedProcess(cmd, 0, "", "")

    _Conn.rows = 0
    monkeypatch.setattr("app.etl_bdtopo.pipeline.subprocess.run", run)
    monkeypatch.setattr("app.etl_bdtopo.pipeline.psycopg.connect", lambda _d: _Conn())
    return calls


def _s_srs(cmd: list[str]) -> str:
    return cmd[cmd.index("-s_srs") + 1]


@pytest.mark.parametrize(
    ("dept", "epsg"),
    [("44", "EPSG:2154"), ("2A", "EPSG:2154")]
    + [(d, epsg) for d, (_, epsg) in sorted(DROM.items())],
)
def test_ogr2ogr_reprojects_from_the_departement_projection(
    bind_departements, ogr_calls, tmp_path, dept, epsg
):
    bind_departements((dept,), BdTopoPipeline)
    gpkg = tmp_path / f"bdtopo_{dept}" / "x.gpkg"

    rows = BdTopoPipeline()._load_gpkg(gpkg, dept)

    assert rows == 3
    (cmd,) = ogr_calls
    assert cmd[0] == "ogr2ogr"
    assert _s_srs(cmd) == epsg
    assert cmd[cmd.index("-t_srs") + 1] == "EPSG:4326"


# --------------------------------------------------------- franc failure --


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    return tmp_path / "etl_tmp" / "datacommune_batiments"


@pytest.mark.parametrize(
    ("status", "reason"), [(404, "404 du producteur"), (400, "HTTP 400")]
)
def test_undelivered_hosted_departement_fails_the_download(
    bind_departements, cache, monkeypatch, status, reason
):
    monkeypatch.setattr(
        "app.etl_bdtopo.pipeline.subprocess.run",
        lambda *a, **k: pytest.fail("nothing is extracted"),
    )
    bind_departements(("44", "971", "85"), BdTopoPipeline)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(bdtopo_url("044", None)).mock(
            return_value=httpx.Response(200, content=b"7z 44")
        )
        mock.get(bdtopo_url("971", None)).mock(return_value=httpx.Response(status))
        later = mock.get(bdtopo_url("085", None)).mock(
            return_value=httpx.Response(200, content=b"7z 85")
        )
        with pytest.raises(BdTopoDepartementMissingError) as exc:
            BdTopoPipeline().download()

    assert exc.value.departement == "971"
    assert reason in str(exc.value)
    assert isinstance(exc.value, FatalInputError)
    assert not later.called  # fails at once, the rest is not downloaded


def test_refused_download_keeps_its_transient_cause(bind_departements, cache):
    bind_departements(("971",), BdTopoPipeline)
    with respx.mock() as mock:
        mock.get(bdtopo_url("971", None)).mock(side_effect=httpx.ConnectError("x"))
        with pytest.raises(BdTopoDepartementMissingError) as exc:
            BdTopoPipeline().download()
    # The runner's retry classification still sees the network cause.
    assert isinstance(exc.value.__cause__, httpx.ConnectError)


@pytest.mark.parametrize(
    "listing",
    [
        "no separator",  # unreadable listing
        "\n----------\n",  # empty archive
    ],
)
def test_empty_or_unreadable_archive_is_an_undelivered_departement(
    bind_departements, cache, monkeypatch, listing
):
    bind_departements(("971",), BdTopoPipeline)
    archive = cache / "raw" / "bdtopo_971.7z"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"7z")
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, listing, "")

    monkeypatch.setattr("app.etl_bdtopo.pipeline.subprocess.run", run)

    with pytest.raises(BdTopoDepartementMissingError) as exc:
        BdTopoPipeline().validate_schema(archive)

    assert exc.value.departement == "971"
    assert [c[1] for c in calls] == ["l"]  # listed, never extracted
    assert not (cache / "work").exists() or not any((cache / "work").rglob("*"))


def test_failed_extraction_is_an_undelivered_departement(
    bind_departements, cache, monkeypatch
):
    bind_departements(("971",), BdTopoPipeline)
    archive = cache / "raw" / "bdtopo_971.7z"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"7z")
    listing = (
        "\n----------\nPath = BDTOPO/x.gpkg\nSize = 10\nAttributes = A -rw-r--r--\n\n"
    )

    def run(cmd, **kwargs):
        if cmd[1] == "x":
            raise subprocess.CalledProcessError(2, cmd, "", "Data Error")
        return subprocess.CompletedProcess(cmd, 0, listing, "")

    monkeypatch.setattr("app.etl_bdtopo.pipeline.subprocess.run", run)

    with pytest.raises(BdTopoDepartementMissingError, match="Data Error"):
        BdTopoPipeline().transform(archive)
    assert not (cache / "work").exists() or not any((cache / "work").rglob("*"))


# ---------------------------------------------- runner: nothing published --


class _Undelivered(FakePipeline):
    """First file raises the undelivered error; the second one is valid."""

    def validate_schema(self, file_path: Path) -> None:
        if file_path.name == "bdtopo_971.7z":
            raise BdTopoDepartementMissingError("971", "archive vide")


@pytest.fixture
def dropped(monkeypatch) -> list[str]:
    """Staging drops of a failed run (``_safe_drop`` opens a connection)."""
    calls: list[str] = []
    monkeypatch.setattr(
        PipelineRunner,
        "_safe_drop",
        lambda _self, pipeline, _dsn, _log: calls.append(pipeline.dataset_name),
    )
    return calls


def _two_files(tmp_path: Path) -> list[Path]:
    files = [tmp_path / "bdtopo_971.7z", tmp_path / "bdtopo_44.7z"]
    for f in files:
        f.write_bytes(b"7z")
    return files


def test_runner_fails_on_an_undelivered_departement_and_publishes_nothing(
    tmp_path, dropped
):
    pipeline = _Undelivered()
    pipeline._files = _two_files(tmp_path)
    pipeline.swap = lambda: pytest.fail("nothing is published")
    staged: list[str] = []
    pipeline.stage = lambda path: staged.append(path.name) or 10

    with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
        result = PipelineRunner().run(pipeline)

    assert not result.success
    assert "971" in (result.error or "")
    assert staged == []  # failed before the next departement was loaded
    mocks["publication"].assert_not_called()
    assert dropped == ["test_ds"]  # the candidate is dropped


def test_runner_failure_on_download_publishes_nothing(tmp_path, dropped):
    pipeline = FakePipeline()

    def download() -> list[Path]:
        raise BdTopoDepartementMissingError("976", "404 du producteur")

    pipeline.download = download
    pipeline.swap = lambda: pytest.fail("nothing is published")

    with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
        result = PipelineRunner().run(pipeline)

    assert not result.success
    assert "976" in (result.error or "")
    mocks["publication"].assert_not_called()
    assert dropped == ["test_ds"]


def test_an_ordinary_file_failure_is_still_skipped(tmp_path):
    """Control: only a FatalInputError ends the loop; other failures keep the
    existing skip, judged by the success ratio and the coverage check."""

    class _Ordinary(FakePipeline):
        def validate_schema(self, file_path: Path) -> None:
            if file_path.name == "bdtopo_971.7z":
                raise ValueError("colonne absente")

    pipeline = _Ordinary()
    pipeline._files = _two_files(tmp_path)
    staged: list[str] = []
    pipeline.stage = lambda path: staged.append(path.name) or 10

    with _patch_all(staging_exists=False, checkpoint_data=None):
        PipelineRunner().run(pipeline)

    assert staged == ["bdtopo_44.7z"]
