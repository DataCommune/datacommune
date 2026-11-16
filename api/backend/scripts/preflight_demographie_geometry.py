#!/usr/bin/env python3
"""Read-only preflight for the demographie surface/densite backfill (D-07..D-09).

Inspects ``demographie`` joined to ``geo_communes`` -- never touches row
data -- to prove three preconditions before
``app.demographie.backfill.backfill_superficie_densite`` is allowed to run:

    1. Geometric coverage of the latest recensement millesime is known and
       explicitly accepted: PLM arrondissement codes (751xx/132xx/6938x)
       are excluded from the denominator -- they have no distinct
       ``geo_communes`` entry by construction (see
       ``app.demographie.backfill.PLM_ARRONDISSEMENT_PREFIXES``) -- but
       every remaining commune must either match a valid administrative
       geometry or the caller must have explicitly accepted a coverage
       threshold via ``--min-coverage-pct``. Coverage below that threshold,
       or no threshold given at all, blocks (D-09: never silently declare
       "good enough").
    2. No matched administrative geometry is NULL, empty or ``ST_IsValid``
       = false, and no matched geometry has a non-positive computed area.
    3. Every PLM global code (75056 Paris, 13055 Marseille, 69123 Lyon)
       that the latest demographie millesime actually references matches
       a ``geo_communes`` entry with a valid, non-empty geometry --
       scoped to what demographie references (not an unconditional "all
       three must always exist") so a deliberately scoped dataset (e.g. a
       single-department disposable fixture) is never blocked on cities
       it never claims to cover. An ambiguous/missing PLM entry that
       demographie *does* reference means the backfill would silently
       skip that city.

The CLI never writes: the connection is opened with ``read_only=True``
(psycopg3), so PostgreSQL itself rejects any write attempt at the wire
protocol level -- a structural guarantee, not just a coding convention. It
also never prints raw geometry (no WKT/coordinates) -- only aggregated
counts and INSEE code identifiers.

``app.demographie.backfill.backfill_superficie_densite`` repeats the
geometry-validity precondition transactionally right before it mutates --
this script alone cannot close the TOCTOU window between "preflight says
OK" and the backfill actually running (a concurrent geometry update could
land in between), only the backfill's own in-transaction precondition can.
This script is the fast, human-facing, zero-risk read (run it anytime, as
often as you like, never blocks a writer).

Usage (from `api/backend/`):
    uv run python -m scripts.preflight_demographie_geometry \
        --dsn "$DATABASE_URL" --min-coverage-pct 95
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

import psycopg

PLM_GLOBAL_CODES: tuple[str, ...] = ("75056", "13055", "69123")
# Kept in sync manually with app.demographie.backfill.PLM_ARRONDISSEMENT_PREFIXES
# (this script intentionally has no runtime dependency on the app package so
# it stays a standalone, minimal-dependency read-only tool).
PLM_ARRONDISSEMENT_PREFIXES: tuple[str, ...] = ("751", "132", "6938")


@dataclass
class PreflightReport:
    """Aggregated, redacted result -- never carries raw geometry."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    coverage_pct: float | None = None
    plm_missing_codes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"status: {'OK' if self.ok else 'BLOCKED'}"]
        if self.coverage_pct is not None:
            lines.append(f"coverage_pct: {self.coverage_pct:.2f}")
        lines.append("counts (aggregated, no geometry):")
        for key, value in sorted(self.counts.items()):
            lines.append(f"  {key}: {value}")
        lines.append(f"plm_missing_codes: {self.plm_missing_codes}")
        if self.reasons:
            lines.append("reasons:")
            lines.extend(f"  - {reason}" for reason in self.reasons)
        return "\n".join(lines)


def _arrondissement_predicate(code_insee_expr: str) -> str:
    ors = " OR ".join(
        f"{code_insee_expr} LIKE '{prefix}%'" for prefix in PLM_ARRONDISSEMENT_PREFIXES
    )
    return f"({ors})"


def _check_coverage(
    cur: psycopg.Cursor,  # type: ignore[type-arg]
    min_coverage_pct: float | None,
) -> tuple[dict[str, int], float, list[str]]:
    arr = _arrondissement_predicate("d.code_insee")

    cur.execute(
        f"""
        SELECT count(*) FROM demographie d
        WHERE d.annee = (SELECT MAX(annee) FROM demographie)
          AND NOT {arr}
        """
    )
    (total,) = cur.fetchone()

    cur.execute(
        f"""
        SELECT count(*) FROM demographie d
        JOIN geo_communes g ON g.code = d.code_insee
        WHERE d.annee = (SELECT MAX(annee) FROM demographie)
          AND NOT {arr}
        """
    )
    (matched,) = cur.fetchone()

    cur.execute(
        f"""
        SELECT count(*) FROM demographie d
        JOIN geo_communes g ON g.code = d.code_insee
        WHERE d.annee = (SELECT MAX(annee) FROM demographie)
          AND NOT {arr}
          AND (NOT ST_IsValid(g.geometry) OR ST_IsEmpty(g.geometry))
        """
    )
    (invalid,) = cur.fetchone()

    cur.execute(
        f"""
        SELECT count(*) FROM demographie d
        JOIN geo_communes g ON g.code = d.code_insee
        WHERE d.annee = (SELECT MAX(annee) FROM demographie)
          AND NOT {arr}
          AND ST_IsValid(g.geometry) AND NOT ST_IsEmpty(g.geometry)
          AND ST_Area(g.geometry::geography) <= 0
        """
    )
    (nonpositive,) = cur.fetchone()

    counts = {
        "total_communes_hors_arrondissements": total,
        "matched_geometry": matched,
        "missing_geometry": total - matched,
        "invalid_or_empty_geometry": invalid,
        "nonpositive_area": nonpositive,
    }
    coverage_pct = (matched / total * 100.0) if total else 0.0

    errors: list[str] = []
    if invalid:
        errors.append(
            f"{invalid} geometrie(s) administrative(s) invalide(s)/vide(s) "
            "detectee(s) -- backfill refuse tant qu'elles ne sont pas "
            "corrigees"
        )
    if nonpositive:
        errors.append(f"{nonpositive} geometrie(s) a surface calculee non positive")
    if min_coverage_pct is None:
        errors.append(
            "seuil de couverture non explicitement accepte -- passer "
            "--min-coverage-pct pour autoriser le backfill (D-09)"
        )
    elif coverage_pct < min_coverage_pct:
        errors.append(
            f"couverture {coverage_pct:.2f}% < seuil accepte {min_coverage_pct:.2f}%"
        )

    return counts, coverage_pct, errors


def _check_plm(
    cur: psycopg.Cursor,  # type: ignore[type-arg]
) -> tuple[list[str], list[str]]:
    """Only validate PLM codes the latest demographie millesime actually
    references -- a dataset deliberately scoped to a subset of France
    (e.g. a single-department disposable fixture) never claims to cover
    Paris/Lyon/Marseille, so it must not be blocked on their absence."""
    cur.execute(
        "SELECT DISTINCT code_insee FROM demographie "
        "WHERE annee = (SELECT MAX(annee) FROM demographie) "
        "AND code_insee = ANY(%s)",
        (list(PLM_GLOBAL_CODES),),
    )
    referenced = sorted(row[0] for row in cur.fetchall())
    if not referenced:
        return [], []

    cur.execute(
        "SELECT code FROM geo_communes WHERE code = ANY(%s)",
        (referenced,),
    )
    present = {row[0] for row in cur.fetchall()}
    missing = sorted(set(referenced) - present)

    errors: list[str] = []
    if missing:
        errors.append(
            f"code(s) PLM global(aux) reference(s) par demographie mais "
            f"absent(s) de geo_communes: {missing} -- granularite ambigue, "
            "cette ville resterait sans surface administrative"
        )
        return missing, errors

    cur.execute(
        "SELECT code FROM geo_communes "
        "WHERE code = ANY(%s) "
        "AND (NOT ST_IsValid(geometry) OR ST_IsEmpty(geometry))",
        (referenced,),
    )
    bad = sorted(row[0] for row in cur.fetchall())
    if bad:
        errors.append(f"code(s) PLM avec geometrie invalide/vide: {bad}")

    return missing, errors


def run_preflight(
    dsn: str, *, min_coverage_pct: float | None = None
) -> PreflightReport:
    """Read-only inspection of demographie/geo_communes geometric coverage.

    Opens the connection with ``read_only=True`` (psycopg3) so PostgreSQL
    itself rejects any write attempt at the wire protocol level.
    """
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            counts, coverage_pct, coverage_errors = _check_coverage(
                cur, min_coverage_pct
            )
            plm_missing, plm_errors = _check_plm(cur)

    reasons = [*coverage_errors, *plm_errors]
    return PreflightReport(
        ok=not reasons,
        reasons=reasons,
        counts=counts,
        coverage_pct=coverage_pct,
        plm_missing_codes=plm_missing,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="DSN Postgres (sync)")
    parser.add_argument(
        "--min-coverage-pct",
        type=float,
        default=None,
        help=(
            "Seuil de couverture geometrique explicitement accepte (0-100). "
            "Sans cette option le preflight bloque toujours (D-09)."
        ),
    )
    args = parser.parse_args(argv)

    report = run_preflight(args.dsn, min_coverage_pct=args.min_coverage_pct)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
