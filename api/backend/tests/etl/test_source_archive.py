"""Source archive of every import (plan 02-14, D-29/D-30 amended, OPS-03).

Module behaviour on ``tmp_path``: writing objects and records, the refusals
that must leave the archive untouched, the verified reimport, ``verify`` and
``stats``. Free space is simulated by substituting ``shutil.disk_usage``.
"""

import hashlib
import json
import os
import stat
from collections import namedtuple
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.etl import source_archive as sa
from scripts import export_import_manifest as eim

pytestmark = pytest.mark.unit

RUN_A = "2-20260923T001821Z"
RUN_B = "3-20260924T101500Z"
Usage = namedtuple("Usage", "total used free")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def root(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / sa.SENTINEL_NAME).write_text(sa.SENTINEL_CONTENT + "\n", "utf-8")
    return archive


@pytest.fixture
def cache(tmp_path):
    directory = tmp_path / "cache" / "datacommune_vacance"
    (directory / "raw").mkdir(parents=True)
    return directory


def _source(cache: Path, relpath: str, data: bytes) -> Path:
    path = cache / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _archive(root, cache, files, run_id=RUN_A, **kwargs):
    kwargs.setdefault("origin", "telechargement")
    return sa.archive_sources(root, "vacance", run_id, files, cache, **kwargs)


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _object(root: Path, data: bytes) -> Path:
    digest = _sha(data)
    return root / "vacance" / "objects" / digest[:2] / digest


# --------------------------------------------------------------- run id --


@pytest.mark.parametrize(
    "started_at",
    [
        datetime(2026, 9, 23, 0, 18, 21, 345678, tzinfo=UTC),
        datetime(2026, 9, 23, 2, 18, 21, tzinfo=timezone(timedelta(hours=2))),
    ],
)
def test_run_id_is_the_composition_of_the_import_receipt(started_at):
    assert sa.compose_run_id(2, started_at) == eim.compose_run_id(2, started_at)
    assert sa.compose_run_id(2, started_at) == RUN_A


def test_an_import_script_run_is_named_by_its_launch_stamp(root, cache):
    """Scripts have no etl_runs row (plan 02-11): bare UTC stamp, no etl id."""
    path = _source(cache, "raw/departements.geojson", b"{}")
    record = _archive(root, cache, [path], run_id="20260924T220000Z")
    assert (record["run_id"], record["etl_run_id"]) == ("20260924T220000Z", None)
    assert record["started_at"] == "2026-09-24T22:00:00Z"
    replay = sa.load_replay(root, "vacance", "20260924T220000Z")
    assert replay.run_id == "20260924T220000Z"


@pytest.mark.parametrize(
    "run_id", ["0-20260924T220000Z", "20260924T2200Z", "x-20260924T220000Z", "12"]
)
def test_malformed_run_ids_stay_refused(root, cache, run_id):
    path = _source(cache, "raw/a.csv", b"a")
    with pytest.raises(sa.SourceArchiveError, match="run_id_invalide"):
        _archive(root, cache, [path], run_id=run_id)


# ------------------------------------------------------------- archive --


def test_archive_stores_each_file_once_and_writes_the_record_last(root, cache):
    data = b"CODGEO_26;LIBGEO_26\n44001;A\n"
    path = _source(cache, "raw/lovac_communes.csv", data)

    record = _archive(root, cache, [path], deployment_sha="a" * 40)

    obj = _object(root, data)
    assert obj.read_bytes() == data
    assert stat.S_IMODE(obj.stat().st_mode) & 0o222 == 0
    record_path = root / "vacance" / "runs" / f"{RUN_A}.json"
    assert json.loads(record_path.read_text("utf-8")) == record
    assert stat.S_IMODE(record_path.stat().st_mode) & 0o222 == 0
    assert record["version"] == 1
    assert (record["dataset"], record["run_id"], record["etl_run_id"]) == (
        "vacance",
        RUN_A,
        2,
    )
    assert record["started_at"] == "2026-09-23T00:18:21Z"
    assert (record["origin"], record["replayed_from"]) == ("telechargement", None)
    assert record["deployment_sha"] == "a" * 40
    assert record["files"] == [
        {
            "relpath": "raw/lovac_communes.csv",
            "name": "lovac_communes.csv",
            "bytes": len(data),
            "sha256": _sha(data),
            "object": f"objects/{_sha(data)[:2]}/{_sha(data)}",
            "stored": True,
        }
    ]
    # Keys sorted in the file, no temporary left behind.
    text = record_path.read_text("utf-8")
    assert text == json.dumps(record, sort_keys=True, indent=2) + "\n"
    assert not [p for p in root.rglob("*") if p.name.startswith(".tmp")]


def test_same_content_is_stored_once_and_reused_after_rereading_it(root, cache):
    data = b"same bytes\n"
    first = _source(cache, "raw/a.csv", data)
    _archive(root, cache, [first])
    before = _object(root, data).stat()

    record = _archive(root, cache, [first], run_id=RUN_B)

    assert record["files"][0]["stored"] is False
    after = _object(root, data).stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_two_files_with_one_basename_keep_two_relpaths(root, cache):
    a = _source(cache, "raw/2023/44.csv", b"a\n")
    b = _source(cache, "raw/2024/44.csv", b"b\n")

    record = _archive(root, cache, [a, b])

    assert [f["relpath"] for f in record["files"]] == [
        "raw/2023/44.csv",
        "raw/2024/44.csv",
    ]
    assert [f["name"] for f in record["files"]] == ["44.csv", "44.csv"]


def test_file_outside_the_cache_is_recorded_under_its_basename(root, cache, tmp_path):
    frozen = tmp_path / "frozen" / "lovac_communes.csv"
    frozen.parent.mkdir()
    frozen.write_bytes(b"frozen\n")

    record = _archive(root, cache, [frozen])

    assert record["files"][0]["relpath"] == "lovac_communes.csv"


def test_missing_sentinel_writes_nothing(tmp_path, cache):
    bare = tmp_path / "not-mounted"
    bare.mkdir()
    path = _source(cache, "raw/a.csv", b"a\n")

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(bare, cache, [path])

    assert err.value.reason == "sentinelle_absente"
    assert _tree(bare) == []


def test_unexpected_sentinel_content_is_refused(root, cache):
    (root / sa.SENTINEL_NAME).write_text("something else\n", "utf-8")
    path = _source(cache, "raw/a.csv", b"a\n")

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [path])

    assert err.value.reason == "sentinelle_absente"
    assert _tree(root) == [sa.SENTINEL_NAME]


def test_free_space_below_the_guard_writes_nothing(root, cache, monkeypatch):
    data = b"x" * 100
    path = _source(cache, "raw/a.csv", data)
    monkeypatch.setattr(sa.shutil, "disk_usage", lambda _p: Usage(10_000, 0, 1_050))

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [path], min_free_bytes=1_000)

    assert err.value.reason == "espace_insuffisant"
    assert _tree(root) == [sa.SENTINEL_NAME]


def test_free_space_guard_counts_only_the_bytes_to_write(root, cache, monkeypatch):
    data = b"x" * 100
    path = _source(cache, "raw/a.csv", data)
    _archive(root, cache, [path])
    # Nothing new to write: the guard compares free space minus zero bytes.
    monkeypatch.setattr(sa.shutil, "disk_usage", lambda _p: Usage(10_000, 0, 1_000))

    record = _archive(root, cache, [path], run_id=RUN_B, min_free_bytes=1_000)

    assert record["files"][0]["stored"] is False


def test_existing_object_of_another_size_is_never_replaced(root, cache):
    data = b"original\n"
    path = _source(cache, "raw/a.csv", data)
    obj = _object(root, data)
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"truncated")

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [path])

    assert err.value.reason == "objet_corrompu"
    assert obj.read_bytes() == b"truncated"
    assert not (root / "vacance" / "runs").exists()


def test_existing_object_of_same_size_but_other_content_is_never_reused(root, cache):
    data = b"original\n"
    path = _source(cache, "raw/a.csv", data)
    obj = _object(root, data)
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"altered!\n")
    assert len(b"altered!\n") == len(data)

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [path])

    assert err.value.reason == "objet_corrompu"
    assert obj.read_bytes() == b"altered!\n"
    assert not (root / "vacance" / "runs").exists()


def test_duplicate_relpath_is_refused(root, cache):
    path = _source(cache, "raw/a.csv", b"a\n")

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [path, path])

    assert err.value.reason == "chemin_duplique"
    assert _tree(root) == [sa.SENTINEL_NAME]


def test_empty_file_list_writes_nothing(root, cache):
    # A failed download returns []; an empty record would break verify for good.
    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [])

    assert err.value.reason == "aucun_fichier"
    assert _tree(root) == [sa.SENTINEL_NAME]


def test_existing_record_is_refused_and_left_untouched(root, cache):
    path = _source(cache, "raw/a.csv", b"a\n")
    _archive(root, cache, [path])
    record_path = root / "vacance" / "runs" / f"{RUN_A}.json"
    before = record_path.read_bytes()
    other = _source(cache, "raw/b.csv", b"b\n")

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [other])

    assert err.value.reason == "record_existant"
    assert record_path.read_bytes() == before
    assert not _object(root, b"b\n").exists()


# ------------------------------------------- directories (plan 02-22) --
# ArcepPipeline.download() returns one directory per departement, two gz
# files each; the archive and the journal describe the files inside.


def _departement_dir(cache: Path, dept: str) -> dict[str, bytes]:
    files = {
        f"raw/{dept}/base_imb_{dept}.csv.gz": f"base {dept}\n".encode(),
        f"raw/{dept}/actuel_{dept}.csv.gz": f"actuel {dept}\n".encode(),
    }
    for relpath, data in files.items():
        _source(cache, relpath, data)
    return files


def test_a_directory_is_archived_as_the_files_it_contains(root, cache):
    files = _departement_dir(cache, "44")

    record = _archive(root, cache, [cache / "raw" / "44"])

    assert [(f["relpath"], f["name"], f["sha256"]) for f in record["files"]] == [
        ("raw/44/actuel_44.csv.gz", "actuel_44.csv.gz", _sha(b"actuel 44\n")),
        ("raw/44/base_imb_44.csv.gz", "base_imb_44.csv.gz", _sha(b"base 44\n")),
    ]
    for relpath, data in files.items():
        assert _object(root, data).read_bytes() == data, relpath
    replay = sa.load_replay(root, "vacance", RUN_A)
    assert sorted(f["relpath"] for f in replay.files) == sorted(files)


def test_expand_keeps_a_file_and_sorts_each_directory_by_relpath(cache):
    _departement_dir(cache, "85")
    _departement_dir(cache, "44")
    single = _source(cache, "raw/lovac_communes.csv", b"x\n")
    nested = _source(cache, "raw/85/sub/extra.csv", b"y\n")

    expanded = sa.expand_source_paths(
        [single, cache / "raw" / "85", cache / "raw" / "44"], cache
    )

    assert [p.relative_to(cache).as_posix() for p in expanded] == [
        "raw/lovac_communes.csv",
        "raw/85/actuel_85.csv.gz",
        "raw/85/base_imb_85.csv.gz",
        "raw/85/sub/extra.csv",
        "raw/44/actuel_44.csv.gz",
        "raw/44/base_imb_44.csv.gz",
    ]
    assert nested in expanded


def test_an_empty_directory_is_refused_before_any_write(root, cache):
    empty = cache / "raw" / "44"
    (empty / "sub").mkdir(parents=True)

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [empty])

    assert err.value.reason == "repertoire_vide"
    assert _tree(root) == [sa.SENTINEL_NAME]


def test_a_path_neither_file_nor_directory_is_refused_before_any_write(root, cache):
    present = _source(cache, "raw/a.csv", b"a\n")
    missing = cache / "raw" / "absent.csv.gz"

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [present, missing])

    assert err.value.reason == "chemin_non_regulier"
    assert err.value.path == str(missing)
    assert _tree(root) == [sa.SENTINEL_NAME]


def _link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"os.symlink unavailable: {exc}")


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_a_symbolic_link_inside_a_directory_is_refused(root, cache, tmp_path, kind):
    """D-15 of 02-22: rglob skips a linked directory, a linked file got a basename."""
    _departement_dir(cache, "44")
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    elsewhere = outside / "sub" / "elsewhere.csv.gz"
    elsewhere.write_bytes(b"outside\n")
    target = elsewhere.parent if kind == "directory" else elsewhere
    link = cache / "raw" / "44" / f"linked-{kind}"
    _link(link, target)

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [cache / "raw" / "44"])

    assert err.value.reason == "chemin_non_regulier"
    assert err.value.path == str(link)
    assert _tree(root) == [sa.SENTINEL_NAME]


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_a_symbolic_link_in_the_list_is_refused(root, cache, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.csv").write_bytes(b"outside\n")
    target = outside if kind == "directory" else outside / "a.csv"
    link = cache / "raw" / f"linked-{kind}"
    _link(link, target)

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [link])

    assert err.value.reason == "chemin_non_regulier"
    assert _tree(root) == [sa.SENTINEL_NAME]


def test_replay_origin_requires_the_source_run(root, cache):
    path = _source(cache, "raw/a.csv", b"a\n")

    with pytest.raises(sa.SourceArchiveError) as err:
        _archive(root, cache, [path], origin="archive")

    assert err.value.reason == "origine_invalide"


# --------------------------------------------------------------- replay --


def _archived(root, cache, data=b"CODGEO_26\n44001\n"):
    path = _source(cache, "raw/lovac_communes.csv", data)
    _archive(root, cache, [path])
    return data


def _rewrite_record(root, mutate):
    record_path = root / "vacance" / "runs" / f"{RUN_A}.json"
    record = json.loads(record_path.read_text("utf-8"))
    mutate(record)
    os.chmod(record_path, 0o644)
    record_path.write_text(json.dumps(record), "utf-8")


def test_replay_materializes_every_file_at_its_relpath(root, cache, tmp_path):
    data = _archived(root, cache)
    target = tmp_path / "fresh-cache"

    replay = sa.load_replay(root, "vacance", RUN_A)
    paths = replay.materialize(target)

    assert paths == [target / "raw" / "lovac_communes.csv"]
    assert paths[0].read_bytes() == data
    assert os.access(paths[0], os.W_OK)
    assert replay.run_id == RUN_A
    record_bytes = (root / "vacance" / "runs" / f"{RUN_A}.json").read_bytes()
    assert replay.record_sha256 == hashlib.sha256(record_bytes).hexdigest()


def _linked(replay: sa.SourceReplay, target: Path) -> list[Path]:
    try:
        return replay.link(target)
    except OSError as exc:  # Windows without the symlink privilege
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"os.symlink unavailable: {exc}")
        raise


def _fingerprint(path: Path) -> tuple[int, int, bytes]:
    st = path.stat()
    return st.st_mode, st.st_mtime_ns, path.read_bytes()


def test_replay_links_every_object_at_its_relpath_without_copy(root, cache, tmp_path):
    data = _archived(root, cache)
    obj = _object(root, data)
    before = _fingerprint(obj)
    target = tmp_path / "fresh-cache"

    paths = _linked(sa.load_replay(root, "vacance", RUN_A), target)

    assert paths == [target / "raw" / "lovac_communes.csv"]
    assert paths[0].is_symlink()
    assert paths[0].resolve() == obj.resolve()
    assert paths[0].read_bytes() == data
    assert _fingerprint(obj) == before  # mode, mtime and bytes unchanged
    assert not os.access(obj, os.W_OK)


def test_replay_link_replaces_a_cache_leftover_and_never_its_object(
    root, cache, tmp_path
):
    data = _archived(root, cache)
    obj = _object(root, data)
    before = _fingerprint(obj)
    target = tmp_path / "fresh-cache"
    _source(target, "raw/lovac_communes.csv", b"leftover of a failed run\n")
    replay = sa.load_replay(root, "vacance", RUN_A)

    first = _linked(replay, target)
    second = _linked(replay, target)  # a stale link is a leftover too

    assert first == second
    assert second[0].read_bytes() == data
    assert _fingerprint(obj) == before


@pytest.mark.parametrize("relpath", ["../outside.csv", "/etc/passwd", "raw/../../x"])
def test_replay_link_refuses_a_climbing_relpath(root, cache, tmp_path, relpath):
    data = _archived(root, cache)
    replay = sa.load_replay(root, "vacance", RUN_A)
    record = json.loads(json.dumps(replay.record))
    record["files"][0]["relpath"] = relpath
    forged = sa.SourceReplay(root, "vacance", RUN_A, record, replay.record_sha256)
    target = tmp_path / "fresh-cache"

    with pytest.raises(sa.SourceArchiveError) as err:
        forged.link(target)

    assert err.value.reason == "chemin_interdit"
    assert not any(p.is_symlink() for p in tmp_path.rglob("*"))
    assert _object(root, data).read_bytes() == data


def test_replay_link_refuses_a_cache_directory_linked_outside(root, cache, tmp_path):
    _archived(root, cache)
    target = tmp_path / "fresh-cache"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _link(target / "raw", outside)

    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A).link(target)

    assert err.value.reason == "chemin_interdit"
    assert list(outside.iterdir()) == []


def test_cleanup_of_the_cache_removes_the_links_only(
    root, cache, tmp_path, monkeypatch
):
    from app.config import settings
    from app.etl.download import cleanup_download_dir, get_download_dir

    data = _archived(root, cache)
    obj = _object(root, data)
    before = _fingerprint(obj)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    paths = _linked(sa.load_replay(root, "vacance", RUN_A), get_download_dir("vacance"))
    assert paths[0].is_symlink()

    cleanup_download_dir("vacance")

    assert not get_download_dir("vacance").joinpath("raw").exists()
    assert _fingerprint(obj) == before
    assert sa.verify(root)["records"] == 1


def test_replay_links_are_archived_under_their_cache_path(root, cache, tmp_path):
    data = _archived(root, cache)
    target = tmp_path / "fresh-cache"
    links = _linked(sa.load_replay(root, "vacance", RUN_A), target)

    record = sa.archive_sources(
        root, "vacance", RUN_B, links, target, origin="archive", replayed_from=RUN_A
    )

    assert [(f["relpath"], f["stored"]) for f in record["files"]] == [
        ("raw/lovac_communes.csv", False)
    ]
    assert record["files"][0]["sha256"] == _sha(data)
    assert sa.verify(root) == {
        "records": 2,
        "objects": 1,
        "bytes": len(data),
        "orphans": 0,
    }


def test_replay_of_an_absent_record_is_refused(root):
    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A)
    assert err.value.reason == "record_absent"


def test_replay_of_a_record_naming_another_run_is_refused(root, cache):
    _archived(root, cache)
    _rewrite_record(root, lambda r: r.update(run_id=RUN_B))

    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A)
    assert err.value.reason == "record_incoherent"


@pytest.mark.parametrize("relpath", ["/etc/passwd", "../outside.csv", "raw/../../x"])
def test_replay_refuses_a_relpath_escaping_the_cache(root, cache, relpath):
    _archived(root, cache)
    _rewrite_record(root, lambda r: r["files"][0].update(relpath=relpath))

    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A)
    assert err.value.reason == "chemin_interdit"


def test_replay_with_an_altered_record_fingerprint_finds_no_object(root, cache):
    _archived(root, cache)

    def flip(record):
        digest = record["files"][0]["sha256"]
        record["files"][0]["sha256"] = ("1" if digest[0] != "1" else "2") + digest[1:]

    _rewrite_record(root, flip)
    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A)
    assert err.value.reason == "objet_absent"


def test_replay_with_a_truncated_object_is_refused(root, cache):
    data = _archived(root, cache)
    obj = _object(root, data)
    os.chmod(obj, 0o644)
    obj.write_bytes(data[:-1])

    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A)
    assert err.value.reason == "taille_differente"


def test_replay_with_one_flipped_byte_is_refused(root, cache):
    data = _archived(root, cache)
    obj = _object(root, data)
    os.chmod(obj, 0o644)
    obj.write_bytes(bytes([data[0] ^ 1]) + data[1:])

    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A)
    assert err.value.reason == "sha256_mismatch"
    assert str(obj) in str(err.value)


def test_replay_without_sentinel_is_refused(root, cache):
    _archived(root, cache)
    (root / sa.SENTINEL_NAME).unlink()

    with pytest.raises(sa.SourceArchiveError) as err:
        sa.load_replay(root, "vacance", RUN_A)
    assert err.value.reason == "sentinelle_absente"


# ------------------------------------------------------- verify / stats --


def test_verify_counts_records_objects_bytes_and_orphans(root, cache):
    data = _archived(root, cache)
    path = cache / "raw" / "lovac_communes.csv"
    _archive(root, cache, [path], run_id=RUN_B, origin="archive", replayed_from=RUN_A)

    assert sa.verify(root) == {
        "records": 2,
        "objects": 1,
        "bytes": len(data),
        "orphans": 0,
    }
    orphan = _object(root, b"unreferenced")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"unreferenced")
    assert sa.verify(root)["orphans"] == 1
    assert sa.verify(root, dataset="vacance", run_id=RUN_A)["records"] == 1


def test_verify_reports_an_altered_object(root, cache):
    data = _archived(root, cache)
    obj = _object(root, data)
    os.chmod(obj, 0o644)
    obj.write_bytes(bytes([data[0] ^ 1]) + data[1:])

    with pytest.raises(sa.SourceArchiveError) as err:
        sa.verify(root)
    assert err.value.reason == "sha256_mismatch"


def test_stats_reads_each_dataset(root, cache):
    data = _archived(root, cache)
    (root / "historique" / "dvf-avant-2021").mkdir(parents=True)

    assert sa.stats(root) == [
        {"dataset": "vacance", "records": 1, "objects": 1, "bytes": len(data)}
    ]


def test_cli_verify_ok_and_bad(root, cache, monkeypatch, capsys):
    data = _archived(root, cache)
    monkeypatch.setenv("SOURCE_ARCHIVE_DIR", str(root))

    assert sa.main(["verify"]) == 0
    assert capsys.readouterr().out.strip() == (
        f"SOURCE_ARCHIVE_OK records=1 objets=1 octets={len(data)} orphelins=0"
    )
    assert sa.main(["stats"]) == 0
    assert capsys.readouterr().out.strip() == (
        f"SOURCE_ARCHIVE_STATS dataset=vacance records=1 objets=1 octets={len(data)}"
    )
    obj = _object(root, data)
    os.chmod(obj, 0o644)
    obj.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    assert sa.main(["verify"]) == 1
    assert capsys.readouterr().out.startswith("SOURCE_ARCHIVE_BAD sha256_mismatch ")


def test_cli_without_archive_dir_is_bad(monkeypatch, capsys):
    monkeypatch.delenv("SOURCE_ARCHIVE_DIR", raising=False)

    assert sa.main(["verify"]) == 1
    assert capsys.readouterr().out.strip() == (
        "SOURCE_ARCHIVE_BAD source_archive_non_configuree -"
    )


# ------------------------------------------- declared period (C30, n° 91) --


def test_the_record_keeps_the_period_the_run_declared(root, cache):
    """A replay downloads nothing: the record is where the period survives."""
    path = _source(cache, "raw/lovac_communes.csv", b"CODGEO_26\n44001\n")

    record = _archive(root, cache, [path], source_period_label="2026_T2")

    assert record["source_period_label"] == "2026_T2"
    assert sa.load_replay(root, "vacance", RUN_A).record["source_period_label"] == (
        "2026_T2"
    )


def test_an_unknown_period_is_not_written_in_the_record(root, cache):
    path = _source(cache, "raw/lovac_communes.csv", b"CODGEO_26\n44001\n")

    record = _archive(root, cache, [path])

    assert "source_period_label" not in record


# ------------------------------------------------ retention purge (D-125) --


def _run_id(n: int) -> str:
    return f"{n}-202609{10 + n:02d}T000000Z"


def _editions(root, cache, spec):
    """Archive one run per (number, label, {relpath: bytes}) in that order."""
    for n, label, files in spec:
        paths = [_source(cache, rel, data) for rel, data in files.items()]
        _archive(root, cache, paths, run_id=_run_id(n), source_period_label=label)


SHARED = b"unchanged from one quarter to the next\n"
FOUR_QUARTERS = [
    (1, "2026_T1", {"raw/a.csv": b"T1\n", "raw/shared.csv": SHARED}),
    (2, "2026_T2", {"raw/a.csv": b"T2\n"}),
    (3, "2026_T3", {"raw/a.csv": b"T3\n", "raw/shared.csv": SHARED}),
    (4, "2026_T4", {"raw/a.csv": b"T4\n"}),
]


def test_purge_keeps_served_previous_and_newer_and_shared_objects(root, cache):
    _editions(root, cache, FOUR_QUARTERS)
    before = _tree(root)

    plan = sa.plan_purge(root, "vacance", _run_id(3))

    assert [(e["edition"], e["kept"], e["served"]) for e in plan["editions"]] == [
        ("periode:2026_T1", False, False),
        ("periode:2026_T2", True, False),
        ("periode:2026_T3", True, True),
        ("periode:2026_T4", True, False),  # never served yet: not purged
    ]
    assert plan["records"] == [_run_id(1)]
    # The shared object is still cited by T3: only T1's own file goes.
    assert plan["objects"] == [{"sha256": _sha(b"T1\n"), "bytes": 3}]
    assert plan["bytes"] == 3
    assert _tree(root) == before  # a dry run writes nothing


def test_purge_groups_records_of_one_file_set_and_replays_with_their_source(
    root, cache
):
    _editions(
        root,
        cache,
        [
            (1, None, {"raw/a.csv": b"old\n"}),
            (2, None, {"raw/a.csv": b"mid\n"}),
            (3, None, {"raw/a.csv": b"mid\n"}),  # same set: same edition as 2
            (4, None, {"raw/a.csv": b"new\n"}),
        ],
    )
    replayed = _source(cache, "raw/a.csv", b"old\n")
    _archive(
        root,
        cache,
        [replayed],
        run_id=_run_id(5),
        origin="archive",
        replayed_from=_run_id(1),
        source_period_label="2026_T9",  # a replay keeps its source's edition
    )

    plan = sa.plan_purge(root, "vacance", _run_id(5))

    assert [e["records"] for e in plan["editions"]] == [
        [_run_id(1), _run_id(5)],
        [_run_id(2), _run_id(3)],
        [_run_id(4)],
    ]
    assert plan["editions"][0]["served"] is True
    assert plan["records"] == []  # served is the oldest: nothing before it


def test_an_unlabelled_record_and_a_labelled_run_of_its_files_are_one_edition(
    root, cache
):
    """Archive of the work base (cadastre 7, 35, 39): records before C30 carry no label.

    Run 3 relabels the files of run 2; counted as two editions, run 1 (an
    older, real edition) would be purged although only one edition follows.
    """
    _editions(
        root,
        cache,
        [
            (1, None, {"raw/a.csv": b"2025-12\n"}),
            (2, None, {"raw/a.csv": b"2026-06\n"}),
            (3, "2026-06-01", {"raw/a.csv": b"2026-06\n"}),
        ],
    )

    plan = sa.plan_purge(root, "vacance", _run_id(3))

    assert [(e["records"], e["kept"]) for e in plan["editions"]] == [
        ([_run_id(1)], True),
        ([_run_id(2), _run_id(3)], True),
    ]
    assert plan["editions"][1]["edition"] == "periode:2026-06-01"
    assert plan["records"] == []


@pytest.mark.parametrize("served", [None, "9-20260930T000000Z"])
def test_purge_without_a_known_served_edition_is_refused(root, cache, served):
    _editions(root, cache, FOUR_QUARTERS)

    with pytest.raises(sa.PurgeRefusedError) as err:
        sa.plan_purge(root, "vacance", served)

    assert err.value.reason == "edition_servie_inconnue"


def test_purge_keeps_at_least_two_editions(root, cache):
    _editions(root, cache, FOUR_QUARTERS)

    with pytest.raises(sa.PurgeRefusedError) as err:
        sa.plan_purge(root, "vacance", _run_id(4), keep_editions=1)

    assert err.value.reason == "keep_editions_min_2"


@pytest.fixture
def purge_cli(root, monkeypatch):
    monkeypatch.setenv("SOURCE_ARCHIVE_DIR", str(root))
    monkeypatch.setenv("WORKBASE_SHA", "abc1234")
    served = {"run_id": _run_id(3)}
    monkeypatch.setattr(sa, "_served_run_id_from_db", lambda _ds: served["run_id"])
    return served


@pytest.mark.parametrize(
    "extra", [["--apply"], ["--apply", "--confirm", "dvf"], ["--confirm", "vacance"]]
)
def test_purge_cli_refuses_apply_without_its_confirmation(
    root, cache, purge_cli, capsys, extra
):
    _editions(root, cache, FOUR_QUARTERS)
    before = _tree(root)

    code = sa.main(["purge", "--dataset", "vacance", *extra])

    out = capsys.readouterr().out
    if "--apply" in extra:
        assert code == 2
        assert out.strip() == (
            "ARCHIVE_PURGE REFUSED dataset=vacance reason=confirmation_absente"
        )
    else:  # --confirm alone stays a dry run
        assert code == 0
        assert out.startswith("ARCHIVE_PURGE dry-run dataset=vacance")
    assert _tree(root) == before


def test_purge_cli_refuses_an_unknown_served_edition(root, cache, purge_cli, capsys):
    _editions(root, cache, FOUR_QUARTERS)
    purge_cli["run_id"] = None

    assert sa.main(["purge", "--dataset", "vacance"]) == 2
    assert capsys.readouterr().out.strip() == (
        "ARCHIVE_PURGE REFUSED dataset=vacance reason=edition_servie_inconnue"
    )


def test_purge_cli_dry_run_prints_the_plan(root, cache, purge_cli, capsys):
    _editions(root, cache, FOUR_QUARTERS)

    assert sa.main(["purge", "--dataset", "vacance", "--json"]) == 0

    out = capsys.readouterr().out
    document, line = out.rsplit("\n", 2)[0], out.strip().splitlines()[-1]
    assert line == (
        "ARCHIVE_PURGE dry-run dataset=vacance editions_kept=3 records=1 "
        "objects=1 bytes=3"
    )
    plan = json.loads(document)
    assert (plan["mode"], plan["deployment_sha"]) == ("dry-run", "abc1234")
    assert not (root / sa.JOURNAL_NAME).exists()


def test_purge_writes_its_journal_before_the_first_deletion(
    root, cache, purge_cli, monkeypatch
):
    _editions(root, cache, FOUR_QUARTERS)
    journal = root / sa.JOURNAL_NAME
    seen: list[dict] = []
    real_remove = sa._remove

    def remove(path):
        if not seen:
            lines = journal.read_text("utf-8").splitlines()
            seen.append(json.loads(lines[-1]))
        real_remove(path)

    monkeypatch.setattr(sa, "_remove", remove)

    assert (
        sa.main(["purge", "--dataset", "vacance", "--apply", "--confirm", "vacance"])
        == 0
    )

    (line,) = seen
    assert line["dataset"] == "vacance"
    assert line["records"] == [_run_id(1)]
    assert line["objects"] == [{"bytes": 3, "sha256": _sha(b"T1\n")}]
    assert line["editions_kept"] == [
        "periode:2026_T2",
        "periode:2026_T3",
        "periode:2026_T4",
    ]
    assert line["deployment_sha"] == "abc1234"
    assert {"utc", "served_run_id", "bytes", "keep_editions"} <= set(line)


def test_purge_apply_leaves_a_verified_archive(root, cache, purge_cli, capsys):
    _editions(root, cache, FOUR_QUARTERS)

    code = sa.main(["purge", "--dataset", "vacance", "--apply", "--confirm", "vacance"])

    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert out[0] == (
        "ARCHIVE_PURGE applied dataset=vacance editions_kept=3 records=1 "
        "objects=1 bytes=3"
    )
    kept_bytes = len(SHARED) + 9
    assert out[1] == (
        f"SOURCE_ARCHIVE_OK records=3 objets=4 octets={kept_bytes} orphelins=0"
    )
    assert not (root / "vacance" / "runs" / f"{_run_id(1)}.json").exists()
    assert not _object(root, b"T1\n").exists()
    assert _object(root, SHARED).read_bytes() == SHARED  # still cited by T3
    assert sa.verify(root)["orphans"] == 0
    # Applied once: the next computation removes nothing more.
    assert sa.plan_purge(root, "vacance", _run_id(3))["records"] == []
    assert len((root / sa.JOURNAL_NAME).read_text("utf-8").splitlines()) == 1
