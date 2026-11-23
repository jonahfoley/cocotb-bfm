"""Generic, composable, reset-aware ``Monitor`` base class with legacy scoreboard interop."""

from __future__ import annotations

import inspect
from abc import abstractmethod
from collections.abc import AsyncIterator, Callable
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

import cocotb
from cocotb.handle import LogicObject
from cocotb.queue import Queue

from cocotb_bfm.bus.base import Bus
from cocotb_bfm.components._concurrency import race
from cocotb_bfm.components.base import Component
from cocotb_bfm.components.reset import Reset

BusT = TypeVar("BusT", bound=Bus)
TxT = TypeVar("TxT")
TxT_co = TypeVar("TxT_co", covariant=True)


@runtime_checkable
class MonitorProtocol(Protocol[TxT_co]):
    """Structural protocol satisfied by :class:`Monitor` and legacy ``cocotb-bus`` monitors.

    Implementing ``add_callback`` allows :class:`Monitor` instances to plug directly
    into legacy ``cocotb_bus.scoreboard.Scoreboard.add_interface()`` as well as the
    modern :class:`~cocotb_bfm.scoreboard.Scoreboard`.
    """

    @property
    def name(self) -> str: ...

    def add_callback(self, callback: Callable[[TxT_co], Any]) -> Any: ...


class Monitor(Component, Generic[BusT, TxT]):
    """Passive, statically typed observer for an externally owned :class:`Bus`.

    Transactions emitted via :meth:`_emit` are simultaneously available through:

    1. Async queue / iteration: ``await monitor.recv()`` or ``async for tx in monitor:``
    2. Typed sync or async callbacks: ``monitor.add_callback(fn)``
    3. Legacy ``cocotb_bus.scoreboard.Scoreboard.add_interface(monitor, expected)``

    When a :class:`Reset` is configured, reset assertion automatically cancels the
    active :meth:`_monitor_loop` (discarding any partial multi-cycle capture), runs
    :meth:`_on_reset`, waits for reset deassertion, and cleanly restarts monitoring.
    """

    def __init__(
        self,
        bus: BusT,
        clock: LogicObject | Any,
        *,
        reset: Reset | None = None,
        name: str | None = None,
        max_queue_size: int = 0,
        capture_queue: bool = True,
    ) -> None:
        super().__init__(name=name)
        self.bus: BusT = bus
        self.clock: LogicObject = clock
        self.reset: Reset | None = reset
        self._capture_queue: bool = capture_queue
        self._queue: Queue[TxT] = Queue(maxsize=max_queue_size)
        self._callbacks: list[Callable[[TxT], Any]] = []
        self._rx_count: int = 0

    @property
    def count(self) -> int:
        """Total number of transactions emitted by this monitor."""
        return self._rx_count

    @property
    def pending(self) -> int:
        """Number of unread transactions currently buffered in the capture queue."""
        return self._queue.qsize()

    def add_callback(self, callback: Callable[[TxT], Any]) -> Callable[[TxT], Any]:
        """Register a sync or async callback invoked for every captured transaction.

        Compatible with legacy ``cocotb_bus.scoreboard.Scoreboard.add_interface()``.
        """
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
            self._queue.put_nowait(transaction)
        for callback in list(self._callbacks):
            result = callback(transaction)
            if inspect.isawaitable(result):
                cocotb.start_soon(self._run_async_callback(result))

    async def _run_async_callback(self, awaitable: Any) -> None:
        await awaitable

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
        """Continuously observe ``self.bus`` and call ``self._emit(tx)`` for each transaction."""

    async def _on_reset(self) -> None:
        """Optional hook invoked when reset asserts and aborts any partial capture."""
