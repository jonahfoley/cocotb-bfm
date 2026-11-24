"""Generic, composable, reset-aware ``Monitor`` with bounded buffering."""

from __future__ import annotations

import contextlib
import inspect
from abc import abstractmethod
from asyncio import CancelledError
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

import cocotb
from cocotb.handle import LogicObject
from cocotb.queue import Queue, QueueEmpty
from cocotb.task import Task

from cocotb_bfm.bus.base import Bus
from cocotb_bfm.components._concurrency import race
from cocotb_bfm.components.base import Component
from cocotb_bfm.components.reset import Reset

BusT = TypeVar("BusT", bound=Bus)
TxT = TypeVar("TxT")
TxT_co = TypeVar("TxT_co", covariant=True)


@runtime_checkable
class MonitorProtocol(Protocol[TxT_co]):
    """Structural protocol satisfied by :class:`Monitor` and adapters."""

    @property
    def name(self) -> str:
        """Human-readable monitor name used by scoreboards and logs."""
        ...

    def add_callback(self, callback: Callable[[TxT_co], Any]) -> Any:
        """Register a callback invoked with each captured transaction."""
        ...


class Monitor(Component, Generic[BusT, TxT]):
    """Passive, statically typed observer for an externally owned :class:`Bus`.

    Transactions emitted via :meth:`_emit` are simultaneously available through:

    1. Async queue / iteration: ``await monitor.recv()`` or ``async for tx in monitor:``
    2. Typed sync or async callbacks: ``monitor.add_callback(fn)``
    3. Legacy ``cocotb_bus.scoreboard.Scoreboard`` via
       :func:`~cocotb_bfm.as_legacy_monitor`

    To prevent unbounded memory growth during callback-only soak tests while still
    allowing callers to ``await monitor.recv()`` without prior registration, the
    capture queue defaults to a bounded ring buffer (``max_queue_size=1024``) that
    evicts the oldest unread item when full instead of raising ``QueueFull``. Pass
    ``max_queue_size=None`` if you explicitly require an unbounded queue, or
    ``capture_queue=False`` to disable queue buffering altogether.
    """

    def __init__(
        self,
        bus: BusT,
        clock: LogicObject | Any,
        *,
        reset: Reset | None = None,
        name: str | None = None,
        max_queue_size: int | None = 1024,
        capture_queue: bool = True,
    ) -> None:
        """Initialize a passive monitor observing an externally owned ``bus``.

        Args:
            bus: Typed :class:`~cocotb_bfm.Bus` instance observed by this monitor.
            clock: Clock handle used to sample bus signals and synchronous resets.
            reset: Optional :class:`~cocotb_bfm.Reset` descriptor for reset handling.
            name: Optional human-readable monitor name for logs and scoreboards.
            max_queue_size: Bounded ring-buffer capacity for :meth:`recv` (defaults to
                ``1024``; pass ``None`` for an unbounded queue).
            capture_queue: Set ``False`` to disable queue buffering in callback-only
                monitors.
        """
        super().__init__(name=name)
        if max_queue_size is not None and max_queue_size < 1:
            raise ValueError(
                f"max_queue_size must be >= 1 or None (unbounded), got {max_queue_size}"
            )
        self.bus: BusT = bus
        self.clock: LogicObject = clock
        self.reset: Reset | None = reset
        self._capture_queue: bool = capture_queue
        self._queue: Queue[TxT] = Queue(
            maxsize=0 if max_queue_size is None else max_queue_size
        )
        self._callbacks: list[Callable[[TxT], Any]] = []
        self._rx_count: int = 0
        self._dropped_count: int = 0
        self._async_cb_queue: Queue[Awaitable[Any]] = Queue()
        self._async_cb_task: Task[None] | None = None

    @property
    def count(self) -> int:
        """Total number of transactions emitted by this monitor."""
        return self._rx_count

    @property
    def pending(self) -> int:
        """Number of unread transactions currently buffered in the capture queue."""
        return self._queue.qsize()

    @property
    def dropped_count(self) -> int:
        """Number of unread queue items evicted when ``max_queue_size`` was exceeded."""
        return self._dropped_count

    def add_callback(self, callback: Callable[[TxT], Any]) -> Callable[[TxT], Any]:
        """Register a sync or async callback invoked for every captured transaction."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)
        return callback

    def remove_callback(self, callback: Callable[[TxT], Any]) -> None:
        """Unregister a previously added callback."""
        self._callbacks.remove(callback)

    async def recv(self) -> TxT:
        """Await and return the next captured transaction."""
        return await self._queue.get()

    def recv_nowait(self) -> TxT:
        """Return the next buffered transaction immediately, or raise ``QueueEmpty``."""
        return self._queue.get_nowait()

    def __aiter__(self) -> AsyncIterator[TxT]:
        return self._iter_transactions()

    async def _iter_transactions(self) -> AsyncIterator[TxT]:
        while True:
            yield await self.recv()

    def _emit(self, transaction: TxT) -> None:
        """Publish a newly captured transaction to the queue and all callbacks."""
        self._rx_count += 1
        if self._capture_queue:
            if self._queue.maxsize > 0 and self._queue.full():
                with contextlib.suppress(QueueEmpty):
                    self._queue.get_nowait()
                    self._dropped_count += 1
            self._queue.put_nowait(transaction)
        for callback in list(self._callbacks):
            result = callback(transaction)
            if inspect.isawaitable(result):
                self._enqueue_async_callback(result)

    def _enqueue_async_callback(self, awaitable: Awaitable[Any]) -> None:
        if self._async_cb_task is None or self._async_cb_task.done():
            self._async_cb_task = cocotb.start_soon(self._async_cb_worker())
        self._async_cb_queue.put_nowait(awaitable)

    async def _async_cb_worker(self) -> None:
        while True:
            awaitable = await self._async_cb_queue.get()
            await awaitable

    async def _on_stop(self) -> None:
        cb_task = self._async_cb_task
        if cb_task is not None and not cb_task.done():
            cb_task.cancel()
            with contextlib.suppress(CancelledError):
                await cb_task
        self._async_cb_task = None
        while not self._async_cb_queue.empty():
            with contextlib.suppress(QueueEmpty):
                pending_coro = self._async_cb_queue.get_nowait()
                close_fn = getattr(pending_coro, "close", None)
                if callable(close_fn):
                    close_fn()

    async def _run(self) -> None:
        if self.reset is None:
            await self._monitor_loop()
            return

        while True:
            if self.reset.is_active():
                await self._on_reset()
                await self.reset.wait_inactive(self.clock)

            winner, _ = await race(
                self._monitor_loop(),
                self.reset.wait_active(self.clock),
            )
            if winner == 1:
                await self._on_reset()
                await self.reset.wait_inactive(self.clock)
                continue
            return

    @abstractmethod
    async def _monitor_loop(self) -> None:
        """Continuously observe ``self.bus`` and call ``self._emit(tx)``."""

    async def _on_reset(self) -> None:
        """Optional hook invoked when reset asserts and aborts any partial capture."""
