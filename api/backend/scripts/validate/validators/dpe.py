"""Validate DPE diagnostics against ADEME API."""

from __future__ import annotations

import time

import httpx
import psycopg

from app.etl.base import BasePipeline
from app.etl_dpe.sources import DPE_SOURCES
from app.scope.model import DEPARTEMENTS_HEBERGEABLES

from ..base import BaseValidator, FieldResult, FieldSpec, RowResult, ValidationReport
from ..compare import FieldType, compare_field


class SourceContradictionError(ValueError):
    """The declared provenance and the fetched official records disagree."""


class SourceResponseError(ValueError):
    """The official response cannot establish presence or absence."""


class DpeValidator(BaseValidator):
    dataset_name = "diagnostics_dpe"
    table_name = "diagnostics_dpe"
    id_column = "numero_dpe"

    @property
    def fields_to_compare(self) -> list[FieldSpec]:
        return [
            FieldSpec("etiquette_dpe", "etiquette_dpe", FieldType.EXACT),
            FieldSpec("etiquette_ges", "etiquette_ges", FieldType.EXACT),
            FieldSpec(
                "surface_habitable",
                "surface_habitable_logement",
                FieldType.NUMERIC,
                tolerance=0.01,
            ),
            FieldSpec(
                "conso_5_usages_ep",
                "conso_5_usages_ep",
                FieldType.NUMERIC,
                tolerance=0.01,
            ),
            FieldSpec(
                "emission_ges_5_usages",
                "emission_ges_5_usages",
                FieldType.NUMERIC,
                tolerance=0.01,
            ),
        ]

    def sample_query(self, n: int) -> str:
        columns = [
            self.id_column,
            "code_departement",
            "source_observations",
            "source_state",
        ] + [spec.db_column for spec in self.fields_to_compare]
        return (
            f"SELECT {', '.join(columns)} FROM {self.table_name} "
            "WHERE NOT is_tombstone AND source_state NOT IN "
            "('absent_from_complete_scope', 'explicitly_deactivated') "
            f"ORDER BY random() LIMIT {int(n)}"
        )

    def fetch_gov_record(self, client: httpx.Client, row: dict) -> dict | None:  # noqa: C901
        pairs = {
            (o.get("source_slug"), o.get("department"))
            for o in row.get("source_observations", [])
            if o.get("state") == "present"
        }
        # Any source on any hostable departement (migration 101 constraint).
        if not pairs or any(
            slug not in DPE_SOURCES or department not in DEPARTEMENTS_HEBERGEABLES
            for slug, department in pairs
        ):
            raise SourceContradictionError("Active official provenance unavailable")
        records = []
        for slug, department in sorted(pairs):
            if department != row["code_departement"]:
                raise SourceContradictionError("Provenance department disagrees")
            resp = client.get(
                DPE_SOURCES[slug].endpoint,
                params={
                    "qs": f'numero_dpe:"{row["numero_dpe"]}" '
                    f'AND code_departement_ban:"{department}"',
                    "size": 2,
                },
            )
            resp.raise_for_status()
            results = self.response_records(resp)
            if len(results) > 1:
                raise SourceContradictionError(
                    "Multiple records for the official number"
                )
            record = results[0] if results else None
            if record and record.get("numero_dpe") != row["numero_dpe"]:
                raise SourceContradictionError("Official response number disagrees")
            if record is not None and (
                str(record.get("code_departement_ban")) != department
            ):
                raise SourceContradictionError("Official response department disagrees")
            records.append(record)
        if all(record is None for record in records):
            return None
        if any(record is None for record in records):
            raise SourceContradictionError("One active source copy is absent")
        # Only return the shared projection after every active copy agrees.
        for spec in self.fields_to_compare:
            values = [record.get(spec.gov_key) for record in records]
            if any(value != values[0] for value in values[1:]):
                raise SourceContradictionError("Active official source copies disagree")
        return {
            spec.gov_key: records[0].get(spec.gov_key)
            for spec in self.fields_to_compare
        }

    @staticmethod
    def response_records(response: httpx.Response) -> list[dict]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceResponseError("Invalid official response JSON") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise SourceResponseError("Invalid official response shape")
        records = payload["results"]
        if any(not isinstance(record, dict) for record in records):
            raise SourceResponseError("Invalid official response record")
        return records

    def validate_row(self, client: httpx.Client, row: dict) -> RowResult:
        number = str(row[self.id_column])
        try:
            record = self.fetch_gov_record(client, row)
        except SourceResponseError as exc:
            return RowResult(number, "api_error", error=str(exc))
        except (SourceContradictionError, ValueError, TypeError) as exc:
            return RowResult(number, "source_contradiction", error=str(exc))
        except httpx.HTTPError:
            return RowResult(number, "api_error", error="Official source inaccessible")
        if record is None:
            # Observation only; never writes a source withdrawal into the DB.
            return RowResult(number, "source_absent")
        fields = []
        for spec in self.fields_to_compare:
            match, detail = compare_field(
                row.get(spec.db_column),
                record.get(spec.gov_key),
                spec.field_type,
                spec.tolerance,
            )
            fields.append(FieldResult(spec.db_column, match, detail))
        return RowResult(
            number,
            "ok" if all(f.match for f in fields) else "divergence",
            fields=fields,
        )

    def run(self, n: int = 10, rate_limit: float = 0.5) -> ValidationReport:
        report = ValidationReport(self.dataset_name, n)
        with psycopg.connect(BasePipeline.get_sync_dsn()) as conn:
            cur = conn.execute(self.sample_query(n))
            columns = [column.name for column in cur.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        counters = {
            "ok": "ok",
            "divergence": "divergences",
            "source_contradiction": "divergences",
            "source_absent": "not_found",
            "api_error": "api_errors",
        }
        with httpx.Client(timeout=15.0) as client:
            for row in rows:
                result = self.validate_row(client, row)
                report.rows.append(result)
                counter = counters[result.status]
                setattr(report, counter, getattr(report, counter) + 1)
                time.sleep(rate_limit)
        return report
