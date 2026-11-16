"""Shared asyncio helpers.

fire_and_forget -- schedule a coroutine as a tracked task whose unhandled
exception is logged instead of silently swallowed. Use this anywhere
asyncio.create_task would otherwise discard exceptions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Module-level set prevents tasks being garbage-collected before completion.
_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(
    coro: Coroutine[Any, Any, Any], *, task_name: str | None = None
) -> asyncio.Task:
    """Schedule coro as a background task; log exceptions via done-callback."""
    task = asyncio.create_task(coro, name=task_name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "background_task_failed",
            exc_info=exc,
            extra={"task_name": task.get_name()},
        )
