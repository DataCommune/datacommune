#!/usr/bin/env python3
"""Read-only preflight for the fiscalite percent-points migration (D-01/D-02).

Inspects `fiscalite_locale`'s four rate columns (taux_tfpb, taux_tfpnb,
taux_th_rs, taux_teom) via `information_schema` and aggregate statistics —
never touches the row data itself — to prove four preconditions before
migration 073_semantic_contracts is allowed to mutate the table:

    1. Every rate column has one of the two known-good numeric
       type/precision/scale configurations (legacy fraction storage
       NUMERIC(6,5), or already-migrated percent-points storage
       NUMERIC(5,2)). Anything else is an unexpected type — abort.
    2. The four columns share one storage type: all legacy NUMERIC(6,5), or
       all target NUMERIC(5,2). Values cannot identify the unit safely: a
       legacy TFNB ratio above 1 still represents a legitimate rate above
       100 points. Mixed storage types are structural corruption and abort.
    3. Every value is non-negative and fits the reversible target
       NUMERIC(5,2) exactly. This is a technical representability guard, not
       an invented semantic maximum for global tax rates.
    4. `fiscalite_locale` has no dependent view/matview other than the one
       known consumer, `v_commune_synthese` (T-43-04) — an unknown
       dependency means the migration's DROP+ALTER+rebuild plan is
       incomplete and must not proceed blind.

The CLI never writes: the connection is opened with `read_only=True`
(psycopg3), so PostgreSQL itself rejects any write attempt at the wire
protocol level — a structural guarantee, not just a coding convention.

migrations/versions/073_semantic_contracts.py repeats these exact checks
transactionally right before it mutates the table — this script alone
cannot close the TOCTOU window between "preflight says OK" and "migration
starts" (a concurrent write could land in between), only the migration's
own in-transaction precondition can. This script is the fast, human-facing,
zero-risk read (run it anytime, as often as you like, never blocks a writer).

Usage (from `api/backend/`):
    uv run python -m scripts.preflight_semantic_contracts --dsn-env DATABASE_URL
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

import psycopg

TABLE = "fiscalite_locale"
RATE_COLUMNS: tuple[str, ...] = (
    "taux_tfpb",
    "taux_tfpnb",
    "taux_th_rs",
    "taux_teom",
)
KNOWN_DEPENDENT_VIEWS = frozenset({"v_commune_synthese"})

# Legacy fraction storage vs. target percent-points storage.
_RATIO_PRECISION_SCALE = (6, 5)
_POINTS_PRECISION_SCALE = (5, 2)
_TARGET_MAX = 999.99

_RATE_COLUMNS_IN_CLAUSE = ", ".join(f"'{col}'" for col in RATE_COLUMNS)


@dataclass
class PreflightReport:
    """Aggregated, redacted result — never carries raw row values."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    column_types: dict[str, tuple[str, int | None, int | None]] = field(
        default_factory=dict
    )
    counts: dict[str, dict[str, int | float | None]] = field(default_factory=dict)
    distribution_state: str = "unknown"
    dependencies: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"status: {'OK' if self.ok else 'BLOCKED'}"]
        lines.append(f"distribution_state: {self.distribution_state}")
        lines.append("column_types:")
        for col, (data_type, precision, scale) in sorted(self.column_types.items()):
            lines.append(f"  {col}: {data_type}({precision},{scale})")
        lines.append("counts (aggregated, no raw values):")
        for col, stats in sorted(self.counts.items()):
            stats_str = ", ".join(f"{k}={v}" for k, v in stats.items())
            lines.append(f"  {col}: {stats_str}")
        lines.append(f"dependencies: {self.dependencies}")
        if self.reasons:
            lines.append("reasons:")
            lines.extend(f"  - {reason}" for reason in self.reasons)
        return "\n".join(lines)


def _check_column_types(
    cur: psycopg.Cursor,  # type: ignore[type-arg]
) -> tuple[dict[str, tuple[str, int | None, int | None]], str, list[str]]:
    cur.execute(
        "SELECT column_name, data_type, numeric_precision, numeric_scale "
        "FROM information_schema.columns "
        f"WHERE table_name = %s AND column_name IN ({_RATE_COLUMNS_IN_CLAUSE})",
        (TABLE,),
    )
    types = {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}

    errors: list[str] = []
    missing = set(RATE_COLUMNS) - types.keys()
    if missing:
        errors.append(f"colonnes manquantes sur {TABLE}: {sorted(missing)}")
        return types, "unknown", errors

    for col in RATE_COLUMNS:
        data_type, precision, scale = types[col]
        if data_type != "numeric" or (precision, scale) not in (
            _RATIO_PRECISION_SCALE,
            _POINTS_PRECISION_SCALE,
        ):
            errors.append(
                f"{col}: type/precision inattendus {data_type}({precision},{scale}) "
                f"— attendu numeric{_RATIO_PRECISION_SCALE} "
                f"ou numeric{_POINTS_PRECISION_SCALE}"
            )
    if errors:
        return types, "unknown", errors

    configurations = {(precision, scale) for _, precision, scale in types.values()}
    if configurations == {_RATIO_PRECISION_SCALE}:
        return types, "ratio", []
    if configurations == {_POINTS_PRECISION_SCALE}:
        return types, "points", []
    errors.append(
        "types de stockage mixtes sur fiscalite_locale -- les quatre taux "
        "doivent etre tous numeric(6,5) ou tous numeric(5,2)"
    )
    return types, "mixed_types", errors


def _check_distribution(
    cur: psycopg.Cursor,  # type: ignore[type-arg]
    storage_state: str,
) -> tuple[str, dict[str, dict[str, int | float | None]], list[str]]:
    counts: dict[str, dict[str, int | float | None]] = {}
    errors: list[str] = []
    multiplier = 100 if storage_state == "ratio" else 1

    for col in RATE_COLUMNS:
        cur.execute(
            f"""
            SELECT
                count(*) FILTER (WHERE {col} IS NOT NULL) AS non_null,
                count(*) FILTER (WHERE {col} IS NULL) AS null_count,
                count(*) FILTER (WHERE {col} < 0) AS negative,
                count(*) FILTER (
                    WHERE ROUND({col} * %s, 2) > %s
                ) AS target_overflow,
                count(*) FILTER (
                    WHERE {col} * %s != ROUND({col} * %s, 2)
                ) AS target_precision_loss,
                min({col}) AS min_val,
                max({col}) AS max_val
            FROM {TABLE}
            """,
            (multiplier, _TARGET_MAX, multiplier, multiplier),
        )
        (
            non_null,
            null_count,
            negative,
            overflow,
            precision_loss,
            min_val,
            max_val,
        ) = cur.fetchone()
        counts[col] = {
            "non_null": non_null,
            "null": null_count,
            "negative": negative,
            "target_overflow": overflow,
            "target_precision_loss": precision_loss,
            "min": min_val,
            "max": max_val,
        }
        if negative or overflow or precision_loss:
            errors.append(
                f"{col}: {negative} valeur(s) negative(s), "
                f"{overflow} valeur(s) non representable(s) en "
                f"numeric{_POINTS_PRECISION_SCALE}, {precision_loss} valeur(s) "
                "necessitant un arrondi destructif apres conversion"
            )

    state = "invalid" if errors else storage_state
    return state, counts, errors


def _check_dependencies(
    cur: psycopg.Cursor,  # type: ignore[type-arg]
) -> tuple[list[str], list[str]]:
    cur.execute(
        """
        SELECT DISTINCT dependent_view.relname AS dependent_view
        FROM pg_depend
        JOIN pg_rewrite ON pg_depend.objid = pg_rewrite.oid
        JOIN pg_class AS dependent_view ON pg_rewrite.ev_class = dependent_view.oid
        JOIN pg_class AS source_table ON pg_depend.refobjid = source_table.oid
        WHERE source_table.relname = %s
          AND dependent_view.relname != %s
        """,
        (TABLE, TABLE),
    )
    deps = sorted({row[0] for row in cur.fetchall()})

    errors: list[str] = []
    unknown = set(deps) - KNOWN_DEPENDENT_VIEWS
    if unknown:
        errors.append(
            f"dépendances inconnues sur {TABLE}: {sorted(unknown)} — seule "
            f"{sorted(KNOWN_DEPENDENT_VIEWS)} est prise en compte par la "
            "migration 073"
        )
    return deps, errors


def run_preflight(dsn: str) -> PreflightReport:
    """Read-only inspection of fiscalite_locale's rate columns.

    Opens the connection with `read_only=True` (psycopg3) so PostgreSQL
    itself rejects any write attempt at the wire protocol level — the CLI
    structurally cannot mutate the database, not just by convention.
    """
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            column_types, storage_state, type_errors = _check_column_types(cur)
            if type_errors:
                # Distribution/dependency checks assume the columns exist
                # and are numeric — skip them rather than raise on a
                # missing/wrong-typed column.
                return PreflightReport(
                    ok=False,
                    reasons=type_errors,
                    column_types=column_types,
                    distribution_state=storage_state,
                )
            state, counts, dist_errors = _check_distribution(cur, storage_state)
            deps, dep_errors = _check_dependencies(cur)

    reasons = [*type_errors, *dist_errors, *dep_errors]
    return PreflightReport(
        ok=not reasons,
        reasons=reasons,
        column_types=column_types,
        counts=counts,
        distribution_state=state,
        dependencies=deps,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn-env",
        required=True,
        help="Environment variable containing the sync PostgreSQL DSN",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        parser.error(f"environment variable {args.dsn_env} is not set")

    report = run_preflight(dsn)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
