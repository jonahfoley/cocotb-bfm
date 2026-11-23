"""Concurrency helpers compatible across cocotb 2.0 and 2.1+."""

from __future__ import annotations

import contextlib
from asyncio import CancelledError
from collections.abc import Coroutine
from typing import Any

import cocotb
from cocotb import triggers
from cocotb.task import Task


async def race(
    first_coro: Coroutine[Any, Any, Any],
    second_coro: Coroutine[Any, Any, Any],
) -> tuple[int, Any]:
    """Race two coroutines, cancelling the loser and returning ``(winner_index, result)``.

    Uses ``cocotb.triggers.select`` natively on cocotb 2.1+ (avoiding ``First(Task)``
    deprecation warnings) and falls back cleanly to ``First`` on cocotb 2.0.x.
    """
    select_fn = getattr(triggers, "select", None)
    if callable(select_fn):
        idx, result = await select_fn(first_coro, second_coro)
        return int(idx), result

    task_a: Task[Any] = cocotb.start_soon(first_coro)
    task_b: Task[Any] = cocotb.start_soon(second_coro)
    try:
        winner = await triggers.First(task_a, task_b)
        if winner is task_b or (task_b.done() and not task_a.done()):
            task_a.cancel()
            with contextlib.suppress(CancelledError):
                await task_a
            return 1, task_b.result()
        task_b.cancel()
        with contextlib.suppress(CancelledError):
            await task_b
        return 0, task_a.result()
    except CancelledError:
        for task in (task_a, task_b):
            if not task.done():
                task.cancel()
        raise
