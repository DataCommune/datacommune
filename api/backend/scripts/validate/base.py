"""Base validator ABC with concrete run() orchestration."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx
import psycopg

from app.etl.base import BasePipeline

from .compare import FieldType, compare_field


@dataclass
class FieldResult:
    name: str
    match: bool
    detail: str


@dataclass
class RowResult:
    row_id: str
    status: str  # "ok" | "divergence" | "not_found" | "api_error"
    fields: list[FieldResult] = field(default_factory=list)
    error: str | None = None


@dataclass
class ValidationReport:
    dataset: str
    sample_size: int
    ok: int = 0
    divergences: int = 0
    not_found: int = 0
    api_errors: int = 0
    rows: list[RowResult] = field(default_factory=list)


@dataclass
class FieldSpec:
    """Describes how to compare one field between DB and GOV."""

    db_column: str
    gov_key: str
    field_type: FieldType
    tolerance: float = 0.0


class BaseValidator(ABC):
    @property
    @abstractmethod
    def dataset_name(self) -> str: ...

    @property
    @abstractmethod
    def table_name(self) -> str: ...

    @property
    @abstractmethod
    def id_column(self) -> str: ...

    @property
    @abstractmethod
    def fields_to_compare(self) -> list[FieldSpec]: ...

    def sample_query(self, n: int) -> str:
        cols = [self.id_column] + [f.db_column for f in self.fields_to_compare]
        select = ", ".join(cols)
        return f"SELECT {select} FROM {self.table_name} ORDER BY random() LIMIT {n}"

    @abstractmethod
    def fetch_gov_record(self, client: httpx.Client, row: dict) -> dict | None:
        """Fetch the matching record from the government API.

        Return a dict with keys matching FieldSpec.gov_key, or None if not found.
        """
        ...

    def run(self, n: int = 10, rate_limit: float = 0.5) -> ValidationReport:
        report = ValidationReport(dataset=self.dataset_name, sample_size=n)
        dsn = BasePipeline.get_sync_dsn()

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(self.sample_query(n))
            columns = [desc[0] for desc in cur.description]  # type: ignore[union-attr]
            rows = [dict(zip(columns, r, strict=False)) for r in cur.fetchall()]

        with httpx.Client(timeout=15.0) as client:
            for row in rows:
                row_id = str(row[self.id_column])
                try:
                    gov = self.fetch_gov_record(client, row)
                except httpx.HTTPError as exc:
                    report.api_errors += 1
                    report.rows.append(
                        RowResult(row_id=row_id, status="api_error", error=str(exc))
                    )
                    time.sleep(rate_limit)
                    continue

                if gov is None:
                    report.not_found += 1
                    report.rows.append(RowResult(row_id=row_id, status="not_found"))
                    time.sleep(rate_limit)
                    continue

                field_results: list[FieldResult] = []
                has_divergence = False
                for spec in self.fields_to_compare:
                    db_val = row.get(spec.db_column)
                    gov_val = gov.get(spec.gov_key)
                    match, detail = compare_field(
                        db_val, gov_val, spec.field_type, spec.tolerance
                    )
                    field_results.append(
                        FieldResult(name=spec.db_column, match=match, detail=detail)
                    )
                    if not match:
                        has_divergence = True

                if has_divergence:
                    report.divergences += 1
                    report.rows.append(
                        RowResult(
                            row_id=row_id, status="divergence", fields=field_results
                        )
                    )
                else:
                    report.ok += 1
                    report.rows.append(
                        RowResult(row_id=row_id, status="ok", fields=field_results)
                    )

                time.sleep(rate_limit)

        return report
