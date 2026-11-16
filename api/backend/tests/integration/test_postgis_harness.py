"""Wave 0 smoke test for the disposable PostGIS harness (Phase 43, 43-01).

Proves the three must_haves of run_contract_integration.ps1:
1. PostGIS is real and usable (spatial extension + a real spatial query).
2. A rolled-back transaction leaves no residue (DDL is transactional here).
3. The Alembic wiring the harness exposes to later plans actually resolves.

Cleanup (the `down -v --remove-orphans` in a `finally` block) is proven by
the wrapper script itself, not by this file — see
scripts/run_contract_integration.ps1.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/run_contract_integration.ps1 \
        tests/integration/test_postgis_harness.py
"""

from __future__ import annotations

import psycopg
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory


def test_postgis_extension_is_real(pg_conn: psycopg.Connection) -> None:
    """PostGIS is installed and can run an actual spatial computation."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT PostGIS_Version()")
        (version,) = cur.fetchone()
        assert version

        # Real spatial computation, not just "extension exists": distance in
        # meters between two known points on a geography, tolerant range.
        cur.execute(
            "SELECT ST_Distance("
            "  ST_GeogFromText('SRID=4326;POINT(-1.5536 47.2184)'),"  # Nantes
            "  ST_GeogFromText('SRID=4326;POINT(-1.4267 46.6705)')"  # La Roche-sur-Yon
            ")"
        )
        (distance_m,) = cur.fetchone()
        assert 55_000 < distance_m < 65_000


def test_transaction_rollback_leaves_no_residue(pg_dsn: str) -> None:
    """A rolled-back transaction (DDL + DML) disappears entirely.

    Uses its own connection (not the pg_conn fixture) so the test controls
    exactly when rollback() fires and can observe its effect within the
    same session — proving rollback, not just fixture teardown ordering.
    """
    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE _rollback_probe (id integer)")
            cur.execute("INSERT INTO _rollback_probe (id) VALUES (1)")

        conn.rollback()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_name = '_rollback_probe'"
                ")"
            )
            (table_exists,) = cur.fetchone()
        assert table_exists is False
    finally:
        conn.close()


def test_pg_conn_fixture_is_usable(pg_conn: psycopg.Connection) -> None:
    """Sanity check that the shared pg_conn fixture itself works."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT 1")
        (result,) = cur.fetchone()
    assert result == 1


def test_alembic_config_resolves_head(alembic_config: AlembicConfig) -> None:
    """The alembic_config fixture wires script_location + DSN correctly.

    Only resolves the head revision from the versions/ directory (fast, no
    DB write) — later Phase 43 plans run real upgrade()/downgrade() against
    this same fixture for the fiscalite migration.
    """
    script = ScriptDirectory.from_config(alembic_config)
    head = script.get_current_head()
    assert head is not None


def test_pg_dsn_fixture_has_expected_scheme(pg_dsn: str) -> None:
    """pg_dsn is always the sync (psycopg) scheme, never asyncpg."""
    assert pg_dsn.startswith("postgresql://")
    assert "asyncpg" not in pg_dsn
