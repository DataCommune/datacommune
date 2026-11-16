"""Versioned delivery evidence for private measurements, never publication GO."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urljoin
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.commune.indicator_schemas import (
    V2_DEPARTEMENTS,
    HostedDepartement,
    hosted_departements,
)

SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
LABEL = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")]
COUNT = Annotated[int, Field(strict=True, ge=0)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Receipt(Closed):
    path: str
    sha256: SHA

    def read(self, directory: Path) -> bytes:
        path = (directory / self.path).resolve()
        if (
            not path.is_relative_to(directory.resolve())
            or not path.is_file()
            or path.stat().st_size > 8 * 1024 * 1024
        ):
            raise ValueError("Evidence must be a file inside its manifest directory")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("Evidence checksum mismatch")
        return content


class Partition(Closed):
    release_id: LABEL
    year: Annotated[int, Field(strict=True, ge=2000, le=2100)]
    department: HostedDepartement
    compressed: Receipt
    decompressed_sha256: SHA
    schema_sha256: SHA
    rows: COUNT

    def verify(self, directory: Path) -> None:
        data = self.compressed.read(directory)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("Compressed partition exceeds the measured size bound")
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            raw = stream.read(64 * 1024 * 1024 + 1)
            if len(raw) > 64 * 1024 * 1024 or stream.read(1):
                raise ValueError("Decompressed partition exceeds its bound")
        if hashlib.sha256(raw).hexdigest() != self.decompressed_sha256:
            raise ValueError("Decompressed checksum mismatch")
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        columns = reader.fieldnames
        required = {
            "id_mutation",
            "date_mutation",
            "code_departement",
            "code_commune",
            "valeur_fonciere",
            "type_local",
            "surface_reelle_bati",
        }
        if (
            not columns
            or len(set(columns)) != len(columns)
            or not required <= set(columns)
            or hashlib.sha256(
                json.dumps(columns, separators=(",", ":")).encode()
            ).hexdigest()
            != self.schema_sha256
        ):
            raise ValueError("Partition schema mismatch")
        count = 0
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Partition row width mismatch")
            if (
                date.fromisoformat(row["date_mutation"]).year != self.year
                or row["code_departement"] != self.department
            ):
                raise ValueError("Partition year or department mismatch")
            count += 1
        if count != self.rows:
            raise ValueError("Partition row count mismatch")


class IndexDocument(Closed):
    url: str
    content: Receipt


class IndexLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.update(value for key, value in attrs if key == "href" and value)


class ObserverBinding(Closed):
    schema_version: Literal["dvf-observer-binding.v2"]
    release_id: LABEL
    period_start: date
    period_end_exclusive: date
    partition_sha256: list[SHA]
    official_directory: Annotated[
        str, Field(pattern=r"^https://files\.data\.gouv\.fr/geo-dvf/\d{4}-\d{2}/csv/$")
    ]
    supplier_generation_code: None
    index_documents: Annotated[list[IndexDocument], Field(min_length=1)]
    reviewer: LABEL
    reviewed_at: datetime
    decision: Literal["official_scope_observed"]

    @model_validator(mode="after")
    def dated_review(self) -> ObserverBinding:
        if self.reviewed_at.utcoffset() is None:
            raise ValueError("Observer review must carry its timezone")
        if self.period_end_exclusive > self.reviewed_at.date():
            raise ValueError(
                "A received annual period cannot end after its observation"
            )
        return self

    def verify_index_scope(self, directory: Path, departments: tuple[str, ...]) -> None:
        indexes = {}
        for document in self.index_documents:
            parser = IndexLinks()
            parser.feed(document.content.read(directory).decode("utf-8"))
            indexes[document.url] = {
                urljoin(document.url, link) for link in parser.links
            }
        base = self.official_directory
        expected = {base}
        for year in range(self.period_start.year, self.period_end_exclusive.year):
            annual = f"{base}{year}/"
            departmental = f"{annual}departements/"
            expected.update((annual, departmental))
            if (
                annual not in indexes.get(base, set())
                or departmental not in indexes.get(annual, set())
                or not {f"{departmental}{dept}.csv.gz" for dept in departments}
                <= indexes.get(departmental, set())
            ):
                raise ValueError(
                    "Official indexes do not announce every scoped partition"
                )
        if set(indexes) != expected or len(indexes) != len(self.index_documents):
            raise ValueError(
                "Observer indexes are missing, repeated or outside the scope"
            )


class StockBinding(Closed):
    schema_version: Literal["dvf-stock-binding.v2"]
    release_id: LABEL
    partition_sha256: list[SHA]
    reconstruction_run_id: LABEL
    transformation_git_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    transformation_config: Receipt
    source_snapshot_sha256: SHA
    restore_receipt_sha256: SHA
    cog_sha256: SHA
    generation: UUID | None
    projected_rows: COUNT
    projected_content_sha256: SHA
    projection_method: Literal["sha256-sorted-row-digests-json.v1"]


class _Delivery(Closed):
    """Received annual files of one release; scope given by the version."""

    schema_version: Literal[
        "dvf-delivery-qualification.v2", "dvf-delivery-qualification.v3"
    ]
    release_id: LABEL
    period_start: date
    period_end_exclusive: date
    acquisition_complete: Literal[True]
    late_registrations_possible: Literal[True]
    all_mutations_before_b_complete: Literal["unknown"]
    partitions: Annotated[list[Partition], Field(min_length=1)]
    observer_binding: Receipt
    stock_binding: Receipt

    @property
    def scope_departments(self) -> tuple[str, ...]:
        raise NotImplementedError

    @model_validator(mode="before")
    @classmethod
    def explicit_boolean_facts(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            type(value.get(key)) is not bool
            for key in ("acquisition_complete", "late_registrations_possible")
        ):
            raise ValueError("Delivery facts require explicit booleans")
        return value

    @model_validator(mode="after")
    def complete_release_scope(self) -> _Delivery:
        if self.period_start >= self.period_end_exclusive:
            raise ValueError("Delivery period must be ordered")
        # The scope admits annual source partitions, not invented cutoffs.
        if (self.period_start.month, self.period_start.day) != (1, 1) or (
            self.period_end_exclusive.month,
            self.period_end_exclusive.day,
        ) != (1, 1):
            raise ValueError("Delivery coverage must identify complete annual files")
        expected = {
            (year, department)
            for year in range(self.period_start.year, self.period_end_exclusive.year)
            for department in self.scope_departments
        }
        actual = {(part.year, part.department) for part in self.partitions}
        if len(actual) != len(self.partitions) or actual != expected:
            raise ValueError("Missing, repeated or unexpected release partition")
        if any(part.release_id != self.release_id for part in self.partitions):
            raise ValueError("Mixed source releases cannot qualify one analysis")
        if len({part.compressed.path for part in self.partitions}) != len(expected):
            raise ValueError("Release partitions must use distinct artifacts")
        return self

    def verify_receipts(self, directory: Path, manifest: Any) -> StockBinding:
        observer = ObserverBinding.model_validate_json(
            self.observer_binding.read(directory)
        )
        stock = StockBinding.model_validate_json(self.stock_binding.read(directory))
        hashes = sorted(part.compressed.sha256 for part in self.partitions)
        if (
            observer.release_id != self.release_id
            or stock.release_id != self.release_id
            or sorted(observer.partition_sha256) != hashes
            or sorted(stock.partition_sha256) != hashes
            or observer.period_start != self.period_start
            or observer.period_end_exclusive != self.period_end_exclusive
        ):
            raise ValueError("Observer or reconstruction does not bind this delivery")
        if (
            stock.source_snapshot_sha256 != manifest.sources["dvf"].snapshot_sha256
            or stock.restore_receipt_sha256 != manifest.target.restore_receipt.sha256
            or stock.cog_sha256 != manifest.cog_sha256
            or stock.generation != manifest.sources["dvf"].generation
        ):
            raise ValueError("Reconstruction is not linked to the measured stock")
        observer.verify_index_scope(directory, self.scope_departments)
        stock.transformation_config.read(directory)
        for partition in self.partitions:
            partition.verify(directory)
        return stock


class DeliveryQualification(_Delivery):
    """v2: the received departements are implicit, exactly 44 and 85."""

    schema_version: Literal["dvf-delivery-qualification.v2"]

    @property
    def scope_departments(self) -> tuple[str, ...]:
        # Reading rule of the v2 contract, kept for its existing evidence.
        return tuple(sorted(V2_DEPARTEMENTS))


class DeliveryQualificationV3(_Delivery):
    """v3: the received departements are explicit hostable codes, in order."""

    schema_version: Literal["dvf-delivery-qualification.v3"]
    departments: list[HostedDepartement]

    @property
    def scope_departments(self) -> tuple[str, ...]:
        return hosted_departements(self.departments)


class ProjectionDigest:
    """Order-independent content identity preserving duplicate multiplicity."""

    def __init__(self) -> None:
        self.hashes: list[bytes] = []

    def add(self, row: dict[str, Any]) -> None:
        value = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        self.hashes.append(hashlib.sha256(value.encode()).digest())

    def hexdigest(self) -> str:
        return hashlib.sha256(b"".join(sorted(self.hashes))).hexdigest()
