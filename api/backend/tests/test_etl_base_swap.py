"""Unit tests for BasePipeline.swap_tables() SQL emission order.

Patches psycopg.connect with a MagicMock — no live DB required.
Verifies: logged guard -> DROP _old -> RENAME prod->old -> RENAME staging->prod
-> commit, and no SET LOGGED rewrite of the candidate (plan 04.1-04).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.etl.base import BasePipeline
from app.etl.errors import StagingNotLoggedError

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing swap_tables()
# ---------------------------------------------------------------------------


class SwapTestPipeline(BasePipeline):
    """Minimal concrete pipeline with fixed table names for swap testing."""

    @property
    def dataset_name(self) -> str:
        return "test"

    @property
    def expected_columns(self) -> list[str]:
        return ["id", "code_departement"]

    @property
    def staging_table_name(self) -> str:
        return "test_staging"

    @property
    def production_table_name(self) -> str:
        return "test_prod"

    @property
    def partition_department_scope(self) -> list[str]:
        return ["85"]

    def download(self) -> list[Path]:
        return []

    def validate_schema(self, file_path: Path) -> None:
        pass

    def transform(self, file_path: Path) -> Path:
        return file_path

    def stage(self, file_path: Path) -> int:
        return 0

    def swap(self) -> None:
        pass


class FlatSwapTestPipeline(SwapTestPipeline):
    """Pipeline whose production contract is intentionally non-partitioned."""

    @property
    def staging_is_partitioned(self) -> bool:
        return False

    @property
    def required_staging_constraints(self) -> tuple[str, ...]:
        return ("PRIMARY KEY (id)", "UNIQUE (natural_key)")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _mock_connection() -> tuple[MagicMock, MagicMock]:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


def test_create_staging_respects_flat_table_contract() -> None:
    pipeline = FlatSwapTestPipeline()
    mock_conn, mock_cur = _mock_connection()

    with patch("app.etl.base.psycopg.connect", return_value=mock_conn):
        pipeline.create_staging_table("postgresql://fake/db")

    statements = [str(call.args[0]) for call in mock_cur.execute.call_args_list]
    create_parent = next(
        statement
        for statement in statements
        if statement.startswith("CREATE TABLE test_staging ")
    )
    assert "PARTITION BY" not in create_parent
    assert not any("UNLOGGED" in statement for statement in statements)
    assert not any("PARTITION OF test_staging" in statement for statement in statements)


def test_create_staging_keeps_partitioned_contract() -> None:
    pipeline = SwapTestPipeline()
    mock_conn, mock_cur = _mock_connection()

    with patch("app.etl.base.psycopg.connect", return_value=mock_conn):
        pipeline.create_staging_table("postgresql://fake/db")

    statements = [str(call.args[0]) for call in mock_cur.execute.call_args_list]
    create_parent = next(
        statement
        for statement in statements
        if statement.startswith("CREATE TABLE test_staging ")
    )
    assert "PARTITION BY LIST (code_departement)" in create_parent
    assert not any("UNLOGGED" in statement for statement in statements)
    assert any("PARTITION OF test_staging" in statement for statement in statements)


def test_ensure_staging_constraints_combines_production_and_declared_contract() -> None:
    pipeline = FlatSwapTestPipeline()
    mock_cur = MagicMock()
    production = MagicMock()
    production.fetchall.return_value = [("UNIQUE (source_key)",)]
    staging = MagicMock()
    staging.fetchall.return_value = [("PRIMARY KEY (id)",)]
    mock_cur.execute.side_effect = [production, staging, MagicMock(), MagicMock()]

    pipeline.ensure_staging_constraints(mock_cur)

    statements = [str(call.args[0]) for call in mock_cur.execute.call_args_list]
    assert "ALTER TABLE test_staging ADD UNIQUE (natural_key)" in statements
    assert "ALTER TABLE test_staging ADD UNIQUE (source_key)" in statements
    assert "ALTER TABLE test_staging ADD PRIMARY KEY (id)" not in statements


def test_prepare_staging_constraints_commits_before_swap() -> None:
    pipeline = FlatSwapTestPipeline()
    mock_conn, mock_cur = _mock_connection()

    with (
        patch("app.etl.base.psycopg.connect", return_value=mock_conn),
        patch.object(pipeline, "ensure_staging_constraints") as ensure_constraints,
    ):
        pipeline.prepare_staging_constraints("postgresql://fake/db")

    ensure_constraints.assert_called_once_with(mock_cur)
    mock_conn.commit.assert_called_once()


def test_swap_tables_sql_sequence() -> None:
    """swap_tables() must emit SQL statements in the correct order.

    Expected sequence:
      1. SET statement_timeout = ... (from set_statement_timeout)
      2. logged guard on the candidate tree (no SET LOGGED rewrite)
      3. DROP TABLE IF EXISTS test_prod_old CASCADE
      4. ALTER TABLE test_prod RENAME TO test_prod_old
      5. ALTER TABLE test_staging RENAME TO test_prod
      6. conn.commit() called exactly once
    """
    pipeline = SwapTestPipeline()

    mock_conn, mock_cur = _mock_connection()

    with (
        patch("app.etl.base.psycopg.connect", return_value=mock_conn),
        patch.object(pipeline, "prepare_staging_constraints") as prepare_constraints,
        patch.object(pipeline, "rename_indexes_after_swap") as rename_indexes,
    ):
        pipeline.swap_tables("postgresql://fake/db")

    # Post-swap index hygiene runs inside the same transaction (DB-HYGIENE-01)
    rename_indexes.assert_called_once_with(mock_cur)

    prepare_constraints.assert_called_once_with("postgresql://fake/db")

    # Extract the SQL strings passed to cur.execute()
    execute_calls = mock_cur.execute.call_args_list
    sql_statements = [str(c.args[0]) for c in execute_calls]

    # 1. statement_timeout must be first
    assert any("statement_timeout" in s for s in sql_statements), (
        "SET statement_timeout not found in execute calls"
    )

    # 2. Logged guard, and never a SET LOGGED rewrite of the candidate
    assert any("relpersistence" in s for s in sql_statements), (
        "logged guard on the candidate not found"
    )
    assert not any("SET LOGGED" in s for s in sql_statements)

    # 3. DROP IF EXISTS _old CASCADE
    assert any(
        "DROP TABLE IF EXISTS" in s and "test_prod_old" in s and "CASCADE" in s
        for s in sql_statements
    ), "DROP TABLE IF EXISTS test_prod_old CASCADE not found"

    # 4. RENAME prod -> old
    assert any(
        "test_prod" in s and "RENAME TO" in s and "test_prod_old" in s
        for s in sql_statements
    ), "ALTER TABLE test_prod RENAME TO test_prod_old not found"

    # 5. RENAME staging -> prod
    assert any(
        "test_staging" in s and "RENAME TO" in s and "test_prod" in s
        for s in sql_statements
    ), "ALTER TABLE test_staging RENAME TO test_prod not found"

    # 6. commit called exactly once
    mock_conn.commit.assert_called_once()


def test_swap_tables_order() -> None:
    """The logged guard comes before the two RENAMEs, DROP before RENAME."""
    pipeline = SwapTestPipeline()

    mock_conn, mock_cur = _mock_connection()

    with (
        patch("app.etl.base.psycopg.connect", return_value=mock_conn),
        patch.object(pipeline, "prepare_staging_constraints"),
        patch.object(pipeline, "rename_indexes_after_swap"),
    ):
        pipeline.swap_tables("postgresql://fake/db")

    sql_statements = [str(c.args[0]) for c in mock_cur.execute.call_args_list]

    def idx(fragment: str) -> int:
        for i, s in enumerate(sql_statements):
            if fragment in s:
                return i
        raise AssertionError(f"Fragment not found: {fragment!r}")

    logged_idx = idx("relpersistence")
    drop_idx = idx("DROP TABLE IF EXISTS")
    rename_prod_idx = idx("test_prod RENAME TO test_prod_old")
    rename_staging_idx = idx("test_staging RENAME TO test_prod")

    # Order: logged guard < DROP < RENAME prod->old < RENAME staging->prod
    assert logged_idx < drop_idx, "logged guard must come before DROP _old"
    assert drop_idx < rename_prod_idx, "DROP _old must come before RENAME prod->old"
    assert rename_prod_idx < rename_staging_idx, (
        "RENAME prod->old must come before RENAME staging->prod"
    )


def test_swap_tables_refuses_unlogged_candidate_before_any_rename() -> None:
    pipeline = SwapTestPipeline()
    mock_conn, mock_cur = _mock_connection()
    mock_cur.execute.return_value.fetchall.return_value = [("test_staging_85",)]

    with (
        patch("app.etl.base.psycopg.connect", return_value=mock_conn),
        patch.object(pipeline, "prepare_staging_constraints"),
        patch.object(pipeline, "rename_indexes_after_swap") as rename_indexes,
        pytest.raises(StagingNotLoggedError, match="test_staging_85"),
    ):
        pipeline.swap_tables("postgresql://fake/db")

    statements = [str(c.args[0]) for c in mock_cur.execute.call_args_list]
    assert not any("RENAME" in s or "DROP TABLE" in s for s in statements)
    rename_indexes.assert_not_called()
    mock_conn.commit.assert_not_called()


def test_published_old_tables_defaults_to_the_renamed_production() -> None:
    assert SwapTestPipeline().published_old_tables == ("test_prod_old",)
