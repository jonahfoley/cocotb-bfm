"""Generic, composable, reset-aware ``Driver`` base class."""

from __future__ import annotations

from abc import abstractmethod
from asyncio import CancelledError
from collections.abc import Iterable
from typing import Any, Generic, TypeVar

from cocotb.handle import LogicObject
from cocotb.queue import Queue, QueueEmpty
from cocotb.triggers import Event

from cocotb_bfm.bus.base import Bus
from cocotb_bfm.bus.spec import Role
from cocotb_bfm.components._concurrency import race
from cocotb_bfm.components.base import Component
from cocotb_bfm.components.reset import Reset, ResetPolicy
from cocotb_bfm.exceptions import ResetError

BusT = TypeVar("BusT", bound=Bus)
TxT = TypeVar("TxT")
ReqT = TypeVar("ReqT")
RespT = TypeVar("RespT")


class TxHandle(Generic[TxT, RespT]):
    """Awaitable completion handle returned by ``Driver.enqueue`` or ``RequestDriver``."""

    def __init__(self, transaction: TxT) -> None:
        self.transaction: TxT = transaction
        self._event: Event = Event()
        self._done: bool = False
        self._result: RespT | None = None
        self._error: BaseException | None = None

    @property
    def done(self) -> bool:
        """Return ``True`` once the transaction has completed or been aborted."""
        return self._done

    @property
    def error(self) -> BaseException | None:
        """Return any exception raised while driving this transaction (e.g., ``ResetError``)."""
        return self._error

    def _set_result(self, result: RespT) -> None:
        if not self._done:
            self._result = result
            self._done = True
            self._event.set()

    def _set_done(self) -> None:
        if not self._done:
            self._done = True
            self._event.set()

    def _set_error(self, exc: BaseException) -> None:
        if not self._done:
            self._done = True
            self._error = exc
            self._event.set()

    async def wait(self) -> RespT:
        """Await transaction completion, returning the response or re-raising any error."""
        if not self._done:
            await self._event.wait()
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]


class Driver(Component, Generic[BusT, TxT]):
    """Composable, statically typed driver that operates on an externally owned :class:`Bus`.

    Subclasses implement :meth:`_drive_transaction` (and optionally :meth:`_drive_idle`
    or :meth:`_on_reset`). Start the driver explicitly with :meth:`start` or
    ``async with driver:`` before calling :meth:`send`.

    Reset defaults to :attr:`ResetPolicy.FLUSH` (cancel in-flight transfer, drive bus
    idle, and clear queued items). Override explicitly via keyword-only ``reset_policy``
    or by overriding :meth:`_on_reset`.
    """

    def __init__(
        self,
        bus: BusT,
        clock: LogicObject | Any,
        *,
        reset: Reset | None = None,
        role: Role = Role.INITIATOR,
        name: str | None = None,
        max_queue_size: int = 0,
        reset_policy: ResetPolicy = ResetPolicy.FLUSH,
    ) -> None:
        super().__init__(name=name)
        self.bus: BusT = bus
        self.clock: LogicObject = clock
        self.reset: Reset | None = reset
        self.role: Role = role
        self.reset_policy: ResetPolicy = reset_policy

        self._queue: Queue[TxHandle[TxT, Any]] = Queue(maxsize=max_queue_size)
        self._in_flight: TxHandle[TxT, Any] | None = None
        self._idle_event: Event = Event()
        self._idle_event.set()
        self._tx_count: int = 0

    @property
    def pending(self) -> int:
        """Number of transactions waiting in the driver queue (excluding any in-flight item)."""
        return self._queue.qsize()

    @property
    def busy(self) -> bool:
        """Return ``True`` if a transaction is currently in flight or queued."""
        return self._in_flight is not None or not self._queue.empty()

    @property
    def count(self) -> int:
        """Total number of transactions successfully driven."""
        return self._tx_count

    def enqueue(self, tx: TxT) -> TxHandle[TxT, None]:
        """Enqueue ``tx`` without blocking and return an awaitable :class:`TxHandle`."""
        handle: TxHandle[TxT, None] = TxHandle(tx)
        self._idle_event.clear()
        self._queue.put_nowait(handle)
        return handle

    async def send(self, tx: TxT) -> None:
        """Enqueue ``tx`` and await its completion."""
        handle: TxHandle[TxT, None] = TxHandle(tx)
        self._idle_event.clear()
        await self._queue.put(handle)
        await handle.wait()

    async def send_many(self, transactions: Iterable[TxT]) -> None:
        """Enqueue all ``transactions`` in order and await completion of the entire batch."""
        handles: list[TxHandle[TxT, None]] = []
        for tx in transactions:
            handle: TxHandle[TxT, None] = TxHandle(tx)
            self._idle_event.clear()
            await self._queue.put(handle)
            handles.append(handle)
        for handle in handles:
            await handle.wait()

    async def wait_idle(self) -> None:
        """Block until the queue is empty and no transaction is in flight."""
        if not self.busy:
            return
        await self._idle_event.wait()

    def _update_idle_state(self) -> None:
        if self._in_flight is None and self._queue.empty():
            self._idle_event.set()
        else:
            self._idle_event.clear()

    def _flush_queue(self, reason: str) -> None:
        exc = ResetError(reason)
        if self._in_flight is not None:
            self._in_flight._set_error(exc)
            self._in_flight = None
        while True:
            try:
                queued = self._queue.get_nowait()
            except QueueEmpty:
                break
            queued._set_error(exc)
        self._update_idle_state()

    async def _run(self) -> None:
        await self._drive_idle()

        if self.reset is not None and self.reset.is_active():
            await self._on_reset()
            if self.reset_policy is ResetPolicy.FLUSH:
                self._flush_queue("Driver flushed while reset was asserted")
            await self.reset.wait_inactive(self.clock)

        paused_handle: TxHandle[TxT, Any] | None = None

        while True:
            if paused_handle is not None:
                handle = paused_handle
                paused_handle = None
            else:
                handle_or_reset = await self._next_handle_or_reset()
                if handle_or_reset is None:
                    # Reset asserted while idle.
                    await self._handle_reset_event()
                    continue
                handle = handle_or_reset

            self._in_flight = handle
            self._update_idle_state()

            interrupted = await self._drive_with_reset_guard(handle)
            if interrupted:
                if self.reset_policy is ResetPolicy.PAUSE:
                    paused_handle = handle
                    self._in_flight = None
                    await self._handle_reset_event(flush=False)
                else:
                    await self._handle_reset_event(flush=True)
                continue

            self._in_flight = None
            self._update_idle_state()

    async def _next_handle_or_reset(self) -> TxHandle[TxT, Any] | None:
        """Await the next queued transaction, returning ``None`` if reset asserts first."""
        if self.reset is None:
            return await self._queue.get()

        if self.reset.is_active():
            return None

        winner, result = await race(
            self._queue.get(),
            self.reset.wait_active(self.clock),
        )
        if winner == 1:
            return None
        handle: TxHandle[TxT, Any] = result
        return handle

    async def _execute_transaction(self, tx: TxT) -> Any:
        return await self._drive_transaction(tx)

    async def _drive_with_reset_guard(self, handle: TxHandle[TxT, Any]) -> bool:
        """Drive ``handle.transaction``, returning ``True`` if interrupted by reset."""
        if self.reset is None:
            try:
                res = await self._execute_transaction(handle.transaction)
            except CancelledError:
                raise
            except Exception as exc:
                handle._set_error(exc)
                raise
            else:
                self._tx_count += 1
                handle._set_result(res)
                return False

        if self.reset.is_active():
            return True

        try:
            winner, res = await race(
                self._execute_transaction(handle.transaction),
                self.reset.wait_active(self.clock),
            )
        except CancelledError:
            raise
        except Exception as exc:
            handle._set_error(exc)
            raise

        if winner == 1:
            return True

        self._tx_count += 1
        handle._set_result(res)
        return False

    async def _handle_reset_event(self, *, flush: bool | None = None) -> None:
        assert self.reset is not None
        should_flush = (self.reset_policy is ResetPolicy.FLUSH) if flush is None else flush
        await self._on_reset()
        if should_flush:
            self._flush_queue("Driver flushed on reset assertion")
        await self.reset.wait_inactive(self.clock)

    @abstractmethod
    async def _drive_transaction(self, tx: TxT) -> Any:
        """Drive a single transaction onto ``self.bus``."""

    async def _drive_idle(self) -> None:
        """Place driven bus signals into their declared idle state."""
        self.bus.drive_idle(role=self.role)

    async def _on_reset(self) -> None:
        """Hook invoked when reset asserts. Defaults to driving idle values."""
        await self._drive_idle()


class RequestDriver(Driver[BusT, ReqT], Generic[BusT, ReqT, RespT]):
    """Request/response driver for memory-mapped and CSR protocols (e.g., Avalon-MM, APB).

    Subclasses implement :meth:`_handle_request` returning a statically typed ``RespT``.
    Callers await :meth:`request` (or :meth:`enqueue_request`) to receive the typed
    response with full reset supervision and queueing guarantees.
    """

    def enqueue_request(self, req: ReqT) -> TxHandle[ReqT, RespT]:
        """Enqueue a request without blocking and return a typed :class:`TxHandle`."""
        handle: TxHandle[ReqT, RespT] = TxHandle(req)
        self._idle_event.clear()
        self._queue.put_nowait(handle)
        return handle

    async def request(self, req: ReqT) -> RespT:
        """Enqueue ``req``, await completion, and return the typed response ``RespT``."""
        handle: TxHandle[ReqT, RespT] = TxHandle(req)
        self._idle_event.clear()
        await self._queue.put(handle)
        return await handle.wait()

    async def _drive_transaction(self, tx: ReqT) -> RespT:
        return await self._handle_request(tx)

    @abstractmethod
    async def _handle_request(self, req: ReqT) -> RespT:
        """Execute a single bus request and return its typed response."""
