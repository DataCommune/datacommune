"""Hosted scope for integration tests that mount a route serving a source.

Every route of the inventory reads the scope stored in the database before any
other read (DATA-03). A test mounting one writes the scope it exercises through
the production path (``replace_scope``); it never overrides the scope
dependency of the API nor relies on a permissive fallback.
"""

from collections.abc import Iterable

import psycopg

from app.common.source_catalog import DATASET_CATALOG, RESOLVER_SOURCE_MAP
from app.etl.base import DEPARTEMENTS
from app.scope.model import SCOPE_EXCLUDED_KEYS, InstanceScope
from app.scope.store import replace_scope

# The two tables of migration 097_instance_scope, for databases built without
# the migration chain (``isolated_pg_dsn``); same columns and checks.
_CODES = ", ".join(f"'{code}'" for code in DEPARTEMENTS)
_SCOPE_TABLES_DDL = f"""
    CREATE TABLE IF NOT EXISTS instance_scope_datasets (
        dataset TEXT PRIMARY KEY CHECK (dataset ~ '^[a-z_]+$'),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS instance_scope_departements (
        code_departement TEXT PRIMARY KEY CHECK (code_departement IN ({_CODES})),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""

# The thirteen sources of the commune synthesis.
COMMUNE_SOURCES: tuple[str, ...] = tuple(RESOLVER_SOURCE_MAP["commune"])
# Every hostable catalogue key, for tests that walk many routes.
HOSTABLE_KEYS: tuple[str, ...] = tuple(
    sorted(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS)
)


def install_instance_scope(
    conn: psycopg.Connection,
    datasets: Iterable[str],
    departements: Iterable[str],
) -> InstanceScope:
    """Create the scope tables when missing, then replace the whole scope."""
    conn.execute(_SCOPE_TABLES_DDL)
    conn.commit()
    return replace_scope(conn, datasets, departements)


def install_scope_at(
    dsn: str, datasets: Iterable[str], departements: Iterable[str]
) -> InstanceScope:
    """:func:`install_instance_scope` on its own connection to *dsn*."""
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        return install_instance_scope(conn, datasets, departements)
