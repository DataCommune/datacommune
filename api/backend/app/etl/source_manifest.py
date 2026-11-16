"""Strict local snapshots for the two reviewed communal CSV contracts.

Qualification and stable copies precede runner entry. This module does not use
network or database access and never returns durable inputs for ETL cleanup.
Repeated unbuffered reads detect content divergence even when timestamps collide.
They do not prove upstream immutability between observations or after admission;
the private, manifest-hashed copy is the input consumed by the runner.
"""

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings

CONTRACTS = {
    "vacance": (
        2026,
        "lovac-communes-v1:2026:2025",
        {"communes": "lovac_communes.csv"},
    ),
    "loyers": (
        2025,
        "loyers-prediction-metadata-v1:2025",
        {
            name: f"{name}.csv"
            for name in ("appartement", "maison", "1-2_pieces", "3+_pieces")
        },
    ),
}


class SourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: str
    path: str
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    dataset: Literal["vacance", "loyers"]
    vintage: int
    source_contract: str
    files: tuple[SourceFile, ...]


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate source manifest key")
        result[key] = value
    return result


def _read_manifest(path: Path, dataset: str) -> tuple[SourceManifest, str]:
    if not path.is_absolute():
        raise ValueError("Source manifest path must be absolute")
    _durable_path(path)
    path_before = path.stat()
    with path.open("rb", buffering=0) as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(131073)
        after = os.fstat(stream.fileno())
        stream.seek(0)
        confirmed = stream.read(131073)
        final = os.fstat(stream.fileno())
    if (
        len(data) > 131072
        or data != confirmed
        or any(_identity(before) != _identity(info) for info in (after, final))
        or _identity(path_before) != _identity(path.stat())
        or _identity(before)[:4] != _identity(path_before)[:4]
    ):
        raise ValueError("Source manifest is oversized or changed while reading")
    try:
        parsed = json.loads(data, object_pairs_hook=_unique_keys)
        if not isinstance(parsed, dict) or type(parsed.get("version")) is not int:
            raise ValueError("Source manifest version must be an integer")
        manifest = SourceManifest.model_validate_json(data)
    except (ValidationError, ValueError):
        raise ValueError("Invalid frozen source manifest") from None
    if manifest.dataset != dataset:
        raise ValueError("Source manifest dataset mismatch")
    vintage, contract, kinds = CONTRACTS[dataset]
    if (manifest.vintage, manifest.source_contract) != (vintage, contract):
        raise ValueError("Source manifest vintage or contract mismatch")
    if len(manifest.files) != len(kinds) or {f.kind for f in manifest.files} != set(
        kinds
    ):
        raise ValueError("Source manifest requires the exact complete file set")
    if len({entry.sha256 for entry in manifest.files}) != len(manifest.files):
        raise ValueError("Frozen source types must not reuse identical file contents")
    return manifest, hashlib.sha256(data).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # Compare ctime within the same API: Windows fstat/stat can disagree even
    # on stable files. The first four fields bind the descriptor to the path.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _durable_path(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    temporary = Path(settings.etl_temp_dir).resolve()
    if (
        not path.is_absolute()
        or not resolved.is_file()
        or resolved.is_relative_to(temporary)
    ):
        raise ValueError(
            "Frozen inputs must be regular durable files outside ETL_TEMP_DIR"
        )
    return resolved


def _copy_verified(entry: SourceFile, target: Path) -> None:
    source = _durable_path(Path(entry.path))
    if entry.bytes > settings.etl_max_download_bytes:
        raise ValueError("Frozen source exceeds the existing download size cap")
    digest = hashlib.sha256()
    total = 0
    path_before = Path(entry.path).stat()
    with source.open("rb", buffering=0) as incoming, target.open("xb") as outgoing:
        before = os.fstat(incoming.fileno())
        if before.st_size != entry.bytes:
            raise ValueError("Frozen source size mismatch")
        while chunk := incoming.read(65536):
            total += len(chunk)
            if total > entry.bytes:
                raise ValueError("Frozen source grew while reading")
            digest.update(chunk)
            outgoing.write(chunk)
        after = os.fstat(incoming.fileno())
        # Metadata can remain equal after a same-size rewrite. Read the same
        # descriptor again, without a Python read buffer reusing old bytes.
        incoming.seek(0)
        confirmed_digest = hashlib.sha256()
        confirmed_total = 0
        while chunk := incoming.read(65536):
            confirmed_total += len(chunk)
            if confirmed_total > entry.bytes:
                raise ValueError("Frozen source changed while reading")
            confirmed_digest.update(chunk)
        final = os.fstat(incoming.fileno())
    if (
        total != entry.bytes
        or confirmed_total != total
        or confirmed_digest.hexdigest() != digest.hexdigest()
        or any(_identity(before) != _identity(info) for info in (after, final))
        or _identity(path_before) != _identity(Path(entry.path).stat())
        or _identity(before)[:4] != _identity(path_before)[:4]
    ):
        raise ValueError("Frozen source changed while reading")
    if digest.hexdigest() != entry.sha256:
        raise ValueError("Frozen source checksum mismatch")


@dataclass(frozen=True)
class FrozenSources:
    manifest: SourceManifest
    manifest_sha256: str
    paths: tuple[Path, ...]
    directory: Path
    root: Path

    def close(self) -> None:
        """Release only this invocation's copies, including on lock refusal."""
        _remove_snapshot(self.directory, self.root)

    @property
    def checkpoint_scope_key(self) -> str:
        return f"frozen-source-v1:{self.manifest.dataset}:{self.manifest_sha256}"

    def verified_paths(self) -> list[Path]:
        """Recheck the working copies; no fallback to mutable upstream inputs."""
        for entry, path in zip(self.manifest.files, self.paths, strict=True):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(65536):
                    digest.update(chunk)
            if path.stat().st_size != entry.bytes or digest.hexdigest() != entry.sha256:
                raise ValueError("Frozen working snapshot checksum mismatch")
        return list(self.paths)


def _remove_snapshot(snapshot: Path, root: Path) -> None:
    if snapshot.resolve() != snapshot or snapshot.parent != root:
        raise ValueError("Frozen snapshot cleanup escaped its owned directory")
    if snapshot.exists():
        shutil.rmtree(snapshot)


def prepare_sources(
    path: Path, dataset: str, validate_schema: Callable[[Path], None]
) -> FrozenSources:
    """Fail before runner/staging; snapshot all files under owned temporary output."""
    manifest, digest = _read_manifest(path, dataset)
    # Runner success cleans datacommune_{dataset}; another invocation may
    # already have prepared its copies before acquiring that dataset's lock.
    root = (Path(settings.etl_temp_dir) / "frozen_sources").resolve()
    root.mkdir(parents=True, exist_ok=True)
    required = sum(entry.bytes for entry in manifest.files)
    if shutil.disk_usage(root).free - required < settings.etl_min_disk_gb * 1024**3:
        raise ValueError("Frozen snapshots would violate the existing ETL disk reserve")
    snapshot = Path(tempfile.mkdtemp(prefix="frozen-", dir=root)).resolve()
    paths = []
    try:
        for entry in manifest.files:
            target = snapshot / CONTRACTS[dataset][2][entry.kind]
            _copy_verified(entry, target)
            validate_schema(target)
            if dataset == "vacance":
                with target.open(encoding="latin-1") as stream:
                    columns = next(
                        csv.reader(io.StringIO(stream.readline()), delimiter=";")
                    )
                years = {c for c in columns if c.startswith("CODGEO_")}
                if max(years, default="") != "CODGEO_26" or not {
                    "LIBGEO_26",
                    "ff_pp_total_25",
                    "pp_vacant_26",
                    "pp_vacant_plus_2ans_26",
                }.issubset(columns):
                    raise ValueError(
                        "Frozen LOVAC columns do not match the reviewed vintage"
                    )
            paths.append(target)
        # A repeated physical source cannot stand in for several housing types.
        identities = [Path(entry.path).resolve() for entry in manifest.files]
        if len(set(identities)) != len(identities):
            raise ValueError("Frozen sources must have distinct paths")
        return FrozenSources(manifest, digest, tuple(paths), snapshot, root)
    except Exception:
        _remove_snapshot(snapshot, root)
        raise
