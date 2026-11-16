"""Unit test: get_db() sessions run under a server-side statement_timeout.

DETTE #1 — a slow query on a get_db() session must not be able to hold a pool
connection indefinitely. The guard is a 15s server-side statement_timeout wired
into the async engine via connect_args. This test inspects the live engine that
backs get_db (no DB connection required) to prove the setting is applied.
"""

import pytest

pytestmark = pytest.mark.unit


def test_engine_sets_server_statement_timeout():
    """The engine backing get_db must carry a 15s server-side statement_timeout.

    SQLAlchemy merges connect_args with the dialect args and captures the final
    connection kwargs in the pool creator's closure. Reading them back proves
    the timeout reaches every connection get_db() hands out — without opening
    one.
    """
    from app.database import engine

    creator = engine.sync_engine.pool._creator
    closure_vars = [cell.cell_contents for cell in (creator.__closure__ or [])]
    connect_kwargs = next(
        c for c in closure_vars if isinstance(c, dict) and "server_settings" in c
    )
    assert connect_kwargs["server_settings"]["statement_timeout"] == "15000"
