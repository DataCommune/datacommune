"""COR-01 real PostgreSQL oracles; run only via the disposable harness."""

from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests.data_quality.scopes import FULL_FRANCE_SCOPE

pytestmark = [pytest.mark.destructive]


@pytest.fixture
def relation(pg_conn):
    name = "cor01_" + uuid4().hex[:12]
    yield name
    pg_conn.rollback()
    pg_conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
            sql.Identifier("public", name)
        )
    )
    pg_conn.execute(
        sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name))
    )
    pg_conn.commit()


def validator(name):
    from app.validation.base import BaseValidator

    class Validator(BaseValidator):
        dataset_name = "cor01"
        production_table_name = name

        def get_source_row_count(self):
            return 0

    return Validator()


def test_count_ignores_stale_statistics_and_schema_homonym(pg_conn, pg_dsn, relation):
    pg_conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(relation)))
    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier(relation, relation))
    )
    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier("public", relation))
    )
    pg_conn.execute(
        sql.SQL("INSERT INTO {} SELECT generate_series(1, 7)").format(
            sql.Identifier("public", relation)
        )
    )
    pg_conn.execute(sql.SQL("ANALYZE {}").format(sql.Identifier("public", relation)))
    pg_conn.execute(
        sql.SQL("INSERT INTO {} SELECT generate_series(8, 19)").format(
            sql.Identifier("public", relation)
        )
    )
    pg_conn.commit()
    assert validator(relation).get_local_row_count(pg_dsn) == 19


def test_partition_parent_counts_children_and_records_null_denominator(
    pg_conn, pg_dsn, relation
):
    from app.etl.validation import check_null_rates

    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int, value int) PARTITION BY RANGE(id)").format(
            sql.Identifier("public", relation)
        )
    )
    pg_conn.execute(
        sql.SQL("CREATE TABLE {} PARTITION OF {} FOR VALUES FROM (0) TO (100)").format(
            sql.Identifier("public", relation + "_part"),
            sql.Identifier("public", relation),
        )
    )
    pg_conn.execute(
        sql.SQL("INSERT INTO {} VALUES (1,NULL),(2,42),(3,NULL)").format(
            sql.Identifier("public", relation)
        )
    )
    pg_conn.commit()
    assert validator(relation).get_local_row_count(pg_dsn) == 3
    findings = check_null_rates(pg_dsn, relation, 0.5)
    assert findings[0]["null_count"] == 2
    assert findings.evidence["observed"] == findings.evidence["population_exact"] == 3
    assert findings.evidence["method"] == "exact"


def test_missing_relation_is_not_zero_or_clean(pg_dsn, relation):
    from app.etl.validation import check_null_rates

    with pytest.raises(psycopg.errors.UndefinedTable):
        validator(relation).get_local_row_count(pg_dsn)
    with pytest.raises(RuntimeError):
        check_null_rates(pg_dsn, relation, 0.5)


def test_empty_sample_preserves_observed_zero_and_estimate(pg_conn, pg_dsn, relation):
    from app.etl.validation import check_null_rates

    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int, value int)").format(
            sql.Identifier("public", relation)
        )
    )
    pg_conn.execute(
        sql.SQL("INSERT INTO {} VALUES (1,NULL)").format(
            sql.Identifier("public", relation)
        )
    )
    pg_conn.commit()
    # Force an inaccurate planner estimate on this disposable catalog only.
    pg_conn.execute(
        "UPDATE pg_class SET reltuples=2000000 WHERE oid=to_regclass(%s)",
        ("public." + relation,),
    )
    pg_conn.commit()
    with pytest.raises(RuntimeError) as caught:
        check_null_rates(pg_dsn, relation, 0.5)
    assert caught.value.evidence["observed"] == 0
    assert caught.value.evidence["population_estimated"] == 2_000_000
    assert caught.value.evidence["seed"] == 0


def test_same_connection_expected_scan_rejects_missing_column(
    pg_conn, pg_dsn, relation
):
    from app.data_quality.profiles import DatasetProfile, NullSpec
    from app.data_quality.run import _audit_profile

    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier("public", relation))
    )
    pg_conn.execute(
        sql.SQL("INSERT INTO {} VALUES (1)").format(sql.Identifier("public", relation))
    )
    profile = DatasetProfile(
        dataset="cor01",
        table=relation,
        nulls=NullSpec(required_cols=("id",), expected_cols=("absent",)),
    )
    with patch(
        "psycopg.connect", side_effect=AssertionError("Unexpected second connection")
    ):
        result, _dims = _audit_profile(pg_conn, profile, FULL_FRANCE_SCOPE)
    assert result.errors
    assert result.stats["null_scans"]["completed"] == 1
    assert result.stats["null_scans"]["expected"] == 2
    assert pg_conn.execute("SELECT 1").fetchone() == (1,)


def test_exact_count_sql_timeout_cannot_pass(pg_conn, pg_dsn, relation):
    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier("public", relation))
    )
    pg_conn.commit()
    pg_conn.execute(
        sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
            sql.Identifier("public", relation)
        )
    )
    candidate = validator(relation)
    candidate.statement_timeout_ms = 50
    with pytest.raises(psycopg.errors.QueryCanceled):
        candidate.get_local_row_count(pg_dsn)


def test_empty_table_no_columns_and_clean_scan_are_distinct(pg_conn, pg_dsn, relation):
    from app.etl.validation import check_null_rates

    pg_conn.execute(
        sql.SQL("CREATE TABLE {} ()").format(sql.Identifier("public", relation))
    )
    pg_conn.commit()
    with pytest.raises(RuntimeError, match="Missing columns"):
        check_null_rates(pg_dsn, relation, 0.5)
    pg_conn.execute(
        sql.SQL("ALTER TABLE {} ADD id int").format(sql.Identifier("public", relation))
    )
    pg_conn.commit()
    with pytest.raises(RuntimeError) as caught:
        check_null_rates(pg_dsn, relation, 0.5)
    assert caught.value.evidence["population_exact"] == 0
    assert caught.value.evidence["method"] == "exact"
    pg_conn.execute(
        sql.SQL("INSERT INTO {} VALUES (42)").format(sql.Identifier("public", relation))
    )
    pg_conn.commit()
    findings = check_null_rates(pg_dsn, relation, 0.5)
    assert findings == []
    assert findings.evidence["observed"] == 1
    assert findings.evidence["null_counts"] == {"id": 0}


def test_required_failure_keeps_expected_scan_and_connection_usable(pg_conn, relation):
    from app.data_quality.profiles import DatasetProfile, NullSpec
    from app.data_quality.run import _audit_profile

    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier("public", relation))
    )
    pg_conn.execute(
        sql.SQL("INSERT INTO {} VALUES (1)").format(sql.Identifier("public", relation))
    )
    profile = DatasetProfile(
        dataset="cor01",
        table=relation,
        nulls=NullSpec(required_cols=("absent",), expected_cols=("id",)),
    )
    result, _dims = _audit_profile(pg_conn, profile, FULL_FRANCE_SCOPE)
    assert result.errors
    assert result.stats["null_scans"]["completed"] == 1
    assert result.stats["null_scans"]["expected"] == 2
    assert result.stats["null_scans"]["checks"][0]["status"] == "unavailable"
    assert pg_conn.execute("SELECT 1").fetchone() == (1,)


def test_null_scan_register_separates_completion_from_clean_verdict(pg_conn, relation):
    from app.data_quality.profiles import DatasetProfile, NullSpec
    from app.data_quality.run import _audit_profile

    pg_conn.execute(
        sql.SQL("CREATE TABLE {} (id int, value int)").format(
            sql.Identifier("public", relation)
        )
    )
    pg_conn.execute(
        sql.SQL("INSERT INTO {} VALUES (1, NULL), (2, 42)").format(
            sql.Identifier("public", relation)
        )
    )
    profile = DatasetProfile(
        dataset="cor01",
        table=relation,
        nulls=NullSpec(
            required_cols=("id",), expected_cols=("value",), expected_threshold=0.05
        ),
    )
    result, _dims = _audit_profile(pg_conn, profile, FULL_FRANCE_SCOPE)
    scans = result.stats["null_scans"]
    assert scans["completed"] == scans["expected"] == 2
    assert scans["checks"][0]["status"] == "passed"
    assert scans["checks"][0]["null_count"] == 0
    assert scans["checks"][0]["observed"] == 2
    assert scans["checks"][1]["status"] == "failed"
    assert scans["checks"][1]["observation"]["null_counts"] == {"value": 1}
    assert scans["checks"][1]["observation"]["observed"] == 2
    assert len(result.warnings) == 1
