"""scripts/measure_db_sizes.py: output shape, without a database (04.1-02).

The catalog queries are answered by a simulated connection; what is checked
is what the script makes of their rows: keys of the JSON, aggregation of
partitions and of partition indexes, constraint-backed indexes, persistence
and the flat-table inventory.
"""

import io
import json
from datetime import UTC, datetime

import pytest

from scripts import measure_db_sizes as mds

pytestmark = pytest.mark.unit

# (relname, relkind, parent, total, table, indexes, toast, reltuples,
#  populated, relpersistence) -- the oid column comes first in the real rows.
RELATIONS = [
    # Real state on the work base: partitioned parents unlogged, partitions logged.
    ("adresses", "p", None, 0, 0, 0, 0, -1, True, "u"),
    ("adresses_44", "r", "adresses", 1000, 600, 300, 100, 40, True, "p"),
    ("adresses_85", "r", "adresses", 500, 300, 150, 50, 20, True, "p"),
    ("eligibilite_fixe", "r", None, 2000, 1500, 500, 0, 300, True, "p"),
    ("_cadastre_staging_flat", "r", None, 800, 800, 0, 0, 90, True, "p"),
    ("_dpe_staging", "r", None, 400, 300, 100, 0, 10, True, "u"),
    ("adresses_staging", "r", None, 10, 10, 0, 0, 1, True, "p"),
]

# (index_name, table_name, index_kind, method, bytes, idx_scan, indisunique,
#  indisprimary, backs_constraint, parent_index)
INDEXES = [
    ("adresses_pkey", "adresses", "I", "btree", 0, None, True, True, True, None),
    (
        "adresses_44_pkey",
        "adresses_44",
        "i",
        "btree",
        100,
        3,
        True,
        True,
        True,
        "adresses_pkey",
    ),
    (
        "adresses_85_pkey",
        "adresses_85",
        "i",
        "btree",
        200,
        4,
        True,
        True,
        True,
        "adresses_pkey",
    ),
    (
        "eligibilite_fixe_pkey",
        "eligibilite_fixe",
        "i",
        "btree",
        300,
        0,
        True,
        True,
        True,
        None,
    ),
    (
        "idx_eligibilite_id_imb",
        "eligibilite_fixe",
        "i",
        "btree",
        120,
        0,
        False,
        False,
        False,
        None,
    ),
    (
        "uq_eligibilite_adresse",
        "eligibilite_fixe",
        "i",
        "btree",
        80,
        None,
        True,
        False,
        False,
        None,
    ),
]


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, *, stats_reset=None):
        self.stats_reset = stats_reset

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        q = " ".join(str(query).split())
        if q == "SELECT version()":
            return FakeResult([("PostgreSQL 17.5",)])
        if q.startswith("SELECT pg_database_size"):
            return FakeResult([(123456,)])
        if q.startswith("SELECT stats_reset"):
            started = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
            return FakeResult([(self.stats_reset, started)])
        if q.startswith("SELECT to_regclass"):
            return FakeResult([("alembic_version",)])
        if q.startswith("SELECT version_num"):
            return FakeResult([("100_dpe_source_columns",)])
        if "FROM pg_class c" in q and "pg_total_relation_size" in q:
            return FakeResult([(n, *row) for n, row in enumerate(RELATIONS)])
        if "FROM pg_index i" in q:
            return FakeResult(INDEXES)
        raise AssertionError(f"unexpected query: {q[:80]}")


def _measure(**kwargs):
    return mds.measure(FakeConn(), exact_counts=False, **kwargs)


def test_payload_carries_indexes_persistence_and_stats_reset():
    payload = _measure()
    assert {"indexes", "index_relations", "stats_reset", "relations", "tables"} <= set(
        payload
    )
    assert payload["stats_reset"] is None
    assert payload["postmaster_start_time"].startswith("2026-10-01T08:00")
    assert all("relpersistence" in r for r in payload["relations"])
    assert "flat_tables" not in payload
    first = payload["indexes"][0]
    assert set(first) >= {
        "name",
        "table",
        "bytes",
        "idx_scan",
        "is_unique",
        "is_primary",
        "backs_constraint",
    }


def test_stats_reset_is_written_in_utc():
    reset = datetime(2026, 9, 24, 10, 30, tzinfo=UTC)
    payload = mds.measure(FakeConn(stats_reset=reset), exact_counts=False)
    assert payload["stats_reset"] == "2026-09-24T10:30:00+00:00"


def test_partitions_aggregate_under_their_root_table():
    tables = _measure()["tables"]
    adresses = tables["adresses"]
    assert adresses["partitions"] == 2
    assert adresses["total_bytes"] == 1500
    assert adresses["table_bytes"] == 900
    assert adresses["indexes_bytes"] == 450
    assert adresses["toast_bytes"] == 150
    # The parent's reltuples of -1 is not counted.
    assert adresses["reltuples"] == 60
    # The parent's own persistence, and that of the partitions holding rows.
    assert adresses["relpersistence"] == "u"
    assert adresses["storage_relpersistence"] == ["p"]
    assert tables["_dpe_staging"]["relpersistence"] == "u"
    assert tables["_dpe_staging"]["storage_relpersistence"] == ["u"]


def test_partition_indexes_aggregate_under_their_root_index():
    indexes = {i["name"]: i for i in _measure()["indexes"]}
    pkey = indexes["adresses_pkey"]
    assert pkey["table"] == "adresses"
    assert pkey["bytes"] == 300
    assert pkey["idx_scan"] == 7
    assert pkey["partitions"] == 2
    assert pkey["is_primary"] and pkey["backs_constraint"]
    # Partition indexes are not listed again at the aggregated level.
    assert "adresses_44_pkey" not in indexes
    relations = {i["name"]: i for i in _measure()["index_relations"]}
    assert relations["adresses_44_pkey"]["parent_index"] == "adresses_pkey"
    assert relations["adresses_pkey"]["partitioned"] is True


def test_constraint_backed_index_is_flagged_and_plain_unique_is_not():
    indexes = {i["name"]: i for i in _measure()["indexes"]}
    assert indexes["eligibilite_fixe_pkey"]["backs_constraint"] is True
    assert indexes["idx_eligibilite_id_imb"]["backs_constraint"] is False
    assert indexes["idx_eligibilite_id_imb"]["idx_scan"] == 0
    unique = indexes["uq_eligibilite_adresse"]
    assert unique["is_unique"] is True and unique["backs_constraint"] is False
    # No statistics row: null, never 0.
    assert unique["idx_scan"] is None


def test_flat_tables_inventory_lists_flat_and_staging_with_persistence():
    flat = {t["name"]: t for t in _measure(with_flat_tables=True)["flat_tables"]}
    assert set(flat) == {"_cadastre_staging_flat", "_dpe_staging"}
    assert flat["_cadastre_staging_flat"]["relpersistence"] == "p"
    assert flat["_dpe_staging"]["relpersistence"] == "u"


def test_script_sha256_is_null_when_sent_on_stdin(monkeypatch):
    monkeypatch.setattr(mds, "__file__", "<stdin>")
    assert mds.script_sha256() is None


def test_out_dash_writes_json_only_on_stdout(monkeypatch, capsys):
    monkeypatch.setattr(mds, "read_dsn", lambda env: "postgresql://unused")
    monkeypatch.setattr(mds, "open_read_only", lambda dsn: FakeConn())
    assert mds.main(["--out", "-", "--flat-tables"]) == 0
    captured = capsys.readouterr()
    payload = json.load(io.StringIO(captured.out))
    assert "flat_tables" in payload and "indexes" in payload
    assert captured.err.startswith("SIZES - relations=")
