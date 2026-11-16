"""Post-swap data quality validation for ETL pipelines.

Provides three functions:
- check_null_rates: detect columns with high null rates
  (with TABLESAMPLE for large tables)
- persist_validation_run: write a row to validation_runs
- run_validation: orchestrate check + persist and return structured result
"""

import json
import logging

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)

# Row count threshold above which TABLESAMPLE(1%) is used instead of full scan
_TABLESAMPLE_THRESHOLD = 1_000_000


def scan_timeout_sql() -> str:
    """Statement timeout of a NULL scan of a published table (both passes).

    A partitioned parent is scanned exactly: the 94.8 M national parcelles did
    not fit in a fixed 8 s (cadastre run 122, 2026-10-31).
    """
    from app.config import settings

    return f"SET LOCAL statement_timeout = '{settings.etl_validation_scan_timeout_s}s'"


class ValidationUnavailableError(RuntimeError):
    """A required observation could not be obtained; never a clean scan."""

    def __init__(self, message: str, *, evidence: dict | None = None):
        super().__init__(message)
        self.evidence = evidence or {}


class NullRateFindings(list):
    """List-compatible anomalies with a denominator even when none are found."""

    def __init__(self, findings: list[dict], evidence: dict):
        super().__init__(findings)
        self.evidence = evidence


def check_null_rates(
    dsn: str,
    table_name: str,
    threshold: float,
    *,
    conn: psycopg.Connection | None = None,
    columns: tuple[str, ...] | None = None,
) -> list[dict]:
    """Bounded observation of public-table NULL rates, with explicit evidence.

    Estimates only select an alert sampling strategy. They never establish an
    exact population, and an empty sample is unavailable rather than clean.
    Partitioned parents use an exact scan of all descendants. The supplied
    connection keeps both required/expected passes in the same transaction.
    """
    if conn is None:
        with psycopg.connect(dsn, connect_timeout=5) as owned:
            owned.set_read_only(True)
            return check_null_rates(
                dsn, table_name, threshold, conn=owned, columns=columns
            )
    with conn.transaction():
        conn.execute("SET LOCAL statement_timeout = '8s'")
        conn.execute("SET LOCAL lock_timeout = '2s'")
        relation = conn.execute(
            "SELECT c.oid, c.relkind, c.reltuples FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=%s",
            (table_name,),
        ).fetchone()
        if not relation:
            raise ValidationUnavailableError(f"Missing public table: {table_name}")
        oid, kind, estimate = relation
        rows = conn.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid=%s "
            "AND attnum>0 AND NOT attisdropped ORDER BY attnum",
            (oid,),
        ).fetchall()
        available = [row[0] for row in rows]
        selected = list(columns) if columns is not None else available
        if not selected or set(selected) - set(available):
            raise ValidationUnavailableError(f"Missing columns in public.{table_name}")
        sampled = kind == "r" and estimate > _TABLESAMPLE_THRESHOLD
        evidence = dict(
            schema="public",
            table=table_name,
            oid=oid,
            population_estimated=float(estimate),
            population_exact=None,
            method="TABLESAMPLE SYSTEM(1)" if sampled else "exact",
            seed=0 if sampled else None,
            observed=None,
            null_counts={},
        )
        relation_sql = sql.Identifier("public", table_name)
        if sampled:
            relation_sql = sql.SQL("{} TABLESAMPLE SYSTEM(1) REPEATABLE(0)").format(
                relation_sql
            )
        conn.execute(scan_timeout_sql())
        counts = sql.SQL(", ").join(
            sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(sql.Identifier(c))
            for c in selected
        )
        row = conn.execute(
            sql.SQL("SELECT count(*), {} FROM {}").format(counts, relation_sql)
        ).fetchone()
        if row is None:
            raise ValidationUnavailableError(
                "NULL scan returned no result", evidence=evidence
            )
        total = int(row[0])
        evidence["observed"] = total
        evidence["population_exact"] = None if sampled else total
        evidence["null_counts"] = dict(zip(selected, map(int, row[1:]), strict=True))
        if total == 0:
            raise ValidationUnavailableError(
                "NULL scan has no observations", evidence=evidence
            )
        findings = [
            dict(column=c, null_rate=round(n / total, 4), null_count=n, observed=total)
            for c, n in evidence["null_counts"].items()
            if n / total > threshold
        ]
        return NullRateFindings(findings, evidence)


# validation_runs.row_drop_pct is NUMERIC(5,2) (migration 054).
_ROW_DROP_PCT_LIMIT = 999.99


def _row_drop_pct(row_count_before: int | None, row_count_after: int) -> float | None:
    """Drop in percent (negative = growth), or None when not representable.

    A first import over a few seed rows, or the national import over 44/85,
    grows by far more than 999.99 %: storing it would overflow the column and
    fail the publication (04.1-21). The exact counts stay in their columns.
    """
    if row_count_before is None or row_count_before <= 0:
        return None
    pct = round((row_count_before - row_count_after) / row_count_before * 100, 2)
    return pct if abs(pct) <= _ROW_DROP_PCT_LIMIT else None


def persist_validation_run(
    dsn: str,
    dataset_name: str,
    *,
    row_count_before: int | None,
    row_count_after: int,
    null_rate_findings: list[dict],
    passed: bool,
    blocked_swap: bool,
    notes: str | None = None,
) -> int:
    """Insert a row into validation_runs and return its id.

    Args:
        dsn: psycopg sync connection string.
        dataset_name: ETL dataset name.
        row_count_before: Row count from dataset_metadata before swap (may be None).
        row_count_after: Row count in production table after swap.
        null_rate_findings: Output of check_null_rates().
        passed: True if no validation issues detected.
        blocked_swap: True if the swap was blocked by a pre-swap guard.
        notes: Optional free-text note.

    Returns:
        The id of the inserted validation_runs row.
    """
    row_drop_pct = _row_drop_pct(row_count_before, row_count_after)

    findings_json = json.dumps(null_rate_findings)

    with psycopg.connect(dsn) as conn:
        cur = conn.execute(
            """
            INSERT INTO validation_runs (
                dataset_name,
                row_count_before,
                row_count_after,
                row_drop_pct,
                null_rate_findings,
                passed,
                blocked_swap,
                notes
            ) VALUES (
                %(dataset_name)s,
                %(row_count_before)s,
                %(row_count_after)s,
                %(row_drop_pct)s,
                %(findings)s::jsonb,
                %(passed)s,
                %(blocked_swap)s,
                %(notes)s
            )
            RETURNING id
            """,
            {
                "dataset_name": dataset_name,
                "row_count_before": row_count_before,
                "row_count_after": row_count_after,
                "row_drop_pct": row_drop_pct,
                "findings": findings_json,
                "passed": passed,
                "blocked_swap": blocked_swap,
                "notes": notes,
            },
        )
        row = cur.fetchone()
        conn.commit()

    run_id: int = row[0]
    logger.info(
        "persist_validation_run: dataset='%s' id=%d passed=%s blocked_swap=%s",
        dataset_name,
        run_id,
        passed,
        blocked_swap,
    )
    return run_id


def run_validation(
    dsn: str,
    dataset_name: str,
    table_name: str,
    *,
    row_count_before: int | None,
    row_count_after: int,
    null_rate_threshold: float,
) -> dict:
    """Run post-swap validation and persist the result.

    Checks null rates on *table_name*, persists a validation_runs row, and
    returns a structured result dict.

    Args:
        dsn: psycopg sync connection string.
        dataset_name: ETL dataset name (for validation_runs.dataset_name).
        table_name: Actual DB table name to inspect (may differ from dataset_name).
        row_count_before: Pre-swap row count from dataset_metadata.
        row_count_after: Row count in the live table after swap.
        null_rate_threshold: Float in [0, 1]. Columns above this are flagged.

    Returns:
        dict with keys:
            validation_run_id: int
            passed: bool
            null_rate_findings: list[dict]
            row_count_before: int | None
            row_count_after: int
    """
    logger.info(
        "run_validation: dataset='%s' table='%s' threshold=%.0f%%",
        dataset_name,
        table_name,
        null_rate_threshold * 100,
    )

    findings = check_null_rates(dsn, table_name, null_rate_threshold)
    passed = len(findings) == 0

    run_id = persist_validation_run(
        dsn,
        dataset_name,
        row_count_before=row_count_before,
        row_count_after=row_count_after,
        null_rate_findings=findings,
        passed=passed,
        blocked_swap=False,
        notes=json.dumps(getattr(findings, "evidence", {})),
    )

    return {
        "validation_run_id": run_id,
        "passed": passed,
        "null_rate_findings": findings,
        "observation": getattr(findings, "evidence", {}),
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
    }
