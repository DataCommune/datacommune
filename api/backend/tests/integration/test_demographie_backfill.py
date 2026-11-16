"""Surface/densite backfill DB entry point (Phase 43, Plans 02 + 06).

Plan 43-02 proved the disposable PostGIS harness can migrate to a head that
carries `geo_communes` + `demographie`, and that the D-07 authoritative
formula -- ST_Area(geometry::geography) / 1_000_000 -- computes correctly on
a real PostGIS geography value, plus the ST_IsValid guard.

Plan 43-06 extends this file with the real
`app.demographie.backfill.backfill_superficie_densite` helper and the
read-only `scripts/preflight_demographie_geometry.py` CLI: order
independence (D-08), idempotence (D-08), typed absence when geometry is
missing (D-09), and the fail-closed precondition repeat when a matched
geometry is invalid (D-09).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/run_contract_integration.ps1 \
        tests/integration/test_demographie_backfill.py
"""

from __future__ import annotations

import re

import psycopg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from app.demographie.backfill import (
    BackfillPreconditionError,
    backfill_superficie_densite,
)
from scripts.preflight_demographie_geometry import run_preflight

# ~1km x 1km square near Nantes -- synthetic, not real commune data.
_SQUARE_WKT = (
    "POLYGON((-1.560 47.210, -1.545 47.210, "
    "-1.545 47.220, -1.560 47.220, -1.560 47.210))"
)
# Self-intersecting (bowtie) polygon -- ST_IsValid() is false for this shape.
_BOWTIE_WKT = "POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))"

_ANNEE = 2024


@pytest.fixture
def _schema_at_head(alembic_config: AlembicConfig) -> None:
    """Ensure the disposable DB is migrated to head.

    Idempotent no-op if already there (proven by 43-02/43-04) -- every
    test in this file depends on this instead of relying on definition
    order, since the harness is session-scoped and shared across the
    whole file.
    """
    command.upgrade(alembic_config, "head")


def _insert_geo_commune(
    pg_conn: psycopg.Connection,  # type: ignore[type-arg]
    *,
    code: str,
    wkt: str,
) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, geometry) "
            "VALUES (%s, %s, %s, ST_Multi(ST_SetSRID(ST_GeomFromText(%s), 4326))) "
            "ON CONFLICT (code) DO UPDATE SET geometry = EXCLUDED.geometry",
            (code, "Commune synthetique", code[:2], wkt),
        )
    pg_conn.commit()


def _insert_demographie(
    pg_conn: psycopg.Connection,  # type: ignore[type-arg]
    *,
    code_insee: str,
    population: int | None,
    annee: int = _ANNEE,
) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO demographie "
            "(code_insee, nom_commune, code_departement, annee, population) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (code_insee, annee) DO UPDATE SET "
            "population = EXCLUDED.population",
            (code_insee, "Commune synthetique", code_insee[:2], annee, population),
        )
    pg_conn.commit()


def _cleanup(
    pg_conn: psycopg.Connection,  # type: ignore[type-arg]
    code_insee_values: tuple[str, ...],
) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM demographie WHERE code_insee = ANY(%s)",
            (list(code_insee_values),),
        )
        cur.execute(
            "DELETE FROM geo_communes WHERE code = ANY(%s)",
            (list(code_insee_values),),
        )
    pg_conn.commit()


# -- Wave 0/1 scaffold (Plan 43-02) — unchanged -------------------------------


def test_geo_communes_and_demographie_columns_exist_after_upgrade(
    alembic_config: AlembicConfig, pg_dsn: str
) -> None:
    """geo_communes.geometry and demographie.superficie/densite exist."""
    command.upgrade(alembic_config, "head")

    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'geo_communes' AND column_name = 'geometry'"
            )
            assert cur.fetchone() is not None, "geo_communes.geometry missing"

            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'demographie' "
                "AND column_name IN ('superficie', 'densite')"
            )
            columns = {row[0] for row in cur.fetchall()}
        assert columns == {"superficie", "densite"}
    finally:
        conn.close()


def test_administrative_area_formula_computes_km2_from_geography(
    pg_conn: psycopg.Connection,
) -> None:
    """D-07 formula on a real geometry: ST_Area(geometry::geography)/1e6."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ST_Area("
            "  ST_GeomFromText("
            f"    '{_SQUARE_WKT}', 4326"
            "  )::geography"
            ") / 1000000.0"
        )
        (area_km2,) = cur.fetchone()
    assert 0.5 < area_km2 < 3.0


def test_invalid_geometry_is_detected_by_st_isvalid(
    pg_conn: psycopg.Connection,
) -> None:
    """A self-intersecting (bowtie) polygon fails ST_IsValid."""
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT ST_IsValid(ST_GeomFromText('{_BOWTIE_WKT}', 4326))")
        (is_valid,) = cur.fetchone()
    assert is_valid is False


# -- backfill_superficie_densite (Plan 43-06, task 1) -------------------------


def test_backfill_computes_administrative_area_and_density(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    code = "44901"
    _insert_geo_commune(pg_conn, code=code, wkt=_SQUARE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=1500)
    try:
        counters = backfill_superficie_densite(pg_conn, code_insee=code)

        assert counters.rows_examined == 1
        assert counters.rows_updated == 1
        assert counters.geometry_missing == 0
        assert counters.geometry_invalid_or_empty == 0
        assert counters.population_missing == 0

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT superficie, densite FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code, _ANNEE),
            )
            superficie, densite = cur.fetchone()
        assert 0.5 < float(superficie) < 3.0
        assert float(densite) == pytest.approx(1500 / float(superficie), rel=0.01)
    finally:
        _cleanup(pg_conn, (code,))


def test_backfill_typed_absence_when_geometry_missing(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    """A commune with no geo_communes match keeps superficie/densite NULL --
    a deterministic, counted absence (D-09), never a silent gap."""
    code = "44902"
    _insert_demographie(pg_conn, code_insee=code, population=42)
    try:
        counters = backfill_superficie_densite(pg_conn, code_insee=code)

        assert counters.rows_updated == 0
        assert counters.geometry_missing == 1

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT superficie, densite FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code, _ANNEE),
            )
            superficie, densite = cur.fetchone()
        assert superficie is None
        assert densite is None
    finally:
        _cleanup(pg_conn, (code,))


def test_backfill_raises_on_invalid_geometry_precondition(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    """An invalid matched geometry blocks the whole call before any write
    (D-09) -- superficie/densite are never touched."""
    code = "44903"
    _insert_geo_commune(pg_conn, code=code, wkt=_BOWTIE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=42)
    try:
        with pytest.raises(BackfillPreconditionError, match="invalide"):
            backfill_superficie_densite(pg_conn, code_insee=code)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT superficie, densite FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code, _ANNEE),
            )
            superficie, densite = cur.fetchone()
        assert superficie is None
        assert densite is None
    finally:
        _cleanup(pg_conn, (code,))


def test_backfill_is_idempotent_across_reruns(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    code = "44904"
    _insert_geo_commune(pg_conn, code=code, wkt=_SQUARE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=800)
    try:
        first = backfill_superficie_densite(pg_conn, code_insee=code)
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT superficie, densite FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code, _ANNEE),
            )
            first_values = cur.fetchone()

        second = backfill_superficie_densite(pg_conn, code_insee=code)
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT superficie, densite FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code, _ANNEE),
            )
            second_values = cur.fetchone()

        assert first.rows_updated == second.rows_updated == 1
        assert first_values == second_values
    finally:
        _cleanup(pg_conn, (code,))


def test_backfill_order_independent_across_producer_sequencing(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    """D-08: geometry-then-demographie and demographie-then-geometry must
    converge on the same values once both sources are present -- the
    UPDATE always joins the current state of both tables."""
    code_geo_first = "44905"
    code_demo_first = "44906"
    try:
        # Order A: geo_communes inserted before demographie.
        _insert_geo_commune(pg_conn, code=code_geo_first, wkt=_SQUARE_WKT)
        _insert_demographie(pg_conn, code_insee=code_geo_first, population=1000)
        backfill_superficie_densite(pg_conn, code_insee=code_geo_first)

        # Order B: demographie inserted before geo_communes (same geometry
        # and population, different code so results are directly comparable).
        _insert_demographie(pg_conn, code_insee=code_demo_first, population=1000)
        _insert_geo_commune(pg_conn, code=code_demo_first, wkt=_SQUARE_WKT)
        backfill_superficie_densite(pg_conn, code_insee=code_demo_first)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT superficie, densite FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code_geo_first, _ANNEE),
            )
            values_a = cur.fetchone()
            cur.execute(
                "SELECT superficie, densite FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code_demo_first, _ANNEE),
            )
            values_b = cur.fetchone()

        assert values_a == values_b
        assert values_a[0] is not None
    finally:
        _cleanup(pg_conn, (code_geo_first, code_demo_first))


def test_backfill_plm_global_code_gets_administrative_area(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    """The PLM global code (75056 Paris) gets an administrative area like
    any other commune -- the backfill has no PLM special-casing of its
    own, it relies on geo_communes already carrying the global boundary."""
    code = "75056"
    _insert_geo_commune(pg_conn, code=code, wkt=_SQUARE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=2_100_000)
    try:
        counters = backfill_superficie_densite(pg_conn, code_insee=code)
        assert counters.rows_updated == 1

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT superficie FROM demographie "
                "WHERE code_insee = %s AND annee = %s",
                (code, _ANNEE),
            )
            (superficie,) = cur.fetchone()
        assert superficie is not None and float(superficie) > 0
    finally:
        _cleanup(pg_conn, (code,))


# -- preflight_demographie_geometry (Plan 43-06, task 1) ----------------------


def test_preflight_blocks_without_explicit_min_coverage_pct(
    _schema_at_head: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    code = "44907"
    _insert_geo_commune(pg_conn, code=code, wkt=_SQUARE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=100)
    try:
        report = run_preflight(pg_dsn)
        assert not report.ok
        assert any("seuil" in reason for reason in report.reasons)
    finally:
        _cleanup(pg_conn, (code,))


def test_preflight_ok_when_geometry_valid_and_threshold_explicitly_met(
    _schema_at_head: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    code = "44908"
    _insert_geo_commune(pg_conn, code=code, wkt=_SQUARE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=100)
    try:
        report = run_preflight(pg_dsn, min_coverage_pct=50.0)
        assert report.ok, report.reasons
        assert report.coverage_pct == pytest.approx(100.0)
    finally:
        _cleanup(pg_conn, (code,))


def test_preflight_blocks_on_invalid_geometry(
    _schema_at_head: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    code = "44909"
    _insert_geo_commune(pg_conn, code=code, wkt=_BOWTIE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=100)
    try:
        report = run_preflight(pg_dsn, min_coverage_pct=0.0)
        assert not report.ok
        assert any("invalide" in reason for reason in report.reasons)
    finally:
        _cleanup(pg_conn, (code,))


def test_preflight_blocks_on_missing_plm_global_code(
    _schema_at_head: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    """A demographie row for 75056 with no matching geo_communes entry
    trips the dedicated PLM check (in addition to the generic coverage
    gap) -- Paris/Lyon/Marseille must never be silently ambiguous."""
    code = "75056"
    _insert_demographie(pg_conn, code_insee=code, population=100)
    try:
        report = run_preflight(pg_dsn, min_coverage_pct=0.0)
        assert not report.ok
        assert code in report.plm_missing_codes
        assert any("PLM" in reason for reason in report.reasons)
    finally:
        _cleanup(pg_conn, (code,))


def test_preflight_never_issues_a_write_statement(
    _schema_at_head: None,
    pg_conn: psycopg.Connection,
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI is SELECT-only: spy on every cursor.execute() it issues and
    assert none of them is a write statement (mirrors
    scripts/preflight_semantic_contracts.py's own test, 43-04)."""
    code = "44910"
    _insert_geo_commune(pg_conn, code=code, wkt=_SQUARE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=100)
    try:
        executed: list[str] = []
        original_execute = psycopg.Cursor.execute

        def _spy_execute(
            self: psycopg.Cursor,  # type: ignore[type-arg]
            query: object,
            *args: object,
            **kwargs: object,
        ):
            executed.append(query if isinstance(query, str) else str(query))
            return original_execute(self, query, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(psycopg.Cursor, "execute", _spy_execute)

        run_preflight(pg_dsn, min_coverage_pct=50.0)

        assert executed, "preflight n'a exécuté aucune requête"
        forbidden = re.compile(
            r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE)\b",
            re.IGNORECASE,
        )
        violations = [q for q in executed if forbidden.search(q)]
        assert not violations, f"preflight a émis des écritures: {violations}"
    finally:
        _cleanup(pg_conn, (code,))


# -- DemographieAuditor (Plan 43-06, task 2 acceptance criteria) -------------
#
# Proves the two acceptance-criteria claims from 43-06-02 directly against
# real PostGIS: the checker ERRORs on a commune covered by a valid
# administrative geometry but never backfilled (D-09), and still WARNs on a
# densite that doesn't match population/superficie (unchanged behaviour,
# now referencing the backfill helper instead of the removed cadastral
# formula in its description).


def test_checker_flags_commune_covered_by_geometry_but_superficie_null(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    from app.data_quality.checkers.demographie import DemographieAuditor

    code = "44911"
    _insert_geo_commune(pg_conn, code=code, wkt=_SQUARE_WKT)
    _insert_demographie(pg_conn, code_insee=code, population=500)
    # Deliberately do NOT call backfill_superficie_densite -- superficie
    # stays NULL despite a valid geo_communes match.
    try:
        result = DemographieAuditor().run(pg_conn)
        error_titles = [f.title for f in result.errors]
        assert any("superficie" in t.lower() for t in error_titles), error_titles
    finally:
        _cleanup(pg_conn, (code,))


def test_checker_flags_densite_incoherent_with_population_and_superficie(
    _schema_at_head: None, pg_conn: psycopg.Connection
) -> None:
    from app.data_quality.checkers.demographie import DemographieAuditor

    code = "44912"
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO demographie "
            "(code_insee, nom_commune, code_departement, annee, "
            " population, superficie, densite) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (code, "Commune synthetique", code[:2], _ANNEE, 1000, 10.0, 999.0),
        )
    pg_conn.commit()
    try:
        result = DemographieAuditor().run(pg_conn)
        warn_titles = [f.title for f in result.warnings]
        assert any("densite" in t.lower() for t in warn_titles), warn_titles
    finally:
        _cleanup(pg_conn, (code,))
