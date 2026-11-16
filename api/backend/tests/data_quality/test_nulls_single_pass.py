"""Required NULL checks read the table once (first national DPE, 2026-10-30).

One exact scan per required column, each capped at 8 s, could not finish on the
16.7 M documents of the national DPE (18 GB): every check came back unavailable
and the publication was rejected. A single exact pass over all required columns
took 8.9 s there.
"""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _profile():
    from app.data_quality.profiles import DatasetProfile, NullSpec

    return DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        nulls=NullSpec(
            required_cols=("id", "numero_dpe", "source_state"), expected_cols=()
        ),
    )


def test_required_columns_are_scanned_in_one_exact_pass():
    from app.config import settings
    from app.data_quality.profiles import check_nulls

    executed = []

    def execute(query, *args, **kwargs):
        text = query if isinstance(query, str) else query.as_string()
        executed.append(text)
        result = MagicMock()
        result.fetchone.return_value = (16_667_371, 0, 0, 2)
        return result

    conn = MagicMock()
    conn.execute.side_effect = execute
    stats = {}
    findings = check_nulls(conn, _profile(), stats=stats)

    scans = [q for q in executed if q.upper().startswith("SELECT COUNT(*)")]
    assert len(scans) == 1
    assert all(
        f'"{c}" IS NULL' in scans[0] for c in ("id", "numero_dpe", "source_state")
    )
    timeouts = [q for q in executed if "statement_timeout" in q]
    assert timeouts == [
        f"SET LOCAL statement_timeout = '{settings.etl_validation_scan_timeout_s}s'"
    ]
    assert stats["null_scans"]["completed"] == 3
    assert [f.count for f in findings if f.severity == "ERROR"] == [2]


def test_a_failed_single_pass_falls_back_to_one_scan_per_column():
    from app.data_quality.profiles import check_nulls

    calls = {"n": 0}

    def execute(query, *args, **kwargs):
        text = query if isinstance(query, str) else query.as_string()
        result = MagicMock()
        if text.upper().startswith("SELECT COUNT(*)"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("column missing in one pass")
            result.fetchone.return_value = (10, 0)
        return result

    conn = MagicMock()
    conn.execute.side_effect = execute
    stats = {}
    check_nulls(conn, _profile(), stats=stats)
    assert calls["n"] == 1 + 3
    assert stats["null_scans"]["completed"] == 3


def test_expected_scan_of_a_partitioned_table_runs_under_the_scan_timeout():
    """Cadastre run 122 (2026-10-31): the expected scan of ``parcelles`` died.

    A partitioned parent is scanned exactly (no TABLESAMPLE); under the 8 s
    cap of ``check_null_rates`` its 94.8 M rows were cancelled, the expected
    check came back unavailable and the national publication was rejected.
    Both passes must scan under the same settable timeout.
    """
    from app.config import settings
    from app.data_quality.profiles import DatasetProfile, NullSpec, check_nulls

    profile = DatasetProfile(
        dataset="cadastre",
        table="parcelles",
        nulls=NullSpec(
            required_cols=("code_departement", "id_parcelle"),
            expected_cols=("commune", "geometrie"),
        ),
    )
    state = {"timeout": None}
    scans = []
    catalog = []

    def execute(query, *args, **kwargs):
        text = query if isinstance(query, str) else query.as_string()
        result = MagicMock()
        if "statement_timeout" in text:
            state["timeout"] = text
        elif "FROM pg_class" in text:
            catalog.append(state["timeout"])
            result.fetchone.return_value = (4242, "p", 94_796_585.0)
        elif "FROM pg_attribute" in text:
            catalog.append(state["timeout"])
            result.fetchall.return_value = [
                ("code_departement",),
                ("id_parcelle",),
                ("commune",),
                ("geometrie",),
            ]
        elif text.upper().startswith("SELECT COUNT(*)"):
            scans.append(state["timeout"])
            result.fetchone.return_value = (94_796_585, 0, 0)
        return result

    conn = MagicMock()
    conn.execute.side_effect = execute
    stats = {}
    findings = check_nulls(conn, profile, stats=stats)

    expected_timeout = (
        f"SET LOCAL statement_timeout = '{settings.etl_validation_scan_timeout_s}s'"
    )
    assert scans == [expected_timeout, expected_timeout]
    # The catalog reads before the scan stay bounded by the short timeout.
    assert catalog == ["SET LOCAL statement_timeout = '8s'"] * 2
    assert stats["null_scans"]["completed"] == 3
    assert [c["status"] for c in stats["null_scans"]["checks"]][-1] == "passed"
    assert not [f for f in findings if f.severity == "ERROR"]
