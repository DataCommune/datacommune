"""Idempotent, order-independent superficie/densite backfill (DATA-03, D-07..D-09).

Computes the D-07 authoritative administrative area -- ``ST_Area(geo_communes.
geometry::geography) / 1_000_000`` -- for every ``demographie`` row at the
latest recensement millesime, then derives ``densite = population /
superficie`` whenever both are available and superficie is strictly
positive.

The former cadastral-sum formula
(``etl_demographie.pipeline.DemographiePipeline._compute_densite_from_cadastre``)
has been removed as the authoritative source: a cadastral contenance sum is
not an administrative boundary, and D-07 forbids presenting it as one. This
helper carries no cadastral fallback field -- communes without a valid
administrative geometry match simply keep ``superficie``/``densite`` NULL, a
deterministic and *typed* absence (see the ``geometry_missing`` /
``geometry_invalid_or_empty`` counters below). ``app/data_quality/checkers/
demographie.py`` surfaces the same gap as a documented finding instead of a
silent empty value, satisfying D-09.

Order independence (D-08): the UPDATE always joins the *current* state of
``demographie`` and ``geo_communes``, so it produces the same result whether
called after the demographie ETL swap or after ``import_geo_boundaries.py``
-- whichever ran last simply sees the other table already populated.

Idempotence (D-08): re-running with unchanged inputs recomputes the exact
same values (``ROUND`` is deterministic) -- this is a plain UPDATE, not an
incremental/additive operation.

Callers MUST run this inside a transaction they own (the caller commits) so
the backfill and the dependent ``v_commune_synthese`` rebuild land as one
logical operation. See ``app/etl/hooks.py::DependentMatviewHook`` (demographie
ETL path) and ``scripts/import_geo_boundaries.py`` (geometry import path).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

# Arrondissement-level INSEE codes present in `demographie` (the source CSV
# is not filtered) but absent from `geo_communes` (geo.api.gouv.fr only
# serves the global commune boundary for Paris/Lyon/Marseille, never
# individual arrondissements). These rows are out of administrative-geometry
# scope by construction, not a coverage gap -- the PLM *aggregate* codes
# (75056/13055/69123, inserted by
# etl_demographie.pipeline.DemographiePipeline._insert_plm_aggregates) are
# the ones expected to carry an administrative superficie/densite.
PLM_ARRONDISSEMENT_PREFIXES: tuple[str, ...] = ("751", "132", "6938")


class BackfillPreconditionError(RuntimeError):
    """Raised when the in-transaction precondition repeat fails.

    Mirrors the preflight/migration double-check pattern established by
    ``scripts/preflight_semantic_contracts.py`` +
    ``migrations/versions/073_semantic_contracts.py`` (Plan 43-04): the
    read-only ``scripts/preflight_demographie_geometry.py`` is the fast,
    human-facing check; this exception is the in-transaction repeat that
    actually closes the TOCTOU window between "preflight said OK" and this
    call's own write.
    """


@dataclass(frozen=True)
class BackfillCounters:
    """Observable counters from one backfill call -- never raw geometry."""

    rows_examined: int
    rows_updated: int
    geometry_missing: int
    geometry_invalid_or_empty: int
    population_missing: int


def _arrondissement_predicate(code_insee_expr: str) -> str:
    """SQL predicate fragment: true when *code_insee_expr* is a PLM
    arrondissement code (out of geo_communes scope by construction)."""
    ors = " OR ".join(
        f"{code_insee_expr} LIKE '{prefix}%'" for prefix in PLM_ARRONDISSEMENT_PREFIXES
    )
    return f"({ors})"


def backfill_superficie_densite(
    conn: psycopg.Connection[Any],
    *,
    code_insee: str | None = None,
) -> BackfillCounters:
    """Recompute ``demographie.superficie``/``densite`` from administrative
    geometry for the latest recensement millesime.

    Args:
        conn: an open connection whose transaction the caller owns -- this
            function never commits.
        code_insee: restrict to a single commune (used by targeted repair
            tooling); ``None`` backfills every commune at the latest annee.

    Raises:
        BackfillPreconditionError: if any matched administrative geometry is
            invalid or empty -- the whole call aborts before writing
            anything (D-09: never publish a value computed on top of a
            geometry known to be broken).
    """
    params: dict[str, str] = {}
    scope_sql = ""
    if code_insee is not None:
        scope_sql = "AND d.code_insee = %(code_insee)s"
        params["code_insee"] = code_insee

    with conn.cursor() as cur:
        # -- Precondition repeat (closes the TOCTOU window) -----------------
        # Any matched geometry that is invalid or empty must block the
        # write entirely: scripts/preflight_demographie_geometry.py is the
        # human-facing read-only check; this is the authoritative repeat
        # that actually runs right before the mutation.
        cur.execute(
            f"""
            SELECT count(*) FROM demographie d
            JOIN geo_communes g ON g.code = d.code_insee
            WHERE d.annee = (SELECT MAX(annee) FROM demographie)
              {scope_sql}
              AND (NOT ST_IsValid(g.geometry) OR ST_IsEmpty(g.geometry))
            """,
            params,
        )
        (invalid_count,) = cur.fetchone()
        if invalid_count:
            msg = (
                f"{invalid_count} geometrie(s) administrative(s) invalide(s) "
                "ou vide(s) detectee(s) juste avant le backfill -- refus "
                "d'ecrire (D-09). Rejouer "
                "scripts/preflight_demographie_geometry.py avant de "
                "corriger geo_communes."
            )
            raise BackfillPreconditionError(msg)

        # -- Examined scope (for the returned counters) ----------------------
        cur.execute(
            f"""
            SELECT count(*) FROM demographie d
            WHERE d.annee = (SELECT MAX(annee) FROM demographie)
              {scope_sql}
            """,
            params,
        )
        (rows_examined,) = cur.fetchone()

        # -- Authoritative D-07 formula ---------------------------------------
        cur.execute(
            f"""
            UPDATE demographie d
            SET superficie = ROUND(
                    (ST_Area(g.geometry::geography) / 1000000.0)::numeric, 2
                ),
                densite = CASE
                    WHEN d.population IS NOT NULL
                         AND ST_Area(g.geometry::geography) > 0
                    THEN ROUND(
                        d.population::numeric
                        / (ST_Area(g.geometry::geography) / 1000000.0)::numeric,
                        1
                    )
                    ELSE NULL
                END
            FROM geo_communes g
            WHERE g.code = d.code_insee
              AND d.annee = (SELECT MAX(annee) FROM demographie)
              AND ST_IsValid(g.geometry)
              AND NOT ST_IsEmpty(g.geometry)
              {scope_sql}
            """,
            params,
        )
        rows_updated = cur.rowcount

        # -- Typed absence counters (D-09) -------------------------------------
        cur.execute(
            f"""
            SELECT count(*) FROM demographie d
            WHERE d.annee = (SELECT MAX(annee) FROM demographie)
              {scope_sql}
              AND NOT EXISTS (
                  SELECT 1 FROM geo_communes g WHERE g.code = d.code_insee
              )
            """,
            params,
        )
        (geometry_missing,) = cur.fetchone()

        cur.execute(
            f"""
            SELECT count(*) FROM demographie d
            JOIN geo_communes g ON g.code = d.code_insee
            WHERE d.annee = (SELECT MAX(annee) FROM demographie)
              {scope_sql}
              AND (NOT ST_IsValid(g.geometry) OR ST_IsEmpty(g.geometry))
            """,
            params,
        )
        (geometry_invalid_or_empty,) = cur.fetchone()

        cur.execute(
            f"""
            SELECT count(*) FROM demographie d
            JOIN geo_communes g ON g.code = d.code_insee
            WHERE d.annee = (SELECT MAX(annee) FROM demographie)
              {scope_sql}
              AND ST_IsValid(g.geometry) AND NOT ST_IsEmpty(g.geometry)
              AND d.population IS NULL
            """,
            params,
        )
        (population_missing,) = cur.fetchone()

    return BackfillCounters(
        rows_examined=rows_examined,
        rows_updated=rows_updated,
        geometry_missing=geometry_missing,
        geometry_invalid_or_empty=geometry_invalid_or_empty,
        population_missing=population_missing,
    )
