"""An offline test finds no database at once, as on CI (conftest guard).

Windows takes 4.1 s to refuse ``localhost:5432`` (2 s per loopback address);
the guard refuses at once with the same error, so a read that degrades without
its database costs nothing more than on Linux.
"""

import time

import pytest
from sqlalchemy import text

from app.database import engine

pytestmark = pytest.mark.unit


async def test_the_app_engine_is_refused_at_once_with_the_ci_error():
    debut = time.perf_counter()
    with pytest.raises(ConnectionRefusedError):
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    assert time.perf_counter() - debut < 0.5
