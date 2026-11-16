"""Transport staging keeps its id sequence on every swap (2026-10-30).

The national import failed three times on ``null value in column "id" of
relation "arrets_transport_staging"``: swap() recreated the staging tables by
``LIKE ... INCLUDING ALL`` without their sequences, so they inherited the
production default; Phase C then dropped that sequence with CASCADE, leaving the
published table without default and the next import with NULL ids.
"""

from unittest.mock import MagicMock

import pytest

from app.etl_transport import pipeline as transport_module
from app.etl_transport.pipeline import TransportPipeline


class _Recorder:
    def __init__(self):
        self.sql = []

    def execute(self, query, *args):
        self.sql.append(str(query))
        return MagicMock()


def test_recreated_staging_tables_get_their_own_sequences():
    cur = _Recorder()
    TransportPipeline._recreate_staging_tables(cur)
    joined = "\n".join(cur.sql)
    for table in ("arrets_transport", "gares"):
        create = joined.index(f"CREATE TABLE {table}_staging")
        seq = joined.index(f"CREATE SEQUENCE {table}_staging_id_seq")
        default = joined.index(f"nextval('{table}_staging_id_seq')")
        assert create < default and seq < default


def test_swap_recreates_staging_through_the_sequence_aware_helper(monkeypatch):
    class _ReachedError(Exception):
        pass

    def _helper(cur):
        raise _ReachedError

    monkeypatch.setattr(
        TransportPipeline, "_recreate_staging_tables", staticmethod(_helper)
    )
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = MagicMock()
    monkeypatch.setattr(transport_module.psycopg, "connect", lambda dsn: conn)
    monkeypatch.setattr(
        TransportPipeline, "get_sync_dsn", staticmethod(lambda: "postgresql://x")
    )
    with pytest.raises(_ReachedError):
        TransportPipeline().swap()
