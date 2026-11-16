#!/usr/bin/env python3
"""Private, read-only measurements on an explicitly identified isolated copy.

No publication policy is selected here. Outputs include unsuppressed experimental
aggregates and must remain in the private evidence directory until reviewed.
Version 1 retains its original watermark meaning. Version 2 qualifies an
identified received delivery, never definitive completeness of events; both
keep their departements, exactly 44 and 85. Version 3 (plan 04.1-10) does the
same for the explicit hosted departements of the received lot.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.commune.indicator_schemas import (
    V2_DEPARTEMENTS,
    HostedDepartement,
    hosted_departements,
)
from app.dpe.lifecycle import validity_at
from scripts.commune_delivery_qualification import (
    DeliveryQualification,
    DeliveryQualificationV3,
    ProjectionDigest,
    StockBinding,
)
from scripts.verify_golden_cases import assert_select_only

CLASSES = ("residentiel_maison", "residentiel_appartement")
SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
LABEL = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")]


class MeasurementError(ValueError):
    """Safe message without source data or connection credentials."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    path: str
    sha256: SHA


class Target(StrictModel):
    kind: Literal["isolated_restore", "synthetic_disposable"]
    host: Literal["127.0.0.1", "::1"]
    port: Annotated[int, Field(strict=True, ge=1024, le=65535)]
    database: Annotated[str, Field(pattern=r"^dc_measure_[a-z0-9_]+$")]
    cluster_name: Annotated[str, Field(pattern=r"^dc_measure_[a-z0-9_]+$")]
    restore_receipt: Evidence
    relation_oids: dict[
        Literal["mutations_grain", "diagnostics_dpe"],
        Annotated[int, Field(strict=True, gt=0)],
    ]


class Source(StrictModel):
    snapshot_sha256: SHA
    generation: UUID | None
    publication_relation_oid: Annotated[int, Field(strict=True, gt=0)] | None


class Watermark(StrictModel):
    complete_before: date
    evidence: Evidence


class CommonManifest(StrictModel):
    # Versions 1 and 2 read their departements as exactly 44 then 85.
    legacy_departments: ClassVar[bool] = True
    target: Target
    snapshot_id: LABEL
    snapshot_sha256: SHA
    cog_version: LABEL
    cog_sha256: SHA
    migrations: list[LABEL]
    sources: dict[Literal["dvf", "dpe"], Source]
    departments: tuple[HostedDepartement, ...]
    window_end_exclusive: date
    dpe_as_of: date

    @model_validator(mode="after")
    def complete_contract(self) -> CommonManifest:
        if set(self.sources) != {"dvf", "dpe"}:
            raise ValueError("Both source states are required")
        if not self.migrations or len(set(self.migrations)) != len(self.migrations):
            raise ValueError("Explicit migration inventory required")
        if set(self.target.relation_oids) != {"mutations_grain", "diagnostics_dpe"}:
            raise ValueError("Both restored relation identities are required")
        for source in self.sources.values():
            if (source.generation is None) != (source.publication_relation_oid is None):
                raise ValueError("Generation and source relation identity must agree")
        if not self.legacy_departments:
            hosted_departements(self.departments)
        elif self.departments != tuple(sorted(V2_DEPARTEMENTS)):
            raise ValueError("Versions 1 and 2 measure exactly departements 44, 85")
        return self


class Manifest(CommonManifest):
    schema_version: Literal["commune-measure-source.v1"]
    watermarks: dict[Literal["44", "85"], Watermark | None]

    @model_validator(mode="after")
    def original_watermarks(self) -> Manifest:
        if set(self.watermarks) != {"44", "85"}:
            raise ValueError("Both original watermark states are required")
        for watermark in self.watermarks.values():
            if watermark and watermark.complete_before < self.window_end_exclusive:
                raise ValueError("Window exceeds the attested source watermark")
        return self


class ManifestV2(CommonManifest):
    schema_version: Literal["commune-measure-source.v2"]
    delivery_qualification: DeliveryQualification | None
    _verified_stock: StockBinding | None = PrivateAttr(default=None)
    _projection_verified: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def delivery_period(self) -> ManifestV2:
        delivery = self.delivery_qualification
        if delivery and (
            delivery.period_end_exclusive != self.window_end_exclusive
            or delivery.period_start
            > calendar_years_before(self.window_end_exclusive, 2)
        ):
            raise ValueError("Two analysis windows must belong to the same delivery")
        return self


class ManifestV3(ManifestV2):
    """Version 2 rules over the explicit hosted departements of the lot."""

    legacy_departments: ClassVar[bool] = False
    schema_version: Literal["commune-measure-source.v3"]  # type: ignore[assignment]
    delivery_qualification: DeliveryQualificationV3 | None  # type: ignore[assignment]

    @model_validator(mode="after")
    def delivery_departments(self) -> ManifestV3:
        delivery = self.delivery_qualification
        if delivery and delivery.scope_departments != self.departments:
            raise ValueError("The delivery must cover the measured departements")
        return self


SourceManifest = Manifest | ManifestV2


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest(path: Path, content: bytes | None = None) -> SourceManifest:
    """Bind the declaration to local receipts without copying receipt contents."""
    try:
        raw = path.read_bytes() if content is None else content
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Source manifest must be an object")  # noqa: TRY004
        version = payload.get("schema_version")
        model = {
            "commune-measure-source.v2": ManifestV2,
            "commune-measure-source.v3": ManifestV3,
        }.get(version, Manifest)
        manifest = model.model_validate_json(raw)
        receipts = [manifest.target.restore_receipt]
        if isinstance(manifest, Manifest):
            receipts += [item.evidence for item in manifest.watermarks.values() if item]
        for receipt in receipts:
            actual = (path.parent / receipt.path).resolve()
            if not actual.is_relative_to(path.parent.resolve()):
                raise MeasurementError("Receipt must be inside the manifest directory")
            if digest(actual.read_bytes()) != receipt.sha256:
                raise MeasurementError("Receipt hash mismatch")
        if isinstance(manifest, ManifestV2) and manifest.delivery_qualification:
            manifest._verified_stock = manifest.delivery_qualification.verify_receipts(
                path.parent, manifest
            )
        return manifest
    except (OSError, ValueError) as exc:
        raise MeasurementError("Invalid or missing source manifest/receipt") from exc


def connection_parameters(env_name: str, manifest: SourceManifest) -> dict[str, str]:
    """Reject implicit libpq services, multi-host routing and shared database names."""
    if env_name != "DC_MEASURE_DATABASE_URL" or not os.environ.get(env_name):
        raise MeasurementError("Explicit DC_MEASURE_DATABASE_URL is required")
    if any(key.startswith("PG") for key in os.environ):
        raise MeasurementError("Remove implicit libpq PG* environment settings")
    try:
        params = conninfo_to_dict(os.environ[env_name])
    except Exception as exc:
        raise MeasurementError("Invalid measurement connection settings") from exc
    allowed = {"host", "port", "dbname", "user", "password", "sslmode"}
    target = manifest.target
    if set(params) - allowed or not params.get("user"):
        raise MeasurementError("Unsupported or incomplete connection settings")
    if (params.get("host"), params.get("port"), params.get("dbname")) != (
        target.host,
        str(target.port),
        target.database,
    ):
        raise MeasurementError("Connection does not match the isolated target")
    return params


def calendar_years_before(value: date, years: int) -> date:
    year = value.year - years
    return value.replace(
        year=year, day=min(value.day, calendar.monthrange(year, value.month)[1])
    )


def windows(end: date) -> dict[str, tuple[date, date]]:
    middle, start = calendar_years_before(end, 1), calendar_years_before(end, 2)
    return {"previous": (start, middle), "current": (middle, end)}


IDENTITY_SQL = """
SELECT current_database() AS database, current_setting('cluster_name') AS cluster_name,
       current_setting('transaction_read_only') AS read_only,
       current_setting('transaction_isolation') AS isolation,
       current_setting('statement_timeout') AS statement_timeout,
       version() AS postgres, postgis_full_version() AS postgis
"""
MIGRATIONS_SQL = "SELECT version_num FROM public.alembic_version ORDER BY version_num"
PUBLICATIONS_SQL = """
SELECT DISTINCT ON (dataset_name) dataset_name, generation::text, status, relation_oids
FROM public.etl_publications WHERE dataset_name IN ('dvf', 'dpe')
ORDER BY dataset_name, id DESC
"""
OIDS_SQL = """
SELECT 'mutations_grain'::text AS name,
       'public.mutations_grain'::regclass::oid::bigint AS oid
UNION ALL SELECT 'diagnostics_dpe', 'public.diagnostics_dpe'::regclass::oid::bigint
"""
COMMUNES_SQL = """
SELECT departement_code AS department, code AS commune
FROM public.geo_communes WHERE departement_code = ANY(%(departments)s)
ORDER BY departement_code, code
"""
# No join can multiply mutations or filter away empty geographic reference rows.
DVF_SQL = """
SELECT code_departement AS department, code_commune AS commune, classe_mutation,
       date_mutation, est_vente, hors_stats_vefa, nb_communes,
       surface_bati_totale, valeur_fonciere
FROM public.mutations_grain WHERE code_departement = ANY(%(departments)s)
ORDER BY code_departement, code_commune, classe_mutation, date_mutation
"""
# The canonical row carries its observations. Never join/explode it into sources.
# Private identities, addresses and replacement references are not selected.
DPE_SQL = """
SELECT code_insee AS commune, etiquette_dpe, type_batiment, regime_dpe,
       date_etablissement_dpe, date_fin_validite_dpe, annee_construction,
       is_tombstone, source_state, lifecycle_facts,
       revision_evidence->>'status' AS revision,
       ARRAY(SELECT DISTINCT item->>'source_slug'
             FROM jsonb_array_elements(source_observations) item
             ORDER BY item->>'source_slug') AS source_slugs
FROM public.diagnostics_dpe WHERE CASE WHEN left(code_insee, 2) = '97'
  THEN left(code_insee, 3) ELSE left(code_insee, 2) END = ANY(%(departments)s)
ORDER BY code_insee
"""
SQL = (
    IDENTITY_SQL,
    MIGRATIONS_SQL,
    PUBLICATIONS_SQL,
    OIDS_SQL,
    COMMUNES_SQL,
    DVF_SQL,
    DPE_SQL,
)


def select(conn: Any, sql: str, params: Any = None) -> Any:
    assert_select_only(sql, SQL)
    return conn.execute(sql, params)


def check_database(conn: Any, manifest: SourceManifest) -> dict[str, Any]:
    identity = select(conn, IDENTITY_SQL).fetchone()
    if not identity or any(
        (
            identity["database"] != manifest.target.database,
            identity["cluster_name"] != manifest.target.cluster_name,
            identity["read_only"] != "on",
            identity["isolation"] != "repeatable read",
            identity["statement_timeout"] not in {"60s", "1min", "60000ms"},
        )
    ):
        raise MeasurementError("Database identity or read-only isolation mismatch")
    revisions = [row["version_num"] for row in select(conn, MIGRATIONS_SQL)]
    if revisions != sorted(manifest.migrations):
        raise MeasurementError("Migration inventory mismatch")
    publications = {row["dataset_name"]: row for row in select(conn, PUBLICATIONS_SQL)}
    oids = {row["name"]: row["oid"] for row in select(conn, OIDS_SQL)}
    if oids != manifest.target.relation_oids:
        raise MeasurementError("Restored relation identity mismatch")
    for dataset, table in (("dvf", "mutations_grain"), ("dpe", "diagnostics_dpe")):
        expected = manifest.sources[dataset].generation
        actual = publications.get(dataset)
        if expected is None and actual is None:
            continue  # Explicitly unknown legacy generation, never invented.
        if (
            not actual
            or actual["status"] != "published"
            or str(expected) != actual["generation"]
        ):
            raise MeasurementError("Source generation is missing, stale or unpublished")
        if (
            actual["relation_oids"].get(table)
            != manifest.sources[dataset].publication_relation_oid
        ):
            raise MeasurementError("Source publication relation mismatch")
    return {
        "identity": identity,
        "migrations": revisions,
        "publications": publications,
        "oids": oids,
    }


def quantile(
    sorted_values: list[Decimal], fraction: Decimal, removed: int | None = None
) -> Decimal | None:
    """Continuous interpolation; removal is an index view, not an O(n) copy."""
    count = len(sorted_values) - (removed is not None)
    if not count:
        return None
    position = (count - 1) * fraction
    low, weight = int(position), position % 1

    def value(index: int) -> Decimal:
        return sorted_values[index + int(removed is not None and index >= removed)]

    return (
        value(low)
        if not weight
        else value(low) * (1 - weight) + value(low + 1) * weight
    )


def price_metrics(values: Iterable[Decimal]) -> dict[str, Any]:
    ordered = sorted(values)
    results: dict[str, Any] = {
        "n": len(ordered),
        "n_distinct": len(set(ordered)),
        "quantiles": {},
    }
    for name, fraction in (
        ("p10", Decimal(".1")),
        ("p50", Decimal(".5")),
        ("p90", Decimal(".9")),
    ):
        estimate = quantile(ordered, fraction)
        removals = (
            [quantile(ordered, fraction, i) for i in range(len(ordered))]
            if len(ordered) > 1
            else []
        )
        results["quantiles"][name] = {
            "value": estimate,
            "distance_min": estimate - ordered[0] if ordered else None,
            "distance_max": ordered[-1] - estimate if ordered else None,
            "equals_observation": estimate in ordered if ordered else None,
            "max_leave_one_out_delta": max(
                (abs(item - estimate) for item in removals), default=None
            ),
        }
    return results


def measure_dvf(
    communes: list[dict[str, str]], rows: Iterable[Mapping[str, Any]], end: date
) -> dict[str, Any]:
    periods = windows(end)
    corpus = {
        (c["department"], c["commune"], kind, period): []
        for c in communes
        for kind in CLASSES
        for period in periods
    }
    exclusions: Counter[str] = Counter()
    observed: dict[str, Counter[str]] = {c["department"]: Counter() for c in communes}
    for row in rows:
        when = row["date_mutation"]
        observed[row["department"]][when.isoformat() if when else "unknown"] += 1
        failures = dvf_exclusions(row)
        exclusions.update(failures)
        if failures:
            continue
        period = next(
            (name for name, (start, stop) in periods.items() if start <= when < stop),
            None,
        )
        if period is None:
            exclusions["outside_windows"] += 1
            continue
        key = (row["department"], row["commune"], row["classe_mutation"], period)
        if key not in corpus:
            exclusions["outside_geographic_reference"] += 1
            continue
        corpus[key].append(
            Decimal(row["valeur_fonciere"]) / Decimal(row["surface_bati_totale"])
        )
    matrix = [
        {
            "department": key[0],
            "commune": key[1],
            "type": key[2],
            "window": key[3],
            **price_metrics(values),
        }
        for key, values in sorted(corpus.items())
    ]
    return {
        "matrix": matrix,
        "comparisons": compare_windows(matrix),
        "exclusions_overlapping": dict(exclusions),
        "observed_dates_not_watermarks": observed,
    }


def compare_windows(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in matrix:
        pairs.setdefault((row["department"], row["commune"], row["type"]), {})[
            row["window"]
        ] = row
    result = []
    for (department, commune, kind), pair in pairs.items():
        previous, current = (
            pair[name]["quantiles"]["p50"]["value"] for name in ("previous", "current")
        )
        result.append(
            {
                "department": department,
                "commune": commune,
                "type": kind,
                "n_previous": pair["previous"]["n"],
                "n_current": pair["current"]["n"],
                "unrounded_median_change_pct": (current / previous - 1) * 100
                if previous and current is not None
                else None,
                "publication_policy": "pending",
            }
        )
    return result


def dvf_exclusions(row: Mapping[str, Any]) -> list[str]:
    checks = {
        "non_residential": row["classe_mutation"] not in CLASSES,
        "not_sale": row["est_vente"] is not True,
        "vefa_or_unknown": row["hors_stats_vefa"] is not False,
        "multi_commune_or_unknown": row["nb_communes"] != 1,
        "surface_nonpositive_or_unknown": row["surface_bati_totale"] is None
        or row["surface_bati_totale"] <= 0,
        "value_nonpositive_or_unknown": row["valeur_fonciere"] is None
        or row["valeur_fonciere"] <= 0,
        "date_unknown": row["date_mutation"] is None,
    }
    return [name for name, excluded in checks.items() if excluded]


def dpe_population(rows: Iterable[Mapping[str, Any]], as_of: date) -> dict[str, Any]:
    classes, axes, excluded = Counter(), {}, Counter()
    known_dates: list[date] = []
    construction: list[int] = []
    total = restricted = missing_dates = overlap = 0
    for row in rows:
        if row["is_tombstone"] or row["source_state"] in {
            "absent_from_complete_scope",
            "explicitly_deactivated",
        }:
            excluded["removed_document"] += 1
            continue
        total += 1
        label = row["etiquette_dpe"]
        classes[label if label in tuple("ABCDEFG") else "unknown"] += 1
        facts = row["lifecycle_facts"]
        validity = validity_at(
            row["date_etablissement_dpe"],
            row["date_fin_validite_dpe"],
            as_of,
            date_issues=facts.get("date_issues", ()),
        )
        replacement = facts.get("replacement_state", "not_checked")
        restricted += (
            validity == "not_expired_by_date" and replacement == "no_successor_known"
        )
        slugs = set(row["source_slugs"])
        regimes = {"dpe03existant": "existant", "dpe02neuf": "neuf"}
        seen = {regimes.get(slug, "unknown") for slug in slugs} or {
            row["regime_dpe"] or "unknown"
        }
        overlap += {"existant", "neuf"} <= seen
        dimensions = {
            "validity": [validity],
            "replacement": [replacement],
            "source_state": [row["source_state"]],
            "regimes_overlapping": seen,
            "type": [row["type_batiment"] or "unknown"],
            "revision": [row["revision"] or "unknown"],
            "graph_issues_overlapping": facts.get("graph_issues", ()),
        }
        for axis, values in dimensions.items():
            axes.setdefault(axis, Counter()).update(values)
        when = row["date_etablissement_dpe"]
        missing_dates += when is None
        if when:
            known_dates.append(when)
        if row["annee_construction"] is not None:
            construction.append(row["annee_construction"])
    counts = {label: classes[label] for label in "ABCDEFG"}
    n_known = sum(counts.values())
    maximum = max(counts.values())
    ordered = [label for label, count in counts.items() for _ in range(count)]
    construction.sort()
    return {
        "n_documentary": total,
        "classes": counts,
        "n_unknown_class": classes["unknown"],
        "n_known_classes": n_known,
        "modes": [label for label, count in counts.items() if count == maximum]
        if maximum
        else [],
        "ordinal_median": ordered[(n_known - 1) // 2] if n_known else None,
        "fg_pct": Decimal(100) * (counts["F"] + counts["G"]) / n_known
        if n_known
        else None,
        "axes": axes,
        "n_both_regimes": overlap,
        "n_restricted_date_and_no_successor_known": restricted,
        "n_unknown_establishment": missing_dates,
        "observed_establishment_min": min(known_dates, default=None),
        "observed_establishment_max": max(known_dates, default=None),
        "n_construction_known": len(construction),
        "construction_ordinal_median": construction[(len(construction) - 1) // 2]
        if construction
        else None,
        "excluded": excluded,
    }


def stream(conn: Any, sql: str, params: dict[str, Any]) -> Iterable[dict[str, Any]]:
    assert_select_only(sql, SQL)
    with conn.cursor(name="commune_measurement") as cursor:
        cursor.execute(sql, params)
        yield from cursor


def measure_dpe(
    communes: list[dict[str, str]], rows: Iterable[Mapping[str, Any]], as_of: date
) -> dict[str, Any]:
    corpus = {
        row["commune"]: {
            "department": row["department"],
            "commune": row["commune"],
            **dpe_population([], as_of),
        }
        for row in communes
    }
    outside_reference = 0
    for commune, records in groupby(rows, key=lambda row: row["commune"]):
        if commune not in corpus:
            outside_reference += sum(1 for _ in records)
        else:
            corpus[commune].update(dpe_population(records, as_of))
    return {
        "matrix": list(corpus.values()),
        "n_outside_geographic_reference": outside_reference,
    }


def verify_measured_projection(
    manifest: SourceManifest, observed: ProjectionDigest
) -> None:
    if not isinstance(manifest, ManifestV2) or not manifest.delivery_qualification:
        return
    manifest._projection_verified = False
    expected = manifest._verified_stock
    if (
        expected is None
        or expected.projected_rows != len(observed.hashes)
        or expected.projected_content_sha256 != observed.hexdigest()
    ):
        raise MeasurementError("Measured DVF content is not the reconstructed delivery")
    manifest._projection_verified = True


def window_coverage(manifest: SourceManifest) -> dict[str, str]:
    if isinstance(manifest, Manifest):
        return {
            department: "attested_by_receipt" if watermark else "unknown"
            for department, watermark in manifest.watermarks.items()
        }
    return {
        department: "delivery_received_and_stock_bound"
        if manifest._projection_verified
        else "unknown"
        for department in manifest.departments
    }


def run(manifest: SourceManifest, params: dict[str, str]) -> dict[str, Any]:
    options = (
        "-c default_transaction_read_only=on -c statement_timeout=60000 "
        "-c search_path=pg_catalog,public"
    )
    with psycopg.connect(
        **params,
        hostaddr=manifest.target.host,
        options=options,
        connect_timeout=5,
        row_factory=dict_row,
    ) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        metadata = check_database(conn, manifest)
        query_params = {"departments": list(manifest.departments)}
        communes = list(select(conn, COMMUNES_SQL, query_params))
        if {row["department"] for row in communes} != set(manifest.departments):
            raise MeasurementError("Geographic reference misses a requested department")
        cog = digest(
            json.dumps(communes, sort_keys=True, separators=(",", ":")).encode()
        )
        if cog != manifest.cog_sha256:
            raise MeasurementError("Geographic reference hash mismatch")
        started = time.monotonic()
        observed = ProjectionDigest()

        def dvf_rows() -> Iterable[dict[str, Any]]:
            for row in stream(conn, DVF_SQL, query_params):
                if isinstance(manifest, ManifestV2):
                    observed.add(row)
                yield row

        dvf = measure_dvf(communes, dvf_rows(), manifest.window_end_exclusive)
        verify_measured_projection(manifest, observed)
        dvf_seconds = time.monotonic() - started
        started = time.monotonic()
        dpe = measure_dpe(
            communes, stream(conn, DPE_SQL, query_params), manifest.dpe_as_of
        )
        dpe_seconds = time.monotonic() - started
        conn.rollback()
    return {
        "metadata": metadata,
        "dvf": dvf,
        "dpe": dpe,
        "elapsed_seconds": {"dvf": dvf_seconds, "dpe": dpe_seconds},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", required=True, choices=["DC_MEASURE_DATABASE_URL"])
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output_dir.exists() or not args.output_dir.parent.is_dir():
            raise MeasurementError(
                "Output must be a new directory under an existing parent"
            )
        manifest_bytes = args.source_manifest.read_bytes()
        manifest = read_manifest(args.source_manifest, manifest_bytes)
        params = connection_parameters(args.dsn_env, manifest)
        results = run(manifest, params)
        results.update(
            {
                "schema_version": "commune-measurements.v3"
                if isinstance(manifest, ManifestV3)
                else "commune-measurements.v2"
                if isinstance(manifest, ManifestV2)
                else "commune-measurements.v1",
                "publication_decision": "pending_independent_review",
                "source_manifest_sha256": digest(manifest_bytes),
                "script_sha256": digest(Path(__file__).read_bytes()),
                "measured_at": datetime.now(UTC).isoformat(),
                "source": manifest.model_dump(mode="json", exclude={"target"}),
                "windows": windows(manifest.window_end_exclusive),
                "window_coverage": window_coverage(manifest),
                "sql": [{"sha256": digest(sql.encode()), "text": sql} for sql in SQL],
                "pending": [
                    "C591-DATA independent review",
                    "quantile disclosure and trend policy",
                    "EXPLAIN ANALYZE and reconstruction/publication costs",
                ],
            }
        )
        args.output_dir.mkdir(mode=0o700)
        (args.output_dir / "59.1-MEASURES.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        return 0
    except MeasurementError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001
        # Driver/validation errors can contain DSNs or entire input documents.
        print(
            "Measurement refused or failed; no publication decision produced.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
