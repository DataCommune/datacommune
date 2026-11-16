"""Fixtures for tests/integration/ — scoped to this directory only.

Wave 0 harness (Phase 43, plan 43-01): every test under tests/integration/
runs against a real, disposable PostgreSQL/PostGIS container provisioned
and destroyed by scripts/run_contract_integration.ps1. Nothing here talks
to a developer's persistent DB or to production — CONTRACT_TEST_DATABASE_URL
is the only source of truth for where to connect, and it is only ever set
by the wrapper script (see run_contract_integration.ps1 docstring).

Because this conftest.py lives under tests/integration/, pytest only wires
these fixtures into tests placed in this directory tree — the rest of the
suite (unit/smoke/etc.) is unaffected.

Running `pytest tests/integration/` directly (without the wrapper script)
skips before connecting when the harness was not started. This includes the
cadastre geometry gates in this directory; they never fall back to the
application DSN. Once the harness is configured, connection errors fail tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config as AlembicConfig

_ENV_VAR = "CONTRACT_TEST_DATABASE_URL"

# T-43-01: only ever accept a DSN pointing at a disposable local/CI harness.
# This mirrors the allow-list in run_contract_integration.ps1 — belt and
# braces, since a stray DATABASE_URL in the caller's shell must never be
# picked up here.
_ALLOWED_HOSTS = ("127.0.0.1", "localhost")

# tests/integration/conftest.py -> tests/integration -> tests -> backend
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _read_test_dsn() -> str:
    """Read + validate CONTRACT_TEST_DATABASE_URL, or skip/fail loudly.

    Missing var -> skip (harness not started, e.g. plain `pytest`/`make test`).
    Present but disallowed host -> hard fail (T-43-01: never silently ignore
    a DSN that could point somewhere other than the disposable container).
    """
    dsn = os.environ.get(_ENV_VAR)
    if not dsn:
        pytest.skip(
            f"{_ENV_VAR} non defini — lancer via "
            "scripts/run_contract_integration.ps1 pour provisionner le "
            "PostGIS jetable requis par tests/integration/."
        )
    host = dsn.split("@", 1)[-1].split(":", 1)[0].split("/", 1)[0]
    if host not in _ALLOWED_HOSTS:
        pytest.fail(
            f"T-43-01: hostname '{host}' hors allow-list {_ALLOWED_HOSTS} — "
            f"{_ENV_VAR} doit cibler un conteneur jetable local."
        )
    return dsn


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Sync psycopg DSN for the disposable contract-test PostGIS instance."""
    async_dsn = _read_test_dsn()
    return async_dsn.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture()
def pg_conn(pg_dsn: str) -> Iterator[psycopg.Connection]:
    """A psycopg connection scoped to a single test.

    Rolled back and closed on teardown so nothing a test writes survives it
    — the connection itself is destroyed with the container at the end of
    the run regardless (see run_contract_integration.ps1's `finally` block).
    """
    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def alembic_config(pg_dsn: str) -> AlembicConfig:
    """Alembic Config bound to the disposable DB.

    Consumed by later Phase 43 plans (e.g. the fiscalite percent-points
    migration test) that need to run real `upgrade`/`downgrade` against
    PostGIS. Wave 0 only proves the wiring resolves (see
    test_postgis_harness.py) — it does not run the full migration chain.
    """
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", pg_dsn)
    return cfg


@pytest.fixture(scope="module")
def isolated_pg_dsn() -> Iterator[str]:
    """Own a child database in the wrapper's disposable container.

    Modules exercising destructive public-schema DDL cannot share the migrated
    public schema with unrelated suites. Never read DATABASE_URL here.
    """
    from urllib.parse import urlsplit, urlunsplit
    from uuid import uuid4

    from psycopg import sql

    parent = _read_test_dsn().replace("postgresql+asyncpg://", "postgresql://")
    database = "contract_module_" + uuid4().hex
    parts = urlsplit(parent)
    child = urlunsplit(parts._replace(path="/" + database))
    with psycopg.connect(parent, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        with psycopg.connect(child, connect_timeout=5) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
        yield child
    finally:
        # Only the exact UUID database created above is ever dropped.
        with psycopg.connect(parent, autocommit=True, connect_timeout=5) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


@pytest.fixture()
def instance_scope():
    """Factory: write a scope into the database *dsn* points at (D-21).

    Goes through ``replace_scope`` so tests exercise the production write path,
    including on an isolated child database.
    """
    from app.scope import replace_scope

    def write(dsn: str, datasets, departements):
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            return replace_scope(conn, datasets, departements)

    return write


@pytest.fixture()
def full_instance_scope(instance_scope):
    """Factory: host the whole catalogue (minus ``commune``) on 102 departements.

    Same scope as the loader ``tests/conftest.py`` substitutes for in-process
    runners; used where a subprocess (``run_etl.py``) reads the real table.
    """
    from app.common.source_catalog import DATASET_CATALOG
    from app.etl.base import DEPARTEMENTS
    from app.scope import SCOPE_EXCLUDED_KEYS

    def write(dsn: str):
        return instance_scope(
            dsn, sorted(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS), DEPARTEMENTS
        )

    return write
