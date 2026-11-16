"""BD TOPO: producer .7z kept as downloaded, native 7z extraction (plan 04.1-05).

Subprocesses (``7z``, ``ogrinfo``, ``ogr2ogr``) and the database are faked:
the tests prove the file path of the pipeline, not GDAL. The ``7z l -slt``
listing reproduces the form printed by 7-Zip 25.01 of Debian trixie (the
``7zip`` package of the API image), directory and link entries included.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import respx

from app.config import settings
from app.etl import source_archive as sa
from app.etl.errors import BdTopoDepartementMissingError
from app.etl_bdtopo.config import bdtopo_url
from app.etl_bdtopo.pipeline import BdTopoPipeline

pytestmark = pytest.mark.unit

RUN = "40-20261012T215130Z"

GPKG_MEMBER = (
    "BDTOPO/1_DONNEES_LIVRAISON_2025-12-00078/"
    "BDT_3-5_GPKG_LAMB93_D{d}_ED2025-12-15/BDT_3-5_GPKG_LAMB93_D{d}-ED2025-12-15.gpkg"
)
COLUMNS = (
    "cleabs",
    "nature",
    "usage_1",
    "nombre_d_etages",
    "hauteur",
    "altitude_minimale_sol",
    "altitude_maximale_toit",
)


def _member(path: str, size: int = 0, attrs: str = "A -rw-r--r--") -> dict:
    return {"path": path, "size": size, "attrs": attrs}


def _department_members(dept: str) -> list[dict]:
    """Directory entries, one GPKG and a side file, as in an IGN archive."""
    padded = dept.zfill(3)
    gpkg = GPKG_MEMBER.format(d=padded)
    folders = gpkg.split("/")[:-1]
    entries = [
        _member("/".join(folders[: i + 1]), 0, "D drwxr-xr-x")
        for i in range(len(folders))
    ]
    entries.append(_member(gpkg, 4096))
    entries.append(_member("BDTOPO/LISEZ-MOI.txt", 12))
    return entries


def _listing(archive: Path, members: list[dict]) -> str:
    blocks = [
        "\n7-Zip 25.01 (x64) : Copyright (c) 1999-2025 Igor Pavlov : 2025-08-03\n",
        "Scanning the drive for archives:\n1 file, 3237 bytes (4 KiB)\n",
        f"Listing archive: {archive}\n",
        f"--\nPath = {archive}\nType = 7z\nPhysical Size = 3237\n"
        "Headers Size = 219\nMethod = LZMA2:12\nSolid = +\nBlocks = 1\n",
        "----------",
    ]
    blocks.extend(
        f"Path = {m['path']}\nSize = {m['size']}\nPacked Size = \n"
        "Modified = 2026-10-13 12:27:22.0030233\n"
        f"Attributes = {m['attrs']}\nCRC = \nEncrypted = -\nMethod = \nBlock = \n"
        for m in members
    )
    return "\n".join(blocks) + "\n"


@dataclass
class FakeTools:
    """Fake 7z / ogrinfo / ogr2ogr keyed by archive name; records every call."""

    members: dict[str, list[dict]] = field(default_factory=dict)
    features: dict[str, int] = field(default_factory=dict)
    columns: tuple[str, ...] = COLUMNS
    calls: list[list[str]] = field(default_factory=list)
    gpkg_after_extract: list[int] = field(default_factory=list)
    rows: int = 0
    work_root: Path | None = None

    def run(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        tool = Path(cmd[0]).name
        if tool == "7z" and cmd[1] == "l":
            archive = Path(cmd[-1])
            return subprocess.CompletedProcess(
                cmd, 0, _listing(archive, self.members[archive.name]), ""
            )
        if tool == "7z" and cmd[1] == "x":
            archive = Path(cmd[-1])
            out = Path(next(c[2:] for c in cmd if c.startswith("-o")))
            for m in self.members[archive.name]:
                target = out / m["path"]
                if m["attrs"].startswith("D"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"\0" * min(m["size"], 64))
            if self.work_root is not None:
                self.gpkg_after_extract.append(
                    len(list(self.work_root.rglob("*.gpkg")))
                )
            return subprocess.CompletedProcess(cmd, 0, "Everything is Ok\n", "")
        if tool == "ogrinfo":
            gpkg = Path(cmd[-2])
            count = self.features[gpkg.name]
            fields = "\n".join(f"{c}: String (0.0)" for c in self.columns)
            out = (
                f"INFO: Open of `{gpkg}'\n      using driver `GPKG' successful.\n\n"
                f"Layer name: batiment\nGeometry: 3D Multi Polygon\n"
                f"Feature Count: {count}\nExtent: (1.0, 2.0) - (3.0, 4.0)\n"
                f"Layer SRS WKT:\n(unknown)\nFID Column = fid\n"
                f"Geometry Column = geometrie\n{fields}\n"
            )
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if tool == "ogr2ogr":
            source = Path(cmd[4])
            self.rows += self.features[source.name]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected subprocess {cmd}")

    def extractions(self) -> list[list[str]]:
        return [c for c in self.calls if Path(c[0]).name == "7z" and c[1] == "x"]


class _FakeCursor:
    def __init__(self, tools: FakeTools) -> None:
        self._tools = tools

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        return self

    def fetchone(self):
        return (self._tools.rows,)


class _FakeConn:
    def __init__(self, tools: FakeTools) -> None:
        self._tools = tools

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._tools)

    def commit(self):
        return None


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    return tmp_path / "etl_tmp" / "datacommune_batiments"


@pytest.fixture
def tools(monkeypatch, cache):
    fake = FakeTools(work_root=cache / "work")
    monkeypatch.setattr("app.etl_bdtopo.pipeline.subprocess.run", fake.run)
    monkeypatch.setattr(
        "app.etl_bdtopo.pipeline.psycopg.connect", lambda _dsn: _FakeConn(fake)
    )
    return fake


@pytest.fixture
def pipeline(bind_departements):
    bind_departements(("44", "85"), BdTopoPipeline)
    return BdTopoPipeline()


def _archive(cache: Path, dept: str, tools: FakeTools, features: int = 7) -> Path:
    path = cache / "raw" / f"bdtopo_{dept}.7z"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"7z\xbc\xaf\x27\x1c producer bytes " + dept.encode())
    tools.members[path.name] = _department_members(dept)
    tools.features[Path(GPKG_MEMBER.format(d=dept.zfill(3))).name] = features
    return path


def _process(pipeline: BdTopoPipeline, path: Path) -> int:
    """The runner's per-file sequence: validate, transform, stage."""
    pipeline.validate_schema(path)
    return pipeline.stage(pipeline.transform(path))


# ------------------------------------------------------------- download --


def test_download_returns_producer_archives_without_extraction(
    pipeline, cache, monkeypatch
):
    def no_subprocess(*args, **kwargs):
        raise AssertionError("download must not extract")

    monkeypatch.setattr("app.etl_bdtopo.pipeline.subprocess.run", no_subprocess)
    payload = {d: b"7z\xbc\xaf\x27\x1c bytes of " + d.encode() for d in ("44", "85")}
    with respx.mock(assert_all_called=True) as mock:
        for dept, body in payload.items():
            mock.get(bdtopo_url(dept.zfill(3), None)).mock(
                return_value=httpx.Response(200, content=body)
            )
        files = pipeline.download()

    assert [p.name for p in files] == ["bdtopo_44.7z", "bdtopo_85.7z"]
    for path, dept in zip(files, ("44", "85"), strict=True):
        assert path.parent == cache / "raw"
        assert path.read_bytes() == payload[dept]  # producer bytes, exact
    assert not list(cache.rglob("*.gpkg"))
    assert not list(cache.rglob("*.dept"))


# ------------------------------------------------------- refused archives --


@pytest.mark.parametrize(
    "member",
    [
        "../x.gpkg",
        "/etc/x",
        "BDTOPO/../../x.gpkg",
        "BDTOPO\\..\\..\\x.gpkg",
        "C:/x.gpkg",
    ],
)
def test_transform_refuses_unsafe_paths_before_writing(pipeline, cache, tools, member):
    path = _archive(cache, "44", tools)
    tools.members[path.name].append(_member(member, 10))

    with pytest.raises(BdTopoDepartementMissingError, match="chemin"):
        pipeline.transform(path)

    assert tools.extractions() == []
    assert not (cache / "work").exists() or not any((cache / "work").rglob("*"))


def test_transform_refuses_link_member(pipeline, cache, tools):
    path = _archive(cache, "44", tools)
    tools.members[path.name].append(_member("BDTOPO/lnk", 11, "A lrwxrwxrwx"))

    with pytest.raises(BdTopoDepartementMissingError, match="lien"):
        pipeline.transform(path)
    assert tools.extractions() == []


def test_transform_refuses_declared_size_above_cap(pipeline, cache, tools, monkeypatch):
    path = _archive(cache, "44", tools)
    monkeypatch.setattr(settings, "etl_max_decompressed_bytes", 4100)

    with pytest.raises(BdTopoDepartementMissingError, match="4108"):
        pipeline.transform(path)

    assert tools.extractions() == []
    assert not list(cache.rglob("*.gpkg"))


@pytest.mark.parametrize("gpkg_count", [0, 2])
def test_transform_requires_exactly_one_gpkg(pipeline, cache, tools, gpkg_count):
    path = _archive(cache, "44", tools)
    members = [m for m in tools.members[path.name] if not m["path"].endswith(".gpkg")]
    members += [_member(f"BDTOPO/x{i}.gpkg", 10) for i in range(gpkg_count)]
    tools.members[path.name] = members

    with pytest.raises(BdTopoDepartementMissingError, match="gpkg"):
        pipeline.transform(path)
    assert tools.extractions() == []


def test_schema_of_extracted_gpkg_is_still_checked(pipeline, cache, tools):
    path = _archive(cache, "44", tools)
    tools.columns = tuple(c for c in COLUMNS if c != "hauteur")

    with pytest.raises(ValueError, match="hauteur"):
        pipeline.validate_schema(path)


# ------------------------------------------------- extraction and cleanup --


def test_extraction_goes_to_department_work_dir(pipeline, cache, tools):
    path = _archive(cache, "44", tools)

    gpkg = pipeline.transform(path)

    assert gpkg.is_file()
    assert gpkg.suffix == ".gpkg"
    assert gpkg.is_relative_to(cache / "work" / "44")
    (extract,) = tools.extractions()
    assert extract[:4] == ["7z", "x", "-y", "-bd"]
    assert f"-o{cache / 'work' / '44'}" in extract


def test_stage_counts_features_then_removes_gpkg_and_archive(pipeline, cache, tools):
    path = _archive(cache, "44", tools, features=1234)

    rows = _process(pipeline, path)

    assert rows == 1234
    assert pipeline.stage_counters() == {"producer_features_44": 1234}
    assert not path.exists()  # already archived by the runner
    assert not (cache / "work" / "44").exists()
    assert not list(cache.rglob("*.gpkg"))
    (load,) = [c for c in tools.calls if c[0] == "ogr2ogr"]
    assert load[4].endswith("-ED2025-12-15.gpkg")


def test_departments_follow_each_other_one_gpkg_at_a_time(pipeline, cache, tools):
    files = [_archive(cache, d, tools, features=n) for d, n in (("44", 5), ("85", 9))]

    total = sum(_process(pipeline, path) for path in files)

    assert total == 14
    assert tools.gpkg_after_extract == [1, 1]
    assert pipeline.stage_counters() == {
        "producer_features_44": 5,
        "producer_features_85": 9,
    }
    assert not list(cache.rglob("*.gpkg"))
    assert not list(cache.rglob("*.7z"))
    assert len(tools.extractions()) == 2  # validate extracts, transform reuses


def test_failed_load_keeps_archive_and_next_extraction_clears_work(
    pipeline, cache, tools, monkeypatch
):
    first = _archive(cache, "44", tools)
    second = _archive(cache, "85", tools)
    pipeline.validate_schema(first)
    gpkg = pipeline.transform(first)
    monkeypatch.setattr(
        pipeline, "_load_gpkg", lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with pytest.raises(RuntimeError):
        pipeline.stage(gpkg)
    assert first.exists()  # kept for the retry

    pipeline.validate_schema(second)

    assert tools.gpkg_after_extract == [1, 1]
    assert not (cache / "work" / "44").exists()


def test_dry_run_validation_alone_keeps_one_gpkg(pipeline, cache, tools):
    files = [_archive(cache, d, tools) for d in ("44", "85")]

    for path in files:
        pipeline.validate_schema(path)

    assert len(list(cache.rglob("*.gpkg"))) == 1


# ------------------------------------------------------ replay from archive --

OLD_GPKG_RELPATH = (
    "raw/bdtopo_44/BDTOPO_3-5_TOUSTHEMES_GPKG_LAMB93_D044_2025-12-15/"
    + GPKG_MEMBER.format(d="044")
)


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    (root / sa.SENTINEL_NAME).write_text(sa.SENTINEL_CONTENT + "\n", "utf-8")
    monkeypatch.setattr(settings, "source_archive_dir", str(root))
    return root


def _replay_of(root: Path, cache: Path, files: list[Path]) -> sa.SourceReplay:
    """Archive *files* like a run, empty the cache, load the verified record."""
    sa.archive_sources(root, "batiments", RUN, files, cache, origin="telechargement")
    shutil.rmtree(cache)
    return sa.load_replay(root, "batiments", RUN)


def _objects(root: Path) -> dict[Path, tuple[int, int, bytes]]:
    return {
        p: (p.stat().st_mode, p.stat().st_mtime_ns, p.read_bytes())
        for p in (root / "batiments" / "objects").glob("*/*")
    }


def test_archive_replay_is_declared():
    assert BdTopoPipeline.archive_replay is True


def test_replay_of_a_7z_record_links_then_extracts_one_department_at_a_time(
    pipeline, cache, tools, archive_root
):
    files = [_archive(cache, d, tools, features=n) for d, n in (("44", 5), ("85", 9))]
    replay = _replay_of(archive_root, cache, files)
    objects = _objects(archive_root)

    inputs = pipeline.restore_source_inputs(replay, cache)

    assert [p.relative_to(cache).as_posix() for p in inputs] == [
        "raw/bdtopo_44.7z",
        "raw/bdtopo_85.7z",
    ]
    assert all(p.is_symlink() for p in inputs)
    assert sum(_process(pipeline, path) for path in inputs) == 14
    assert tools.gpkg_after_extract == [1, 1]
    assert pipeline.stage_counters() == {
        "producer_features_44": 5,
        "producer_features_85": 9,
    }
    assert not any(p.is_symlink() for p in cache.rglob("*"))  # links removed
    assert not list(cache.rglob("*.gpkg"))
    assert _objects(archive_root) == objects  # objects kept, mode unchanged
    assert sa.verify(archive_root)["records"] == 1


def test_replay_of_an_old_gpkg_record_loads_the_gpkg_as_is(
    pipeline, cache, tools, archive_root
):
    """Records of phase 2 (imports 44/85) hold the GPKG: loaded, not converted."""
    gpkg = cache / OLD_GPKG_RELPATH
    gpkg.parent.mkdir(parents=True)
    gpkg.write_bytes(b"SQLite format 3\0 gpkg of 44")
    tools.features[gpkg.name] = 11
    replay = _replay_of(archive_root, cache, [gpkg])
    objects = _objects(archive_root)

    (linked,) = pipeline.restore_source_inputs(replay, cache)

    assert linked == cache / OLD_GPKG_RELPATH
    assert linked.is_symlink()
    pipeline.validate_schema(linked)
    assert pipeline.transform(linked) == linked
    assert pipeline.stage(linked) == 11
    assert pipeline.stage_counters() == {"producer_features_44": 11}
    assert tools.extractions() == []  # no extraction, no conversion
    assert not linked.is_symlink() and not linked.exists()  # the link went
    assert _objects(archive_root) == objects
