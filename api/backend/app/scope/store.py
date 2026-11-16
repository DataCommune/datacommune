"""Read and replace the hosted instance scope stored in the database."""

from collections.abc import Iterable

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.source_catalog import DATASET_CATALOG
from app.etl.base import DEPARTEMENTS
from app.scope.model import SCOPE_EXCLUDED_KEYS, InstanceScope, sort_departements

# One statement reads both tables from the same snapshot, whatever the
# caller's isolation level: a concurrent replace_scope is seen whole or not.
_SCOPE_SQL = (
    "SELECT 'dataset', dataset FROM instance_scope_datasets "
    "UNION ALL "
    "SELECT 'departement', code_departement FROM instance_scope_departements"
)


def _build(datasets: Iterable[str], departements: Iterable[str]) -> InstanceScope:
    return InstanceScope(
        datasets=frozenset(datasets),
        departements=tuple(sort_departements(set(departements))),
    )


def _from_rows(rows: Iterable[tuple[str, str]]) -> InstanceScope:
    rows = list(rows)
    return _build(
        (value for kind, value in rows if kind == "dataset"),
        (value for kind, value in rows if kind == "departement"),
    )


def load_scope_sync(conn: psycopg.Connection) -> InstanceScope:
    return _from_rows(conn.execute(_SCOPE_SQL).fetchall())


async def load_scope(db: AsyncSession) -> InstanceScope:
    result = await db.execute(text(_SCOPE_SQL))
    return _from_rows((row[0], row[1]) for row in result.all())


def validate_scope(
    datasets: Iterable[str], departements: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Return sorted, deduplicated keys and codes; refuse unknown values.

    Needs no database: callers can refuse bad input before connecting.
    """
    wanted_datasets = sorted(set(datasets))
    wanted_departements = sort_departements(_validated_departements(set(departements)))
    allowed = set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS
    unknown = [key for key in wanted_datasets if key not in allowed]
    if unknown:
        raise ValueError(
            f"Datasets outside the hostable catalogue: {unknown} "
            f"(excluded synthesis keys: {sorted(SCOPE_EXCLUDED_KEYS)})"
        )
    return wanted_datasets, wanted_departements


def replace_scope(
    conn: psycopg.Connection,
    datasets: Iterable[str],
    departements: Iterable[str],
) -> InstanceScope:
    """Validate, then replace the whole scope in a single transaction.

    Readers see either the previous scope or the new one, never a mix.
    """
    wanted_datasets, wanted_departements = validate_scope(datasets, departements)
    with conn.transaction():
        conn.execute("DELETE FROM instance_scope_datasets")
        conn.execute("DELETE FROM instance_scope_departements")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO instance_scope_datasets (dataset) VALUES (%s)",
                [(key,) for key in wanted_datasets],
            )
            cur.executemany(
                "INSERT INTO instance_scope_departements (code_departement) "
                "VALUES (%s)",
                [(code,) for code in wanted_departements],
            )
    return _build(wanted_datasets, wanted_departements)


def _validated_departements(codes: set[str]) -> set[str]:
    invalid = sorted(code for code in codes if code not in DEPARTEMENTS)
    if invalid:
        raise ValueError(f"Unknown departement codes: {invalid}")
    return codes
