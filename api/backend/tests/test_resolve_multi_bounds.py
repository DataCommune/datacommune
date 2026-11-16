"""Bounds on query_multiple_datasets fan-out (audit QE-01, HIGH).

The resolve multi-dataset collector used to accept an unbounded ``datasets``
list: N datasets = N parallel AsyncSessions + N outbound proxy calls. The fix
adds three gates, each proven here:

  1. a hard cap of 5 datasets per request -> explicit 422 beyond it;
  2. deduplication preserving first-seen order (duplicates must not fan out);
  3. a global concurrency semaphore bounding simultaneously open DB sessions
     and third-party HTTP calls.

No real DB: the session factory is faked with an async context manager that
tracks peak concurrency.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.common.exceptions import ErreurApiError
from app.resolve.multi import (
    MAX_CONCURRENCY_DATASETS,
    MAX_DATASETS_PAR_REQUETE,
    query_multiple_datasets,
)


class _CountingSession:
    """Async session stand-in that records open-session concurrency."""

    def __init__(self, state: dict) -> None:
        self._state = state

    async def execute(self, statement, parameters=None):
        return SimpleNamespace(scalar_one=lambda: True)

    async def __aenter__(self):
        self._state["open"] += 1
        self._state["peak"] = max(self._state["peak"], self._state["open"])
        return self

    async def __aexit__(self, *exc_info):
        self._state["open"] -= 1


def _fake_session_factory(state: dict):
    return lambda: _CountingSession(state)


# DB datasets that need neither INSEE nor BAN pre-resolve, so no extra session
# is opened by Phase 1 — every session observed comes from the fan-out itself.
_NO_BAN_DB_DATASETS = [
    "batiments",
    "monuments",
    "transports",
    "antennes",
    "equipements",
]


async def test_more_than_five_datasets_rejected_with_422():
    """Six distinct datasets must be rejected upfront with an explicit 422."""
    six = ["ban", "cadastre", "dvf", "loyers", "fiscalite", "zonage_abc"]

    with pytest.raises(ErreurApiError) as excinfo:
        await query_multiple_datasets(
            _fake_session_factory({"open": 0, "peak": 0}),
            six,
            lat=48.8692,
            lon=2.3315,
        )

    assert excinfo.value.code == 422
    assert excinfo.value.message  # explicit French message, non-empty
    assert "5" in excinfo.value.detail or "cinq" in excinfo.value.detail.lower()


async def test_duplicate_datasets_deduplicated_preserving_order():
    """A repeated list fans out once per unique dataset, in first-seen order."""
    state = {"open": 0, "peak": 0}
    dispatch = AsyncMock(return_value=(None, []))

    with patch("app.resolve.multi._dispatch_db", dispatch):
        result = await query_multiple_datasets(
            _fake_session_factory(state),
            ["monuments", "batiments", "monuments", "transports", "monuments"],
            lat=48.8692,
            lon=2.3315,
        )

    assert list(result["donnees"].keys()) == ["monuments", "batiments", "transports"]
    assert dispatch.await_count == 3


async def test_parallel_db_sessions_bounded_by_semaphore():
    """Peak concurrently open DB sessions never exceeds the semaphore bound."""
    state = {"open": 0, "peak": 0}
    dispatch = AsyncMock(return_value=(None, []))

    async def slow_dispatch(*args, **kwargs):
        # Hold the session open across an event-loop yield so overlap is real.
        import asyncio

        await asyncio.sleep(0.01)
        return (None, [])

    dispatch.side_effect = slow_dispatch

    with patch("app.resolve.multi._dispatch_db", dispatch):
        await query_multiple_datasets(
            _fake_session_factory(state),
            list(_NO_BAN_DB_DATASETS),  # 5 distinct DB datasets
            lat=48.8692,
            lon=2.3315,
        )

    assert state["peak"] <= MAX_CONCURRENCY_DATASETS
    assert MAX_CONCURRENCY_DATASETS < MAX_DATASETS_PAR_REQUETE
