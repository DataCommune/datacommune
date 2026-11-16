"""Immutable archive of the source files of every import (plan 02-14, OPS-03).

The work base is not backed up: it is rebuilt by the ETL. What cannot be
rebuilt are the vintages a producer replaces, so every import keeps its
source files here (D-29 and D-30 amended on 2026-09-23). Layout under the
archive root (``SOURCE_ARCHIVE_DIR``; on the work base, the USB disk of the
hypervisor bind-mounted in the work base container, never the SSD)::

    .datacommune-source-archive          sentinel, "datacommune-source-archive v1"
    <dataset>/objects/<aa>/<sha256>      one file per content, mode 0444
    <dataset>/runs/<run_id>.json         one record per run, mode 0444

Objects and records are never rewritten or moved by this module. They are
deleted by one command only, ``purge --apply --confirm <dataset>``: retention
D-125 (served edition + previous one), Ianis's gesture, journaled in
``purge-journal.jsonl`` before the first deletion; without ``--apply`` it is a
dry run. The sentinel is required before any read or write: an unmounted
disk leaves an empty directory on the SSD, and nothing may be written there.

``run_id`` is the composition of the import receipt (plan 02-10):
``<etl_runs.id>-<started_at UTC, YYYYMMDDTHHMMSSZ>``; for an import script
without etl_runs row, the bare UTC stamp of its launch (plan 02-11).

Command line (root from ``SOURCE_ARCHIVE_DIR``)::

    python -m app.etl.source_archive verify [--dataset D] [--run-id R]
    python -m app.etl.source_archive stats [--json]
    python -m app.etl.source_archive purge --dataset D [--keep-editions 2]
        [--apply --confirm D] [--json]

stdlib only at module level; ``purge`` imports psycopg to read ``etl_runs``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SENTINEL_NAME = ".datacommune-source-archive"
SENTINEL_CONTENT = "datacommune-source-archive v1"
RECORD_VERSION = 1
ORIGINS = ("telechargement", "archive")

_DATASET_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# ``<etl_runs.id>-<stamp>`` for a pipeline run; the bare stamp for an import
# script, which has no etl_runs row (plan 02-11, workbase-import.sh --script).
_RUN_ID_RE = re.compile(r"^(?:([1-9][0-9]*)-)?([0-9]{8}T[0-9]{6}Z)$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1 << 20


class SourceArchiveError(RuntimeError):
    """A refusal of the archive: a closed ``reason`` and the path concerned."""

    def __init__(self, reason: str, path: str | os.PathLike[str] = "-") -> None:
        self.reason = reason
        self.path = str(path)
        super().__init__(f"{reason} {self.path}")


def compose_run_id(etl_run_id: int, started_at: datetime) -> str:
    """Same value as ``scripts.export_import_manifest.compose_run_id``."""
    stamp = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{etl_run_id}-{stamp}"


# ------------------------------------------------------------------ helpers --


def _digest(path: Path) -> tuple[int, str]:
    """Size and SHA-256 read from the file itself."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _check_names(dataset: str, run_id: str) -> None:
    if not _DATASET_RE.fullmatch(dataset or ""):
        raise SourceArchiveError("dataset_invalide", dataset or "-")
    if not _RUN_ID_RE.fullmatch(run_id or ""):
        raise SourceArchiveError("run_id_invalide", run_id or "-")


def _check_sentinel(root: Path) -> None:
    sentinel = root / SENTINEL_NAME
    try:
        content = sentinel.read_text(encoding="utf-8")
    except OSError:
        raise SourceArchiveError("sentinelle_absente", sentinel) from None
    if content.strip() != SENTINEL_CONTENT:
        raise SourceArchiveError("sentinelle_absente", sentinel)


def _check_relpath(relpath: Any) -> str:
    """A relative POSIX path that stays inside the cache directory."""
    if not isinstance(relpath, str) or not relpath:
        raise SourceArchiveError("chemin_interdit", relpath or "-")
    parts = relpath.split("/")
    if (
        PurePosixPath(relpath).is_absolute()
        or "\\" in relpath
        or ":" in relpath
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise SourceArchiveError("chemin_interdit", relpath)
    return relpath


def _relpath(path: Path, download_dir: Path) -> str:
    """Path relative to the cache; a file outside it keeps its basename.

    Files outside the cache: frozen sources (``--source-manifest``) are
    copied under ``ETL_TEMP_DIR/frozen_sources``, not in the dataset cache.
    """
    # The last component is not resolved: a replay link (``SourceReplay.link``)
    # keeps its own cache path, not the path of the archived object.
    try:
        relative = (path.parent.resolve() / path.name).relative_to(
            download_dir.resolve()
        )
    except ValueError:
        return path.name
    return relative.as_posix()


def _is_replay_link(path: Path, linked_objects: Path | None) -> bool:
    """A link of ``SourceReplay.link``: to a regular file under *linked_objects*."""
    return (
        linked_objects is not None
        and path.is_file()
        and path.resolve().is_relative_to(Path(linked_objects).resolve())
    )


def expand_source_paths(
    paths: list[Path],
    download_dir: str | os.PathLike[str],
    linked_objects: str | os.PathLike[str] | None = None,
) -> list[Path]:
    """Replace each directory by the regular files under it; keep a file as is.

    A download may return directories: ``ArcepPipeline`` returns one per
    departement, two gz files each (plan 02-22). The files of a directory are
    sorted by their path relative to the cache, the ``relpath`` of the record.
    A directory without any file and a path that is neither a file nor a
    directory are refused: nothing of the source may be skipped in silence.
    A symbolic link is refused too: rglob skips a linked directory, and a
    linked file would be recorded outside the cache under its basename. The
    one exception is a listed link to a regular file under *linked_objects*
    (``<archive>/<dataset>/objects``): a replay input of ``SourceReplay.link``,
    read through and recorded under its own cache path (plan 04.1-05).
    """
    download_dir = Path(download_dir)
    linked = Path(linked_objects) if linked_objects is not None else None
    expanded: list[Path] = []
    for path in map(Path, paths):
        if path.is_symlink():
            if not _is_replay_link(path, linked):
                raise SourceArchiveError("chemin_non_regulier", path)
            expanded.append(path)
            continue
        if path.is_file():
            expanded.append(path)
            continue
        if not path.is_dir():
            raise SourceArchiveError("chemin_non_regulier", path)
        expanded.extend(_files_under(path, download_dir))
    return expanded


def _files_under(directory: Path, download_dir: Path) -> list[Path]:
    """Regular files under *directory*, by relpath; links refused, empty refused."""
    found: list[Path] = []
    for child in directory.rglob("*"):
        if child.is_symlink():
            raise SourceArchiveError("chemin_non_regulier", child)
        if child.is_file():
            found.append(child)
        elif not child.is_dir():
            raise SourceArchiveError("chemin_non_regulier", child)
    if not found:
        raise SourceArchiveError("repertoire_vide", directory)
    return sorted(found, key=lambda p: _relpath(p, download_dir))


def _object_rel(sha256: str) -> str:
    return f"objects/{sha256[:2]}/{sha256}"


def _fsync_dir(directory: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):  # Windows: no directory descriptors
        return
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _drop_link(tmp: Path, kept: Path | None) -> None:
    """Remove the temporary name of a file published by ``os.link``.

    Windows refuses to unlink a read-only file (local test runs); the mode is
    shared by both names, so it is lifted for the unlink and restored on the
    name that stays.
    """
    try:
        tmp.unlink()
    except FileNotFoundError:
        return
    except PermissionError:
        os.chmod(tmp, 0o644)
        tmp.unlink()
        if kept is not None and kept.exists():
            os.chmod(kept, 0o444)


def _existing_object(obj: Path, size: int, sha256: str) -> None:
    """Re-read an existing object before any reuse; never reuse it in silence."""
    if obj.stat().st_size != size or _digest(obj) != (size, sha256):
        raise SourceArchiveError("objet_corrompu", obj)


def _store_object(source: Path, obj: Path, size: int, sha256: str) -> None:
    """Copy, fsync, re-read, then publish by ``os.link`` (never a replacement)."""
    obj.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=obj.parent)
    tmp = Path(name)
    published = False
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, out, _CHUNK)
            out.flush()
            os.fsync(out.fileno())
        if _digest(tmp) != (size, sha256):
            raise SourceArchiveError("copie_divergente", source)
        os.chmod(tmp, 0o444)
        try:
            os.link(tmp, obj)
            published = True
        except FileExistsError:
            # Written meanwhile by another run: same rule as a prior object.
            _existing_object(obj, size, sha256)
        _fsync_dir(obj.parent)
    finally:
        _drop_link(tmp, obj if published else None)


def _write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, sort_keys=True, indent=2) + "\n"
    fd, name = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=path.parent)
    tmp = Path(name)
    published = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o444)
        try:
            os.link(tmp, path)
            published = True
        except FileExistsError:
            raise SourceArchiveError("record_existant", path) from None
        _fsync_dir(path.parent)
    finally:
        _drop_link(tmp, path if published else None)


# ------------------------------------------------------------------- write --


def archive_sources(  # noqa: C901
    root: str | os.PathLike[str],
    dataset: str,
    run_id: str,
    files: list[Path],
    download_dir: str | os.PathLike[str],
    origin: str,
    replayed_from: str | None = None,
    deployment_sha: str | None = None,
    min_free_bytes: int = 0,
    source_period_label: str | None = None,
) -> dict[str, Any]:
    """Archive the source files of one run; return the record written.

    *source_period_label*: the period the run declared for these files (C30,
    e.g. the quarter of connectivite read on the final download URL), written
    only when known: a replay downloads nothing and reads it back here.

    Every refusal is raised before the first write, except a copy that
    diverges from its source while being written (``copie_divergente``). The
    record is written last: an interrupted run leaves at most unreferenced
    objects, which ``verify`` counts as orphans.
    """
    root = Path(root)
    download_dir = Path(download_dir)
    _check_names(dataset, run_id)
    if origin not in ORIGINS or (origin == "archive") != bool(replayed_from):
        raise SourceArchiveError("origine_invalide", origin or "-")
    if replayed_from is not None and not _RUN_ID_RE.fullmatch(replayed_from):
        raise SourceArchiveError("run_id_invalide", replayed_from)
    _check_sentinel(root)

    base = root / dataset
    record_path = base / "runs" / f"{run_id}.json"
    if record_path.exists():
        raise SourceArchiveError("record_existant", record_path)
    if not files:
        # An empty record is immutable and fails verify for good.
        raise SourceArchiveError("aucun_fichier", dataset)
    files = expand_source_paths(files, download_dir, base / "objects")

    entries: list[dict[str, Any]] = []
    sources: dict[str, Path] = {}
    seen: set[str] = set()
    for path in files:
        path = Path(path)
        relpath = _check_relpath(_relpath(path, download_dir))
        if relpath in seen:
            raise SourceArchiveError("chemin_duplique", relpath)
        seen.add(relpath)
        size, sha256 = _digest(path)
        obj = base / _object_rel(sha256)
        if obj.exists():
            _existing_object(obj, size, sha256)
            stored = False
        else:
            # Same content twice in one run: stored once, by the first file.
            stored = sha256 not in sources
            sources.setdefault(sha256, path)
        entries.append(
            {
                "relpath": relpath,
                "name": path.name,
                "bytes": size,
                "sha256": sha256,
                "object": _object_rel(sha256),
                "stored": stored,
            }
        )

    to_write = sum(e["bytes"] for e in entries if e["stored"])
    free = shutil.disk_usage(root).free
    if free - to_write < min_free_bytes:
        raise SourceArchiveError("espace_insuffisant", root)

    for entry in entries:
        if entry["stored"]:
            _store_object(
                sources[entry["sha256"]],
                base / entry["object"],
                entry["bytes"],
                entry["sha256"],
            )

    match = _RUN_ID_RE.fullmatch(run_id)
    assert match is not None  # checked by _check_names
    stamp = datetime.strptime(match.group(2), "%Y%m%dT%H%M%SZ")
    record = {
        "version": RECORD_VERSION,
        "dataset": dataset,
        "run_id": run_id,
        "etl_run_id": int(match.group(1)) if match.group(1) else None,
        "started_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origin": origin,
        "replayed_from": replayed_from,
        "deployment_sha": deployment_sha,
        "archived_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": entries,
    }
    if source_period_label is not None:
        record["source_period_label"] = str(source_period_label)
    _write_record(record_path, record)
    return record


# ------------------------------------------------------------------ replay --


@dataclass(frozen=True)
class SourceReplay:
    """A verified record: every object present, of the recorded size and SHA-256."""

    root: Path
    dataset: str
    run_id: str
    record: dict[str, Any]
    record_sha256: str

    @property
    def files(self) -> list[dict[str, Any]]:
        return list(self.record["files"])

    def materialize(self, download_dir: str | os.PathLike[str]) -> list[Path]:
        """Copy each object to its relpath (writable), re-verified; record order."""
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        base = download_dir.resolve()
        paths: list[Path] = []
        for entry in self.files:
            target = download_dir / _check_relpath(entry["relpath"])
            if not target.resolve().is_relative_to(base):
                raise SourceArchiveError("chemin_interdit", entry["relpath"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()  # cache leftover of a failed run
            obj = self.root / self.dataset / _object_rel(entry["sha256"])
            with obj.open("rb") as incoming, target.open("xb") as out:
                shutil.copyfileobj(incoming, out, _CHUNK)
            if _digest(target) != (entry["bytes"], entry["sha256"]):
                raise SourceArchiveError("sha256_mismatch", target)
            os.chmod(target, 0o644)
            paths.append(target)
        return paths

    def link(self, download_dir: str | os.PathLike[str]) -> list[Path]:
        """Link each verified object at its relpath, without copy; record order.

        Replay of large sources (BD TOPO, plan 04.1-05, Astra review n° 7):
        ``load_replay`` has already read every object against its SHA-256, so
        the object is linked, not copied. Nothing is written to it: no chmod,
        no open for writing, never through the link. A leftover at the same
        path (cache of a failed run) is removed first, as in ``materialize``;
        removing a link never touches its target.
        """
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        base = download_dir.resolve()
        objects = (self.root / self.dataset / "objects").resolve()
        paths: list[Path] = []
        for entry in self.files:
            relpath = _check_relpath(entry["relpath"])
            target = download_dir / relpath
            if not target.parent.resolve().is_relative_to(base):
                raise SourceArchiveError("chemin_interdit", relpath)
            obj = (self.root / self.dataset / _object_rel(entry["sha256"])).resolve()
            if not obj.is_relative_to(objects) or not obj.is_file():
                raise SourceArchiveError("objet_absent", obj)
            if obj.stat().st_size != entry["bytes"]:
                raise SourceArchiveError("taille_differente", obj)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()  # cache leftover of a failed run
            target.symlink_to(obj)
            paths.append(target)
        return paths


def _verified_entry(base: Path, entry: Any) -> None:
    if not isinstance(entry, dict):
        raise SourceArchiveError("record_incoherent", base)
    _check_relpath(entry.get("relpath"))
    sha256, size = entry.get("sha256"), entry.get("bytes")
    if (
        not isinstance(sha256, str)
        or not _SHA_RE.fullmatch(sha256)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        raise SourceArchiveError("record_incoherent", base)
    # The object path derives from the recorded fingerprint: an altered
    # fingerprint names an object that does not exist.
    obj = base / _object_rel(sha256)
    if not obj.is_file():
        raise SourceArchiveError("objet_absent", obj)
    if obj.stat().st_size != size:
        raise SourceArchiveError("taille_differente", obj)
    if _digest(obj)[1] != sha256:
        raise SourceArchiveError("sha256_mismatch", obj)
    if entry.get("object") != _object_rel(sha256):
        raise SourceArchiveError("record_incoherent", base)


def load_replay(
    root: str | os.PathLike[str], dataset: str, run_id: str
) -> SourceReplay:
    """Read and verify a record and ALL its objects before returning."""
    root = Path(root)
    _check_names(dataset, run_id)
    _check_sentinel(root)
    record_path = root / dataset / "runs" / f"{run_id}.json"
    if not record_path.is_file():
        raise SourceArchiveError("record_absent", record_path)
    raw = record_path.read_bytes()
    try:
        record = json.loads(raw)
    except ValueError:
        raise SourceArchiveError("record_incoherent", record_path) from None
    files = record.get("files") if isinstance(record, dict) else None
    if (
        not isinstance(record, dict)
        or record.get("version") != RECORD_VERSION
        or record.get("dataset") != dataset
        or record.get("run_id") != run_id
        or not isinstance(files, list)
        or not files
    ):
        raise SourceArchiveError("record_incoherent", record_path)
    relpaths = set()
    for entry in files:
        _verified_entry(root / dataset, entry)
        relpaths.add(entry["relpath"])
    if len(relpaths) != len(files):
        raise SourceArchiveError("record_incoherent", record_path)
    return SourceReplay(
        root=root,
        dataset=dataset,
        run_id=run_id,
        record=record,
        record_sha256=hashlib.sha256(raw).hexdigest(),
    )


# ------------------------------------------------------------ verify/stats --


def _datasets(root: Path) -> list[str]:
    return sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and ((d / "runs").is_dir() or (d / "objects").is_dir())
    )


def verify(
    root: str | os.PathLike[str], dataset: str | None = None, run_id: str | None = None
) -> dict[str, int]:
    """Check every record; count distinct objects, their bytes and orphans.

    Orphans (objects no record references, leftover temporaries) are counted,
    not failed: they are what an interrupted write leaves behind. They are
    only counted when a whole dataset is verified.
    """
    root = Path(root)
    _check_sentinel(root)
    if run_id is not None and dataset is None:
        raise SourceArchiveError("dataset_requis", run_id)
    records = 0
    orphans = 0
    referenced: dict[Path, int] = {}
    for ds in [dataset] if dataset else _datasets(root):
        runs_dir = root / ds / "runs"
        if run_id is not None:
            names = [run_id]
        else:
            names = sorted(p.name[: -len(".json")] for p in runs_dir.glob("*.json"))
        for name in names:
            replay = load_replay(root, ds, name)
            records += 1
            for entry in replay.files:
                referenced[root / ds / entry["object"]] = entry["bytes"]
        if run_id is None:
            orphans += len(list(runs_dir.glob("*.part")))
            for obj in (root / ds / "objects").glob("*/*"):
                if obj not in referenced:
                    orphans += 1
    return {
        "records": records,
        "objects": len(referenced),
        "bytes": sum(referenced.values()),
        "orphans": orphans,
    }


def stats(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Records, objects and object bytes per dataset (no verification)."""
    root = Path(root)
    _check_sentinel(root)
    result = []
    for ds in _datasets(root):
        objects = [
            p
            for p in (root / ds / "objects").glob("*/*")
            if p.is_file() and not p.name.startswith(".")
        ]
        result.append(
            {
                "dataset": ds,
                "records": len(list((root / ds / "runs").glob("*.json"))),
                "objects": len(objects),
                "bytes": sum(p.stat().st_size for p in objects),
            }
        )
    return result


# ------------------------------------------------------------------- purge --

JOURNAL_NAME = "purge-journal.jsonl"
MIN_KEEP_EDITIONS = 2


class PurgeRefusedError(Exception):
    """A purge that cannot be computed or applied: a closed ``reason``."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _read_records(root: Path, dataset: str) -> dict[str, dict[str, Any]]:
    """Every record of *dataset*, structure checked (objects are not re-read)."""
    records: dict[str, dict[str, Any]] = {}
    for path in sorted((root / dataset / "runs").glob("*.json")):
        run_id = path.name[: -len(".json")]
        try:
            record = json.loads(path.read_bytes())
        except ValueError:
            raise SourceArchiveError("record_incoherent", path) from None
        files = record.get("files") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or record.get("version") != RECORD_VERSION
            or record.get("dataset") != dataset
            or record.get("run_id") != run_id
            or not isinstance(record.get("archived_at"), str)
            or not isinstance(record.get("started_at"), str)
            or not isinstance(files, list)
            or not files
        ):
            raise SourceArchiveError("record_incoherent", path)
        for entry in files:
            sha256, size = entry.get("sha256"), entry.get("bytes")
            if (
                not isinstance(sha256, str)
                or not _SHA_RE.fullmatch(sha256)
                or not isinstance(size, int)
                or isinstance(size, bool)
            ):
                raise SourceArchiveError("record_incoherent", path)
        records[run_id] = record
    return records


def _file_set(record: dict[str, Any]) -> str:
    shas = sorted({entry["sha256"] for entry in record["files"]})
    digest = hashlib.sha256("\n".join(shas).encode()).hexdigest()
    return f"fichiers:{len(shas)}:{digest[:16]}"


def _editions(records: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Edition of every record (D-125): run id -> edition name.

    Records are one edition when they share a ``source_period_label``, the
    same set of file SHA-256, or a replay link (``replayed_from``): a record
    archived before the label existed (C30) and a later labelled run of the
    same files are one edition, not two. The name is the label(s) of the
    edition, else the file set of its first record.
    """
    parent = {run_id: run_id for run_id in records}

    def find(run_id: str) -> str:
        while parent[run_id] != run_id:
            parent[run_id] = parent[parent[run_id]]
            run_id = parent[run_id]
        return run_id

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    first_by_key: dict[str, str] = {}
    for run_id, record in records.items():
        keys = [_file_set(record)]
        if record.get("source_period_label"):
            keys.append(f"periode:{record['source_period_label']}")
        for key in keys:
            union(run_id, first_by_key.setdefault(key, run_id))
        if record.get("replayed_from") in records:
            union(run_id, record["replayed_from"])
    groups: dict[str, list[str]] = {}
    for run_id in records:
        groups.setdefault(find(run_id), []).append(run_id)
    names: dict[str, str] = {}
    for members in groups.values():
        labels = sorted(
            {
                str(records[r]["source_period_label"])
                for r in members
                if records[r].get("source_period_label")
            }
        )
        first = min(
            members, key=lambda r: (records[r]["archived_at"], records[r]["started_at"])
        )
        name = "periode:" + "|".join(labels) if labels else _file_set(records[first])
        names.update(dict.fromkeys(members, name))
    return names


def plan_purge(
    root: str | os.PathLike[str],
    dataset: str,
    served_run_id: str | None,
    keep_editions: int = MIN_KEEP_EDITIONS,
) -> dict[str, Any]:
    """Records and objects a retention purge would remove; writes nothing.

    Kept: the served edition, the ``keep_editions - 1`` editions before it
    (ordered by their first ``archived_at``) and every edition after it,
    never served yet (D-125 purges the oldest only). An object is removable
    only when no kept record cites it.
    """
    root = Path(root)
    if not _DATASET_RE.fullmatch(dataset or ""):
        raise SourceArchiveError("dataset_invalide", dataset or "-")
    _check_sentinel(root)
    if keep_editions < MIN_KEEP_EDITIONS:
        raise PurgeRefusedError("keep_editions_min_2")
    records = _read_records(root, dataset)
    if not served_run_id or served_run_id not in records:
        raise PurgeRefusedError("edition_servie_inconnue")
    edition_of = _editions(records)
    editions: dict[str, list[str]] = {}
    for run_id in records:
        editions.setdefault(edition_of[run_id], []).append(run_id)

    def first_seen(key: str) -> tuple[str, str]:
        return min(
            (records[r]["archived_at"], records[r]["started_at"]) for r in editions[key]
        )

    ordered = sorted(editions, key=lambda key: (first_seen(key), key))
    served = edition_of[served_run_id]
    position = ordered.index(served)
    kept = set(ordered[max(0, position - (keep_editions - 1)) :])
    purged_runs = sorted(r for key in ordered if key not in kept for r in editions[key])
    cited = {
        entry["sha256"]
        for run_id, record in records.items()
        if run_id not in purged_runs
        for entry in record["files"]
    }
    objects = {
        entry["sha256"]: entry["bytes"]
        for run_id in purged_runs
        for entry in records[run_id]["files"]
        if entry["sha256"] not in cited
    }
    return {
        "dataset": dataset,
        "served_run_id": served_run_id,
        "served_edition": served,
        "keep_editions": keep_editions,
        "editions": [
            {
                "edition": key,
                "first_archived_at": first_seen(key)[0],
                "records": sorted(editions[key]),
                "kept": key in kept,
                "served": key == served,
            }
            for key in ordered
        ],
        "editions_kept": len(kept),
        "records": purged_runs,
        "objects": [
            {"sha256": sha, "bytes": size} for sha, size in sorted(objects.items())
        ],
        "bytes": sum(objects.values()),
    }


def _remove(path: Path) -> None:
    """Delete one archive file (read-only on Windows test runs: mode lifted)."""
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, 0o644)
        path.unlink()


def apply_purge(
    root: str | os.PathLike[str],
    plan: dict[str, Any],
    deployment_sha: str | None,
) -> dict[str, int]:
    """Journal the purge, then delete its records and objects, then verify.

    The journal line is appended and fsynced before the first deletion;
    records go before objects, so an interruption leaves at most orphan
    objects, which ``verify`` counts without failing.
    """
    root = Path(root)
    dataset = plan["dataset"]
    line = {
        "utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": dataset,
        "served_run_id": plan["served_run_id"],
        "keep_editions": plan["keep_editions"],
        "editions_kept": [e["edition"] for e in plan["editions"] if e["kept"]],
        "records": plan["records"],
        "objects": plan["objects"],
        "bytes": plan["bytes"],
        "deployment_sha": deployment_sha,
    }
    journal = root / JOURNAL_NAME
    with journal.open("a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(line, sort_keys=True) + "\n")
        out.flush()
        os.fsync(out.fileno())
    _fsync_dir(root)
    for run_id in plan["records"]:
        _remove(root / dataset / "runs" / f"{run_id}.json")
    for entry in plan["objects"]:
        _remove(root / dataset / _object_rel(entry["sha256"]))
    return verify(root, dataset)


def _served_run_id_from_db(dataset: str) -> str | None:
    """Run id of the last successful run of *dataset* (``etl_runs``).

    psycopg is imported here only: it is in the API image of every deployed
    SHA, and the module can be sent on stdin to the tool image of the served
    SHA (plan 04.1-05). ``DATABASE_URL`` is given by ``wb-tool.sh``.
    """
    import psycopg

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return None
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, started_at FROM etl_runs "
            "WHERE dataset_name = %s AND status = 'success' "
            "ORDER BY id DESC LIMIT 1",
            (dataset,),
        ).fetchone()
    return compose_run_id(row[0], row[1]) if row else None


def _deployment_sha() -> str | None:
    return os.environ.get("WORKBASE_SHA") or os.environ.get("DEPLOYMENT_SHA") or None


def _purge_command(root: str, args: argparse.Namespace) -> int:
    dataset = args.dataset
    if args.apply and args.confirm != dataset:
        print(f"ARCHIVE_PURGE REFUSED dataset={dataset} reason=confirmation_absente")
        return 2
    try:
        served = _served_run_id_from_db(dataset)
    except Exception as exc:  # noqa: BLE001 -- any read failure: served unknown
        print(
            f"ARCHIVE_PURGE REFUSED dataset={dataset} "
            f"reason=lecture_etl_runs_impossible ({type(exc).__name__})"
        )
        return 2
    try:
        plan = plan_purge(root, dataset, served, args.keep_editions)
    except PurgeRefusedError as exc:
        print(f"ARCHIVE_PURGE REFUSED dataset={dataset} reason={exc.reason}")
        return 2
    mode = "applied" if args.apply else "dry-run"
    if args.apply:
        checked = apply_purge(root, plan, _deployment_sha())
    if args.json:
        print(
            json.dumps(
                {**plan, "mode": mode, "deployment_sha": _deployment_sha()},
                indent=2,
                sort_keys=True,
            )
        )
    print(
        f"ARCHIVE_PURGE {mode} dataset={dataset} "
        f"editions_kept={plan['editions_kept']} records={len(plan['records'])} "
        f"objects={len(plan['objects'])} bytes={plan['bytes']}"
    )
    if args.apply:
        print(
            f"SOURCE_ARCHIVE_OK records={checked['records']} "
            f"objets={checked['objects']} octets={checked['bytes']} "
            f"orphelins={checked['orphans']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.etl.source_archive",
        description="Check, measure or purge the source archive (SOURCE_ARCHIVE_DIR).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--dataset")
    check.add_argument("--run-id")
    measure = sub.add_parser("stats")
    measure.add_argument("--json", action="store_true")
    purge = sub.add_parser(
        "purge",
        description="Retention D-125: served edition + previous one. Dry run "
        "unless --apply --confirm <dataset> (Ianis's gesture, journaled).",
    )
    purge.add_argument("--dataset", required=True)
    purge.add_argument("--keep-editions", type=int, default=MIN_KEEP_EDITIONS)
    purge.add_argument("--apply", action="store_true")
    purge.add_argument("--confirm")
    purge.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = os.environ.get("SOURCE_ARCHIVE_DIR", "")
    try:
        if not root:
            raise SourceArchiveError("source_archive_non_configuree")
        if args.command == "purge":
            return _purge_command(root, args)
        if args.command == "verify":
            r = verify(root, args.dataset, args.run_id)
            print(
                f"SOURCE_ARCHIVE_OK records={r['records']} objets={r['objects']} "
                f"octets={r['bytes']} orphelins={r['orphans']}"
            )
        elif args.json:
            print(
                json.dumps(
                    {"deployment_sha": _deployment_sha(), "datasets": stats(root)},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for s in stats(root):
                print(
                    f"SOURCE_ARCHIVE_STATS dataset={s['dataset']} "
                    f"records={s['records']} objets={s['objects']} octets={s['bytes']}"
                )
    except SourceArchiveError as exc:
        print(f"SOURCE_ARCHIVE_BAD {exc.reason} {exc.path}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
