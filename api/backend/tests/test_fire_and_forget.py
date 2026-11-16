"""Tests for app.common.async_helpers.fire_and_forget.

Verifies that:
- Successful coroutines produce no ERROR log.
- Failing coroutines produce an ERROR-level 'background_task_failed' log
  with exc_info and task_name in extra.
- Cancelled coroutines produce no ERROR log.
- Task references are held in _background_tasks until completion.
"""

import asyncio
import contextlib
import logging

import pytest

from app.common.async_helpers import fire_and_forget


@pytest.mark.asyncio
async def test_successful_coro_no_error_log(caplog):
    async def ok():
        return 42

    caplog.set_level(logging.ERROR)
    t = fire_and_forget(ok(), task_name="ok_task")
    await asyncio.wait_for(t, timeout=1.0)
    assert not any(r.message == "background_task_failed" for r in caplog.records)


@pytest.mark.asyncio
async def test_failing_coro_logs_error(caplog):
    async def boom():
        raise RuntimeError("expected")

    caplog.set_level(logging.ERROR)
    t = fire_and_forget(boom(), task_name="boom_task")
    # Wait for task to complete (raises internally; done-callback already fired).
    with contextlib.suppress(RuntimeError):
        await asyncio.wait_for(t, timeout=1.0)
    failed = [r for r in caplog.records if r.message == "background_task_failed"]
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert failed[0].exc_info is not None
    assert failed[0].exc_info[0] is RuntimeError
    assert (
        getattr(failed[0], "task_name", None) == "boom_task"
        or failed[0].__dict__.get("task_name") == "boom_task"
    )


@pytest.mark.asyncio
async def test_cancelled_coro_no_error_log(caplog):
    async def slow():
        await asyncio.sleep(5)

    caplog.set_level(logging.ERROR)
    t = fire_and_forget(slow(), task_name="slow_task")
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert not any(r.message == "background_task_failed" for r in caplog.records)


@pytest.mark.asyncio
async def test_task_reference_held_until_done():
    """Prevents task GC before completion."""
    from app.common.async_helpers import _background_tasks

    async def delayed():
        await asyncio.sleep(0.05)

    t = fire_and_forget(delayed())
    assert t in _background_tasks
    await t
    # After done_callback fires, task is discarded.
    assert t not in _background_tasks
