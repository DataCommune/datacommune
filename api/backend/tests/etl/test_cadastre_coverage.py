"""Cadastre department coverage on its flat staging table (plan 02-21).

The 02-11 import failed with ``UndefinedColumn code_departement``: the
coverage guard fell back to ``code_departement`` on ``_cadastre_staging_flat``,
which only holds ``commune``. The pipeline now declares the departement
expression of that table. The runner's own SQL is evaluated here on an
in-memory SQLite table with the same single column, so the expression is run,
not compared as text.
"""

import json
import re
import sqlite3
from unittest.mock import MagicMock

import pytest

from app.etl.runner import PipelineRunner
from app.etl_cadastre.pipeline import CadastrePipeline
from app.scope import InstanceScope

pytestmark = pytest.mark.unit


def _sqlite_with_postgres_left() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    # PostgreSQL LEFT(text, n) for n >= 0; NULL in, NULL out.
    conn.create_function("LEFT", 2, lambda text, n: None if text is None else text[:n])
    return conn


class _SqliteFlatStaging:
    """psycopg-like connection over SQLite: `= ANY(%s)` becomes `IN json_each`."""

    def __init__(self, communes):
        self.conn = _sqlite_with_postgres_left()
        self.conn.execute("CREATE TABLE _cadastre_staging_flat (commune TEXT)")
        self.conn.executemany(
            "INSERT INTO _cadastre_staging_flat VALUES (?)",
            [(code,) for code in communes],
        )

    def execute(self, query, params):
        query = re.sub(r"= ANY\(%s\)", "IN (SELECT value FROM json_each(?))", query)
        return self.conn.execute(query, [json.dumps(list(p)) for p in params])


def _bound_cadastre() -> CadastrePipeline:
    pipeline = CadastrePipeline()
    pipeline.bind_scope(InstanceScope(frozenset({"cadastre"}), ("44", "85")))
    return pipeline


def _departement(expression: str, commune: str | None) -> str | None:
    conn = _sqlite_with_postgres_left()
    return conn.execute(
        f"SELECT {expression} FROM (SELECT ? AS commune)", (commune,)
    ).fetchone()[0]


def test_cadastre_declares_the_expression_of_its_flat_table():
    pipeline = CadastrePipeline()
    coverage = pipeline.coverage_departement_sql
    assert coverage is not None
    assert list(coverage) == [pipeline.flat_staging_table_name]
    assert pipeline.flat_staging_table_name == "_cadastre_staging_flat"
    # Scope filtering stays off: the download is already departmental.
    assert pipeline.scope_filter_sql is None


@pytest.mark.parametrize(
    ("commune", "expected"),
    [
        ("44109", "44"),
        ("85191", "85"),
        ("97105", "971"),
        ("97411", "974"),
        ("2A004", "2A"),
        (None, None),
    ],
)
def test_expression_yields_the_departement_of_a_commune(commune, expected):
    (expression,) = CadastrePipeline().coverage_departement_sql.values()
    assert _departement(expression, commune) == expected


def test_complete_44_85_flat_staging_passes_the_coverage_guard():
    staging = _SqliteFlatStaging(["44109", "44184", "85191", "85165"])
    pipeline = _bound_cadastre()
    error = PipelineRunner._scoped_coverage_error(
        staging, pipeline, pipeline.flat_staging_table_name, MagicMock()
    )
    assert error is None


@pytest.mark.parametrize(
    ("communes", "refused"),
    [
        (["44109", "44184"], "missing scoped departments ['85']"),
        (
            ["44109", "85191", "49007"],
            "staging contains departments outside scoped ETL ['49']",
        ),
        (
            ["44109", "85191", None],
            "staging contains departments outside scoped ETL [None]",
        ),
    ],
)
def test_incomplete_or_foreign_flat_staging_is_refused(communes, refused):
    staging = _SqliteFlatStaging(communes)
    pipeline = _bound_cadastre()
    error = PipelineRunner._scoped_coverage_error(
        staging, pipeline, pipeline.flat_staging_table_name, MagicMock()
    )
    assert error is not None
    assert refused in error
    assert "_cadastre_staging_flat -- swap aborted" in error
