"""Closed DPE sources and independently verifiable private import manifests."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.etl_dpe.config import (
    COLUMN_MAPPING,
    ETL_DPE_SOURCES,
    EXPECTED_COLUMNS,
    SOURCE_FIELD_FORMATS,
    SOURCE_FIELD_TYPES,
    api_base_url,
)
from app.scope.model import DEPARTEMENTS_HEBERGEABLES

# An archive replays only with the column list that produced it
# (P-ARCHIVE-DPE, option B): 04.15 requests 4 more fields than 58.2.
MAPPING_VERSION = "04.15-44-fields-v1"


def expected_pairs(departements: Iterable[str]) -> frozenset[tuple[str, str]]:
    """Source x departement matrix: every source on every departement given.

    A generation's contract (manifest, reconciliation, quality) is the matrix
    of its own departements, never a fixed list. A code outside
    ``DEPARTEMENTS_HEBERGEABLES`` (overseas collectivity, unknown code) is
    refused: the observation constraint (migration 101) would refuse it.
    """
    codes = set(departements)
    outside = sorted(code for code in codes if code not in DEPARTEMENTS_HEBERGEABLES)
    if outside:
        raise ValueError(f"DPE departements outside the hostable list: {outside}")
    return frozenset((slug, code) for slug in ETL_DPE_SOURCES for code in codes)


# Historical existing-build split of 44 (measured page chains): kept readable
# for the archives that used it, never a model for other departements.
SPLIT_44_FILTERS = {
    "house": 'code_departement_ban:"44" AND type_batiment:"maison"',
    "flat_early": 'code_departement_ban:"44" AND type_batiment:"appartement" '
    "AND date_etablissement_dpe:[* TO 2023-12-31]",
    "flat_later": 'code_departement_ban:"44" AND type_batiment:"appartement" '
    "AND NOT date_etablissement_dpe:[* TO 2023-12-31]",
    "other": 'code_departement_ban:"44" AND NOT '
    '(type_batiment:"maison" OR type_batiment:"appartement")',
}


@dataclass(frozen=True)
class DpeSourceDescriptor:
    slug: str
    dataset_id: str
    regime: str
    columns: tuple[str, ...]
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...]
    column_mapping: dict[str, str]
    units: dict[str, str]
    mapping_version: str = MAPPING_VERSION

    @property
    def endpoint(self) -> str:
        return api_base_url(self.slug)

    def filter_for(self, department: str) -> str:
        if department not in DEPARTEMENTS_HEBERGEABLES:
            raise ValueError("Unsupported DPE department")
        return f'code_departement_ban:"{department}"'


DPE_SOURCES = {
    slug: DpeSourceDescriptor(
        slug=slug,
        dataset_id=dataset_id,
        regime=regime,
        columns=tuple(EXPECTED_COLUMNS),
        required_columns=tuple(EXPECTED_COLUMNS),
        optional_columns=(),  # Values may be NULL; the measured schema must exist.
        column_mapping=dict(COLUMN_MAPPING),
        units={
            "surface_habitable": "m2",
            "conso_5_usages_ep": "kWhEP/an",
            "emission_ges_5_usages": "kgCO2/an",
            "conso_5_usages_par_m2_ep": "kWhEP/m2/an",
            "emission_ges_5_usages_par_m2": "kgCO2/m2/an",
        },
    )
    for slug, regime, dataset_id in (
        ("dpe03existant", "existant", "meg-83tjwtg8dyz4vv7h1dqe"),
        ("dpe02neuf", "neuf", "g3cgx7jb3cmys5voxz1mrm22"),
    )
}


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def decode_json(content: str) -> dict:
    # Keep decimal lexemes: binary float round-trips can alter source values.
    result = json.loads(content, parse_float=Decimal)
    if not isinstance(result, dict):
        raise ValueError("DPE response must be an object")  # noqa: TRY004
    return result


def source_version(metadata: dict, source: DpeSourceDescriptor) -> str:
    if (
        metadata.get("id") != source.dataset_id
        or metadata.get("slug") != source.slug
        or metadata.get("href") != source.endpoint.removesuffix("/lines")
        or not metadata.get("dataUpdatedAt")
    ):
        raise ValueError("DPE source version is not official metadata")
    validate_source_schema(metadata.get("schema"), source)
    canonical = json.dumps(
        {
            key: metadata.get(key)
            for key in ("id", "slug", "href", "dataUpdatedAt", "schema", "virtual")
        },
        sort_keys=True,
        ensure_ascii=False,
        default=lambda value: {"decimal": str(value)},
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_source_schema(schema: Any, source: DpeSourceDescriptor) -> None:
    if not isinstance(schema, list):
        raise ValueError("DPE metadata schema mismatch")  # noqa: TRY004
    selected = [
        field
        for field in schema
        if isinstance(field, dict) and field.get("key") in source.columns
    ]
    by_name = {field["key"]: field for field in selected}
    if len(selected) != len(source.columns) or set(by_name) != set(source.columns):
        raise ValueError("DPE metadata schema mismatch")
    for name, field in by_name.items():
        if (field.get("type"), field.get("format")) != (
            SOURCE_FIELD_TYPES[name],
            SOURCE_FIELD_FORMATS.get(name),
        ):
            raise ValueError("DPE metadata schema type or format changed")
        # Units were absent from measured metadata. A new declaration needs
        # review against the annual-unit evidence, not silent reinterpretation.
        if field.get("unit") is not None:
            raise ValueError("DPE metadata schema declares an unreviewed unit")


def csv_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def page_cursor(next_url: str | None, endpoint: str) -> str | None:
    if not next_url:
        return None
    parsed = urlparse(next_url)
    official = urlparse(endpoint)
    cursors = parse_qs(parsed.query).get("after", [])
    allowed_paths = {official.path}
    for source in DPE_SOURCES.values():
        if source.endpoint == endpoint:
            # Measured official pages use the metadata-bound dataset ID alias.
            allowed_paths.add(
                official.path.replace(f"/{source.slug}/", f"/{source.dataset_id}/")
            )
    if (
        (parsed.scheme, parsed.netloc) != (official.scheme, official.netloc)
        or parsed.path not in allowed_paths
        or len(cursors) != 1
        or not cursors[0]
    ):
        raise ValueError("Invalid DPE pagination cursor")
    return cursors[0]


@dataclass(frozen=True)
class DpeArtifact:
    path: str
    sha256: str

    def validate(self, directory: Path) -> Path:
        path = Path(self.path).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("DPE manifest artifact outside generation")
        if not path.is_file() or sha256_file(path) != self.sha256:
            raise ValueError("DPE manifest checksum mismatch")
        return path


@dataclass(frozen=True)
class DpePageProof(DpeArtifact):
    rows: int
    after: str | None
    next_after: str | None


@dataclass(frozen=True)
class DpeCollectionPartition:
    """One original page chain of the measured disjoint existing-build 44 split."""

    partition_id: str
    filter: str
    initial_total: int
    final_total: int
    rows: int
    metadata_start: DpeArtifact
    metadata_end: DpeArtifact
    pages: tuple[DpePageProof, ...]

    def validate(self, version: str, directory: Path) -> None:
        if self.filter != SPLIT_44_FILTERS.get(self.partition_id):
            raise ValueError("Unreviewed DPE partition filter")
        counts = self.initial_total, self.final_total, self.rows
        if (
            any(type(count) is not int or count < 0 for count in counts)
            or len(set(counts)) != 1
        ):
            raise ValueError("DPE partition counts disagree")
        if not self.pages:
            raise ValueError("Missing DPE partition page chain")
        for artifact in (self.metadata_start, self.metadata_end):
            metadata = decode_json(
                artifact.validate(directory).read_text(encoding="utf-8")
            )
            if source_version(metadata, DPE_SOURCES["dpe03existant"]) != version:
                raise ValueError("DPE partition metadata version changed")


@dataclass(frozen=True)
class DpeSourceFile:
    source_slug: str
    department: str
    import_id: str
    path: str
    sha256: str
    endpoint: str
    filter: str
    columns: tuple[str, ...]
    mapping_version: str
    version_start: str
    version_end: str
    initial_total: int
    final_total: int
    rows: int
    pagination_complete: bool
    observed_at: str
    metadata_start: DpeArtifact
    metadata_end: DpeArtifact
    pages: tuple[DpePageProof, ...]
    partitions: tuple[DpeCollectionPartition, ...] = ()
    # Import that downloaded these bytes when this generation reuses a complete
    # pair of an unpublished previous import (plan 04.2-03); None otherwise.
    reused_from: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DpeSourceFile:
        """Rebuild an entry from its JSON form (manifest, pair receipt)."""
        raw = dict(raw)
        raw["columns"] = tuple(raw["columns"])
        raw["pages"] = tuple(DpePageProof(**page) for page in raw["pages"])
        for field in ("metadata_start", "metadata_end"):
            raw[field] = DpeArtifact(**raw[field])
        partitions = []
        for part in raw.get("partitions", []):
            part = dict(part)
            part["pages"] = tuple(DpePageProof(**page) for page in part["pages"])
            for field in ("metadata_start", "metadata_end"):
                part[field] = DpeArtifact(**part[field])
            partitions.append(DpeCollectionPartition(**part))
        raw["partitions"] = tuple(partitions)
        return cls(**raw)

    @property
    def pair(self) -> tuple[str, str]:
        return self.source_slug, self.department

    def validate(self, import_id: str, directory: Path) -> None:
        source = DPE_SOURCES.get(self.source_slug)
        if source is None or self.department not in DEPARTEMENTS_HEBERGEABLES:
            raise ValueError("Unsupported DPE manifest source")
        if self.import_id != import_id or self.mapping_version != MAPPING_VERSION:
            raise ValueError("DPE manifest generation or mapping mismatch")
        if self.reused_from is not None and (
            not isinstance(self.reused_from, str)
            or not self.reused_from
            or self.reused_from == import_id
        ):
            raise ValueError("Invalid DPE reuse provenance")
        if (self.endpoint, self.filter, self.columns) != (
            source.endpoint,
            source.filter_for(self.department),
            source.columns,
        ):
            raise ValueError("DPE manifest scope or projection mismatch")
        self._validate_metadata(source, directory)
        if not self.pagination_complete or bool(self.pages) == bool(self.partitions):
            raise ValueError("DPE pagination is incomplete")
        if (
            any(
                type(value) is not int or value < 0
                for value in (self.rows, self.initial_total, self.final_total)
            )
            or not self.rows == self.initial_total == self.final_total
        ):
            raise ValueError("DPE source counts disagree")
        path = DpeArtifact(self.path, self.sha256).validate(directory)
        self._validate_conversion(path, directory)

    def _validate_metadata(self, source: DpeSourceDescriptor, directory: Path) -> None:
        for artifact, version in (
            (self.metadata_start, self.version_start),
            (self.metadata_end, self.version_end),
        ):
            path = artifact.validate(directory)
            if (
                source_version(decode_json(path.read_text(encoding="utf-8")), source)
                != version
            ):
                raise ValueError("DPE version does not match metadata")
        if self.version_start != self.version_end:
            raise ValueError("DPE source version changed during collection")
        timestamp = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("DPE snapshot timestamp must be UTC")

    def _validate_conversion(self, path: Path, directory: Path) -> None:
        # Python 3.12 writes QUOTE_NOTNULL correctly, but its reader still loses
        # the NULL/empty distinction. Verify exact encoded bytes against JSON.
        with path.open("rb") as stream:
            if stream.read(len(csv_record(self.columns))) != csv_record(self.columns):
                raise ValueError("DPE CSV projection mismatch")
            count = 0
            for row in self._source_rows(directory):
                encoded = csv_record([csv_value(row.get(key)) for key in self.columns])
                if stream.read(len(encoded)) != encoded:
                    raise ValueError("DPE JSON to CSV conversion mismatch")
                count += 1
            if stream.read(1) or count != self.rows:
                raise ValueError("DPE CSV row count mismatch")

    def source_rows(self) -> Iterator[dict[str, Any]]:
        """Read private observations before lossy public transformation filters."""
        yield from self._source_rows(Path(self.path).parent)

    def _source_rows(self, directory: Path) -> Iterator[dict[str, Any]]:
        if self.partitions:
            yield from self._partition_rows(directory)
        else:
            yield from self._page_rows(self.pages, directory, self.rows)

    def _partition_rows(self, directory: Path) -> Iterator[dict[str, Any]]:
        ids = {part.partition_id for part in self.partitions}
        if (
            self.pair != ("dpe03existant", "44")
            or len(self.partitions) != 4
            or ids != set(SPLIT_44_FILTERS)
        ):
            raise ValueError(
                "DPE split requires exactly the four measured 44 partitions"
            )
        if self.pages or sum(part.rows for part in self.partitions) != self.rows:
            raise ValueError("DPE split does not cover the complete department count")
        seen_numbers = set()
        for part in self.partitions:
            part.validate(self.version_start, directory)
            for row in self._page_rows(part.pages, directory, part.rows):
                if split_partition_for(row) != part.partition_id:
                    raise ValueError("DPE row outside its collection partition")
                if row["numero_dpe"] in seen_numbers:
                    raise ValueError("Repeated DPE identity in split source union")
                seen_numbers.add(row["numero_dpe"])
                yield row

    def _page_rows(
        self, pages: tuple[DpePageProof, ...], directory: Path, expected_total: int
    ) -> Iterator[dict[str, Any]]:
        after = None
        seen = set()
        count = 0
        for page in pages:
            if page.after != after or page.after in seen:
                raise ValueError("DPE page chain is repeated or interrupted")
            seen.add(page.after)
            payload = decode_json(page.validate(directory).read_text(encoding="utf-8"))
            rows = payload.get("results")
            if not isinstance(rows, list) or len(rows) != page.rows:
                raise ValueError("DPE page row count mismatch")
            if payload.get("total", expected_total) != expected_total:
                raise ValueError("DPE page announced total disagrees with source count")
            after = page_cursor(payload.get("next"), self.endpoint)
            if after != page.next_after:
                raise ValueError("DPE page cursor proof mismatch")
            for row in rows:
                validate_source_row(row, self.department)
                count += 1
                yield row
        if after is not None or count != expected_total:
            raise ValueError("DPE terminal page or complete row count is missing")


def validate_source_row(row: dict, department: str) -> None:
    if not isinstance(row, dict):
        raise ValueError("Invalid DPE source record")  # noqa: TRY004
    number = row.get("numero_dpe")
    if not isinstance(number, str) or not number.strip() or number != number.strip():
        raise ValueError("Missing or invalid DPE source identity")
    if str(row.get("code_departement_ban")) != department:
        raise ValueError("DPE source row outside requested scope")


def split_partition_for(row: dict) -> str:
    if row.get("type_batiment") == "maison":
        return "house"
    if row.get("type_batiment") != "appartement":
        return "other"
    try:
        establishment = date.fromisoformat(row.get("date_etablissement_dpe"))
    except (TypeError, ValueError):
        # The measured complementary query includes absent/unindexed dates.
        return "flat_later"
    return "flat_early" if establishment <= date(2023, 12, 31) else "flat_later"


def csv_record(values: Iterable[Any]) -> bytes:
    stream = io.StringIO(newline="")
    csv.writer(stream, quoting=csv.QUOTE_NOTNULL).writerow(values)
    return stream.getvalue().encode("utf-8")


@dataclass(frozen=True)
class DpeImportManifest:
    import_id: str
    directory: str
    files: tuple[DpeSourceFile, ...]
    schema_version: int = 1

    @property
    def pairs(self) -> frozenset[tuple[str, str]]:
        """(source, departement) pairs of this generation, read from its files."""
        return frozenset(entry.pair for entry in self.files)

    @property
    def departements(self) -> frozenset[str]:
        """Departements of this generation: its scope, whatever the instance's."""
        return frozenset(entry.department for entry in self.files)

    def validate(self) -> None:
        """Verify the whole generation: structure, then every file and page.

        Called at load(), save() and the explicit checkpoints (presence
        collection before staging, archive, swap), never once per file.
        """
        self._validate_structure()
        for entry in self.files:
            entry.validate(self.import_id, Path(self.directory))

    def _validate_structure(self) -> None:
        """Generation, pair matrix, distinct files, one version per source.

        Reads no file content.
        """
        if self.schema_version != 1 or not self.import_id:
            raise ValueError("Invalid DPE manifest generation")
        if (
            not self.files
            or len(self.pairs) != len(self.files)
            or self.pairs != expected_pairs(self.departements)
        ):
            raise ValueError(
                "DPE manifest requires every source on every departement, once"
            )
        paths = {Path(entry.path).resolve() for entry in self.files}
        count = len(self.files)
        if len(paths) != count or len({path.name for path in paths}) != count:
            raise ValueError("DPE manifest files must be distinct")
        for slug in DPE_SOURCES:
            versions = {
                entry.version_start for entry in self.files if entry.source_slug == slug
            }
            if len(versions) != 1:
                raise ValueError("DPE source version differs across departments")

    def artifact_paths(self) -> list[Path]:
        """List all original evidence, excluding derived staging/presence files."""
        paths = []
        for entry in self.files:
            paths.extend(
                [
                    Path(entry.path),
                    Path(entry.metadata_start.path),
                    Path(entry.metadata_end.path),
                ]
            )
            paths.extend(Path(page.path) for page in entry.pages)
            for part in entry.partitions:
                paths.extend(
                    [Path(part.metadata_start.path), Path(part.metadata_end.path)]
                )
                paths.extend(Path(page.path) for page in part.pages)
        return list(dict.fromkeys(paths))

    def _relocated(self, directory: Path) -> DpeImportManifest:
        # Historical paths are lexical evidence, not paths on this machine.
        path_type = (
            PureWindowsPath if PureWindowsPath(self.directory).drive else PurePosixPath
        )
        origin = path_type(self.directory)
        if not origin.is_absolute() or ".." in origin.parts:
            raise ValueError("Invalid DPE generation root")
        destination = directory.resolve()

        def relocate(value: str) -> str:
            original = path_type(value)
            if not original.is_absolute() or ".." in original.parts:
                raise ValueError("Invalid DPE artifact reference")
            try:
                relative = original.relative_to(origin)
            except ValueError as exc:
                raise ValueError("DPE artifact outside generation") from exc
            target = destination.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError("DPE restored artifact outside generation")
            return str(target)

        def artifact(value):
            return replace(value, path=relocate(value.path))

        entries = []
        for entry in self.files:
            partitions = tuple(
                replace(
                    part,
                    metadata_start=artifact(part.metadata_start),
                    metadata_end=artifact(part.metadata_end),
                    pages=tuple(artifact(page) for page in part.pages),
                )
                for part in entry.partitions
            )
            entries.append(
                replace(
                    entry,
                    path=relocate(entry.path),
                    metadata_start=artifact(entry.metadata_start),
                    metadata_end=artifact(entry.metadata_end),
                    pages=tuple(artifact(page) for page in entry.pages),
                    partitions=partitions,
                )
            )
        return replace(self, directory=str(destination), files=tuple(entries))

    def resolve(self, path: Path) -> DpeSourceFile:
        """Entry bound to *path*, after verifying that entry only.

        A whole validation per resolved file read the generation once per
        file: about 400 complete reads at national scale (plan 04.2-03).
        """
        self._validate_structure()
        matches = [
            entry
            for entry in self.files
            if Path(entry.path).resolve() == path.resolve()
        ]
        if len(matches) != 1:
            raise ValueError("DPE file is not bound to this manifest")
        matches[0].validate(self.import_id, Path(self.directory))
        return matches[0]

    def save(self, path: Path) -> None:
        self.validate()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(path)

    @classmethod
    def load(
        cls, path: Path, *, relocated_directory: Path | None = None
    ) -> DpeImportManifest:
        payload = json.loads(path.read_text(encoding="utf-8"))
        files = tuple(DpeSourceFile.from_dict(raw) for raw in payload.pop("files"))
        manifest = cls(files=files, **payload)
        if relocated_directory is not None:
            manifest = manifest._relocated(relocated_directory)
        manifest.validate()
        return manifest
