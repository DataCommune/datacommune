"""Execute production expressions against adversarial, synthetic SQL rows.

SQLite keeps these regressions active in CI without a database service. Only
numeric/int casts are removed; LEAST explicitly uses PostgreSQL's NULL-ignoring
semantics. Set DC_AUDIT_SQL_TEST_DSN to a loopback PostgreSQL instance to execute
the same cases with the original SQL, inside a read-only transaction. No table,
view, fixture row, ETL operation or application database setting is changed.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import sqlite3
import textwrap
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.commune.matview_ddl import CREATE_SQL, INTERMEDIATE_MATVIEWS
from app.etl_dpe.pipeline import DpePipeline

_NUMERIC_CAST = re.compile(r"::(?:numeric(?:\(\s*\d+\s*,\s*\d+\s*\))?|int)\s*")


def _least_postgres(*values: str | float | None) -> float | None:
    """PostgreSQL ignores NULL arguments, unlike SQLite's scalar min()."""
    present = [float(value) for value in values if value is not None]
    return min(present) if present else None


@pytest.fixture(params=["sqlite", "postgres"])
def sql_engine(request: pytest.FixtureRequest) -> Iterator[tuple[str, object]]:
    if request.param == "sqlite":
        conn = sqlite3.connect(":memory:")
        conn.create_function("LEAST", -1, _least_postgres)
    else:
        dsn = os.environ.get("DC_AUDIT_SQL_TEST_DSN")
        if not dsn:
            pytest.skip("optional loopback PostgreSQL read-only proof")
        try:
            host = conninfo_to_dict(dsn).get("host")
            if host not in {"127.0.0.1", "localhost", "::1"}:
                pytest.fail("SQL regression database must be loopback", pytrace=False)
            conn = psycopg.connect(
                dsn,
                connect_timeout=3,
                options="-c default_transaction_read_only=on -c statement_timeout=5000",
            )
            assert conn.execute("SHOW transaction_read_only").fetchone() == ("on",)
        except psycopg.Error:
            pytest.fail("Loopback SQL regression connection failed", pytrace=False)
    try:
        yield request.param, conn
    finally:
        conn.rollback()
        conn.close()


def _execute(sql_engine: tuple[str, object], query: str) -> list[tuple]:
    dialect, conn = sql_engine
    if dialect == "sqlite":
        query = _NUMERIC_CAST.sub(" ", query)
    assert query.lstrip().startswith(("SELECT", "WITH"))
    return conn.execute(query).fetchall()


def _select_expressions(body: str) -> list[str]:
    """Split a projection, preserving nested functions and quoted commas."""
    body = re.sub(r"--[^\n]*", "", body)
    expressions, start, depth, quoted = [], 0, 0, False
    for index, char in enumerate(body):
        if char == "'":
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                expressions.append(body[start:index].strip())
                start = index + 1
    expressions.append(body[start:].strip())
    return expressions


def _dpe_projection() -> dict[str, str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(DpePipeline.swap)))
    statements = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "INSERT INTO _dpe_incoming" in node.value
    ]
    assert len(statements) == 1
    sql = statements[0]
    columns = sql.split("(", 1)[1].split(")", 1)[0].split(",")
    body = sql.split("SELECT", 1)[1].split("FROM (", 1)[0]
    return dict(
        zip(
            (column.strip() for column in columns),
            _select_expressions(body),
            strict=True,
        )
    )


@pytest.mark.parametrize(
    ("column", "cap"),
    [
        ("surface_habitable", 999999.99),
        ("conso_5_usages_ep", 99999999.99),
        ("emission_ges_5_usages", 99999999.99),
        ("cout_total_5_usages", 9999999999.99),
    ],
)
def test_dpe_missing_values_never_become_the_cap(sql_engine, column, cap):
    expression = _dpe_projection()[column]
    values = (
        f"(1, NULL), (2, ''), (3, '0'), (4, '12.34'), (5, '{cap}'), (6, '{cap + 1}')"
    )
    rows = _execute(
        sql_engine,
        f"WITH source_rows (id, {column}) AS (VALUES {values}) "
        f"SELECT id, {expression} FROM source_rows ORDER BY id",
    )
    assert [row[1] for row in rows[:2]] == [None, None]
    assert [float(row[1]) for row in rows[2:]] == pytest.approx([0, 12.34, cap, cap])


def test_commune_dvf_missing_group_is_null_but_observed_zero_survives(sql_engine):
    names = [
        "nb_mutations_12m",
        "nb_mutations_12m_maison",
        "nb_mutations_12m_appartement",
    ]
    expressions = _select_expressions(
        CREATE_SQL.split("\nSELECT\n", 1)[1].split("FROM demographie", 1)[0]
    )
    projection = [
        expr
        for expr in expressions
        if any(re.search(rf"\bAS {name}$", expr) for name in names)
    ]
    assert len(projection) == 3
    rows = _execute(
        sql_engine,
        "WITH communes(code_insee) AS (VALUES ('44001'), ('44002')), "
        "dvf(code_insee, nb_mutations_12m, nb_mutations_12m_maison, "
        "nb_mutations_12m_appartement) AS (VALUES ('44001', 3, 3, 0)) "
        f"SELECT communes.code_insee, {', '.join(projection)} FROM communes "
        "LEFT JOIN dvf ON dvf.code_insee = communes.code_insee "
        "ORDER BY communes.code_insee",
    )
    assert rows == [("44001", 3, 3, 0), ("44002", None, None, None)]


def test_foncier_area_counts_parcels_not_owners_or_distinct_areas(sql_engine):
    ddl = dict(INTERMEDIATE_MATVIEWS)["v_commune_foncier_pm"]
    query = ddl.split(" AS", 1)[1].rsplit("WITH DATA", 1)[0].strip()
    # Two separate parcels have the same area; both must count. One has two
    # owners, duplicated rights and an owner with no SIREN: count it only once.
    rows = _execute(
        sql_engine,
        "WITH foncier_pm(code_commune, code_parcelle, numero_siren, "
        "contenance_parcelle) AS (VALUES "
        "('44001', '44001000AA0001', '111', 1500.0), "
        "('44001', '44001000AA0001', '222', 1500.0), "
        "('44001', '44001000AA0001', '222', 1500.0), "
        "('44001', '44001000AA0001', NULL, 1500.0), "
        "('44001', '44001000AA0002', '111', 1500.0), "
        "('44001', '44001000AA0003', '111', 0.0), "
        "('44002', '44002000AA0001', '333', NULL)) "
        f"SELECT * FROM ({query}) result ORDER BY code_insee",
    )
    # An owner with no identifier makes the owner count unknown (COR-14),
    # independently of the known area of the two identified parcels.
    assert rows[0][:2] == ("44001", None)
    assert float(rows[0][2]) == pytest.approx(0.3)
    assert rows[1:] == [("44002", 1, None)]


def test_foncier_incomplete_or_conflicting_parcel_area_is_not_a_total(sql_engine):
    ddl = dict(INTERMEDIATE_MATVIEWS)["v_commune_foncier_pm"]
    query = ddl.split(" AS", 1)[1].rsplit("WITH DATA", 1)[0].strip()
    rows = _execute(
        sql_engine,
        "WITH foncier_pm(code_commune, code_parcelle, numero_siren, "
        "contenance_parcelle) AS (VALUES "
        "('44001', '44001000AA0001', '111', 1500.0), "
        "('44001', '44001000AA0001', '222', 2500.0), "
        "('44002', '44002000AA0001', '111', 1500.0), "
        "('44002', '44002000AA0002', '222', NULL), "
        "('44003', '44003000AA0001', '111', 0.0), "
        "('44004', '', '111', 1500.0), "
        "('44004', ' ', '222', 1500.0), "
        "('44005', NULL, '111', 1500.0), "
        "('44005', NULL, '222', 1500.0)) "
        f"SELECT * FROM ({query}) result ORDER BY code_insee",
    )
    assert rows == [
        ("44001", 2, None),
        ("44002", 2, None),
        ("44003", 1, 0),
        ("44004", 2, None),
        ("44005", 2, None),
    ]
