"""Background admission tasks: closing, supervision and shutdown (261013-adm).

Second GPT review: the admission rollback must never delay a refusal past
contract A (2 s + 250 ms) nor be cancelled; a background task's failure must be
retrieved and logged with types only (C60); the lifespan must drain the tasks
before disposing the engine. The pool is doubled: these are the asyncio paths,
the real driver paths live in tests/integration/test_cor20_publication.py.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.common import publication_readiness as readiness

pytestmark = pytest.mark.smoke

_SECRET = "12 rue sentinelle fermeture 44999"


class _Connection:
    """Admission connection double with a controllable checkout and close."""

    def __init__(
        self,
        *,
        start_delay: float = 0.0,
        close_delay: float = 0.0,
        close_error: Exception | None = None,
    ) -> None:
        self.start_delay = start_delay
        self.close_delay = close_delay
        self.close_error = close_error
        self.closed = False

    async def start(self) -> _Connection:
        await asyncio.sleep(self.start_delay)
        return self

    async def close(self) -> None:
        await asyncio.sleep(self.close_delay)
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.connects = 0

    def connect(self) -> _Connection:
        self.connects += 1
        return self.connection


def _session(connection: _Connection) -> SimpleNamespace:
    return SimpleNamespace(bind=_Engine(connection))


async def _writer_holds_the_lock(_connection: object) -> None:
    raise HTTPException(503, readiness.PUBLICATION_LOCKED)


@pytest.fixture(autouse=True)
def _open_admissions(monkeypatch: pytest.MonkeyPatch) -> None:
    # shutdown_admissions closes the gate: restore it after every test.
    monkeypatch.setattr(readiness, "_admissions_open", True, raising=False)


async def test_slow_rollback_does_not_delay_the_writer_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Immediate checkout, writer, 2.4 s rollback: refused within 2.25 s."""
    connection = _Connection(close_delay=2.4)
    monkeypatch.setattr(
        readiness, "acquire_publication_read_lock", _writer_holds_the_lock
    )

    started = time.perf_counter()
    with pytest.raises(HTTPException) as refused:
        await readiness.guard_snapshot_publication(_session(connection), ["dpe"])
    elapsed = time.perf_counter() - started

    assert refused.value.detail == readiness.PUBLICATION_LOCKED
    assert elapsed < 2.25, elapsed
    # The rollback was not cancelled: it finishes and the connection is back.
    assert not connection.closed
    assert await readiness.drain_admission_tasks(5) == 0
    assert connection.closed


async def test_cancelled_request_does_not_cancel_the_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request cancelled while waiting for the rollback leaves it running."""
    connection = _Connection(close_delay=0.3)
    monkeypatch.setattr(readiness, "ADMISSION_CLOSE_WAIT_S", 1.0)
    monkeypatch.setattr(readiness, "acquire_publication_read_lock", AsyncMock())
    monkeypatch.setattr(readiness, "guard_publication", AsyncMock())

    request = asyncio.ensure_future(
        readiness.guard_snapshot_publication(_session(connection), ["dpe"])
    )
    await asyncio.sleep(0.1)  # admitted, now waiting for the rollback
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert await readiness.drain_admission_tasks(5) == 0
    assert connection.closed


@pytest.mark.parametrize("failure", ["close", "checkout"])
async def test_background_failure_is_retrieved_and_logged_by_type(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    """An abandoned checkout, or its closing, that fails: logged, never lost."""
    caplog.set_level(logging.WARNING, logger=readiness.__name__)
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    monkeypatch.setattr(readiness, "ADMISSION_CHECKOUT_TIMEOUT_S", 0.05)
    connection = _Connection(
        start_delay=0.2,
        close_error=RuntimeError(_SECRET) if failure == "close" else None,
    )
    if failure == "checkout":

        async def failing_start() -> _Connection:
            await asyncio.sleep(0.2)
            raise RuntimeError(_SECRET)

        connection.start = failing_start  # type: ignore[method-assign]
    try:
        with pytest.raises(HTTPException) as refused:
            await readiness.guard_snapshot_publication(_session(connection), ["dpe"])
        assert refused.value.detail == readiness.ADMISSION_UNAVAILABLE
        assert await readiness.drain_admission_tasks(5) == 0
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(None)

    assert not [
        c
        for c in unhandled
        if "never retrieved" in c.get("message", "")
        and _SECRET in str(c.get("exception", ""))
    ]
    # Only this module's records: earlier tests may leave others behind.
    messages = [r.getMessage() for r in caplog.records if r.name == readiness.__name__]
    assert messages == [
        "admission: tâche de fond en échec (error_type=builtins.RuntimeError)"
    ]
    assert _SECRET not in caplog.text


async def test_shutdown_drains_an_active_checkout_then_refuses_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readiness, "ADMISSION_CHECKOUT_TIMEOUT_S", 0.05)
    connection = _Connection(start_delay=0.3)
    with pytest.raises(HTTPException):
        await readiness.guard_snapshot_publication(_session(connection), ["dpe"])
    assert readiness._ADMISSION_TASKS, "the checkout is still running"

    assert await readiness.shutdown_admissions(2.0) == 0
    assert connection.closed

    # Closed gate: refused at once, no checkout attempted.
    later = _session(_Connection())
    with pytest.raises(HTTPException) as refused:
        await readiness.guard_snapshot_publication(later, ["dpe"])
    assert refused.value.detail == readiness.ADMISSION_UNAVAILABLE
    assert later.bind.connects == 0


async def test_shutdown_past_its_ceiling_logs_the_remaining_count(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=readiness.__name__)
    monkeypatch.setattr(readiness, "ADMISSION_CHECKOUT_TIMEOUT_S", 0.05)
    connection = _Connection(start_delay=1.0)
    with pytest.raises(HTTPException):
        await readiness.guard_snapshot_publication(_session(connection), ["dpe"])

    started = time.perf_counter()
    assert await readiness.shutdown_admissions(0.1) == 1
    assert time.perf_counter() - started < 0.5
    assert "admission: 1 tâche(s) de fond encore active(s) à l'arrêt" in caplog.text

    # Nothing was cancelled: the checkout still completes and is returned.
    assert await readiness.drain_admission_tasks(5) == 0
    assert connection.closed


async def test_lifespan_drains_admissions_before_disposing_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main
    from app.config import settings

    order: list[str] = []

    class _BootConnection:
        async def __aenter__(self) -> _BootConnection:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, _statement: object) -> None:
            return None

    async def dispose() -> None:
        order.append("dispose")

    async def shutdown() -> int:
        order.append("shutdown_admissions")
        return 0

    monkeypatch.setattr(settings, "cors_origins", ["https://datacommune.fr"])
    monkeypatch.setattr(
        main,
        "engine",
        SimpleNamespace(connect=lambda: _BootConnection(), dispose=dispose),
    )
    monkeypatch.setattr(main, "check_migrations_current", AsyncMock())
    monkeypatch.setattr(main, "shutdown_admissions", shutdown)

    async with main.lifespan(main.app):
        pass

    assert order == ["shutdown_admissions", "dispose"]
