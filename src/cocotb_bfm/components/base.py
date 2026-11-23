"""Explicit structured lifecycle base class for cocotb 2.0 BFM components."""

from __future__ import annotations

import contextlib
import logging
from abc import ABC, abstractmethod
from asyncio import CancelledError
from types import TracebackType
from typing import Any, Self

import cocotb
from cocotb.task import Task

from cocotb_bfm.exceptions import ComponentLifecycleError


class Component(ABC):
    """Base class for verification components with explicit ``start()`` / ``stop()``
    lifecycle.

    Unlike legacy ``cocotb-bus`` components, :class:`Component` **never** spawns
    background tasks inside ``__init__``. Start components explicitly via
    :meth:`start` / :meth:`stop` or as an async context manager::

        async with MyDriver(bus, dut.clk) as drv:
            await drv.send(tx)
    """

    def __init__(self, *, name: str | None = None) -> None:
        self._name: str = name if name is not None else type(self).__name__
        self.log: logging.Logger = logging.getLogger(f"cocotb_bfm.{self._name}")
        self._task: Task[Any] | None = None
        self._stopping: bool = False

    @property
    def name(self) -> str:
        """Human-readable component name (also used by legacy scoreboards and logs)."""
        return self._name

    @property
    def running(self) -> bool:
        """Return ``True`` if the component's background task is currently active."""
        return self._task is not None and not self._task.done()

    def start(self) -> Self:
        """Start the component's background task.

        Raises:
            ComponentLifecycleError: If the component is already running.
        """
        if self.running:
            raise ComponentLifecycleError(
                f"Component '{self._name}' is already running"
            )
        self._stopping = False
        self._task = cocotb.start_soon(self._run_wrapper())
        return self

    async def stop(self) -> None:
        """Stop the component cleanly and await task termination (idempotent)."""
        task = self._task
        self._stopping = True
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(CancelledError):
                await task
        self._task = None
        await self._on_stop()

    async def __aenter__(self) -> Self:
        self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is GeneratorExit:
            if self._task is not None and not self._task.done():
                self._task.cancel()
            self._task = None
            return
        await self.stop()

    async def _run_wrapper(self) -> None:
        try:
            await self._run()
        except CancelledError:
            raise
        except Exception:
            self.log.exception("Unhandled exception in component '%s'", self._name)
            raise

    @abstractmethod
    async def _run(self) -> None:
        """Main background coroutine implemented by subclasses."""

    async def _on_stop(self) -> None:  # noqa: B027
        """Optional hook called after the background task stops."""
