"""Low-overhead adapters for ``cocotbext-*`` and legacy ``cocotb-bus`` interop."""

from __future__ import annotations

import contextlib
import inspect
import logging
from asyncio import CancelledError
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

import cocotb
from cocotb.queue import Queue, QueueEmpty
from cocotb.task import Task

from cocotb_bfm.bus.base import Bus
from cocotb_bfm.components.base import Component
from cocotb_bfm.components.monitor import MonitorProtocol

TxT = TypeVar("TxT")

try:
    from cocotb_bus.bus import Bus as _RealCocotbBus  # type: ignore[import-untyped]
except ModuleNotFoundError:
    _RealCocotbBus = object

try:
    from cocotb_bus.monitors import (  # type: ignore[import-untyped]
        Monitor as _RealCocotbMonitor,
    )
except ModuleNotFoundError:
    _RealCocotbMonitor = object


class AsyncSourceAdapter(Component, Generic[TxT]):
    """Adapt any ``cocotbext-*`` async sink (``recv()``/``read()``) to a monitor.

    Allows sinks from ``cocotbext-axi`` (``AxiStreamSink.recv``), ``cocotbext-uart``
    (``UartSink.read``), ``cocotbext-spi`` (``SpiMaster.read``), or any custom async
    pull function to feed directly into :class:`~cocotb_bfm.scoreboard.Scoreboard`
    or legacy ``cocotb_bus.scoreboard.Scoreboard`` (via :func:`as_legacy_monitor`).
    """

    def __init__(
        self,
        pull: Callable[[], Awaitable[TxT]],
        *,
        name: str = "AsyncSourceAdapter",
        transform: Callable[[TxT], Any] | None = None,
    ) -> None:
        """Initialize an adapter polling ``pull`` into registered callbacks.

        Args:
            pull: Zero-argument async callable returning the next transaction.
            name: Human-readable component name for logging and scoreboards.
            transform: Optional function transforming each pulled item before emission.
        """
        super().__init__(name=name)
        self._pull = pull
        self._transform = transform
        self._callbacks: list[Callable[[Any], Any]] = []
        self._count: int = 0
        self._async_cb_queue: Queue[Awaitable[Any]] = Queue()
        self._async_cb_task: Task[None] | None = None

    @classmethod
    def from_sink(
        cls,
        sink: Any,
        *,
        method: str | None = None,
        name: str | None = None,
        transform: Callable[[Any], Any] | None = None,
    ) -> AsyncSourceAdapter[Any]:
        """Auto-detect ``recv`` or ``read`` on a ``cocotbext-*`` sink object."""
        if method is not None:
            pull_fn = getattr(sink, method)
        elif callable(getattr(sink, "recv", None)):
            pull_fn = sink.recv
        elif callable(getattr(sink, "read", None)):
            pull_fn = sink.read
        else:
            raise TypeError(
                f"Cannot adapt {type(sink).__name__}: expected a 'recv' or 'read' "
                "coroutine method, or pass method='...' explicitly."
            )
        resolved_name = name if name is not None else type(sink).__name__
        return cls(pull_fn, name=resolved_name, transform=transform)

    @property
    def count(self) -> int:
        """Total number of transactions forwarded by this adapter."""
        return self._count

    def add_callback(self, callback: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """Register a sync or async callback invoked for every pulled item."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)
        return callback

    def remove_callback(self, callback: Callable[[Any], Any]) -> None:
        """Unregister a previously registered callback."""
        self._callbacks.remove(callback)

    def _enqueue_async_cb(self, awaitable: Awaitable[Any]) -> None:
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
        while True:
            item = await self._pull()
            out = self._transform(item) if self._transform is not None else item
            self._count += 1
            for cb in list(self._callbacks):
                res = cb(out)
                if inspect.isawaitable(res):
                    self._enqueue_async_cb(res)


class QueueAdapter(AsyncSourceAdapter[TxT]):
    """Adapt a cocotb 2.0 :class:`~cocotb.queue.Queue` to :class:`MonitorProtocol`."""

    def __init__(
        self,
        queue: Queue[TxT],
        *,
        name: str = "QueueAdapter",
        transform: Callable[[TxT], Any] | None = None,
    ) -> None:
        """Initialize an adapter consuming items from ``queue``."""
        super().__init__(queue.get, name=name, transform=transform)
        self.queue = queue


class LegacyBusShim(_RealCocotbBus):  # type: ignore[misc]
    """Present a typed :class:`Bus` as a legacy ``cocotb_bus.bus.Bus``.

    - Subclasses ``cocotb_bus.bus.Bus`` when ``cocotb-bus`` is installed so
      ``isinstance(shim, cocotb_bus.bus.Bus)`` evaluates to ``True``.
    - Populates ``_signals`` with all bound (non-``None``) signal handles, matching
      real ``cocotb_bus.bus.Bus._add_signal``.
    - Raises ``AttributeError`` when accessing an unbound optional signal so
      ``hasattr(shim, "tkeep")`` returns ``False`` (matching legacy ``cocotb-bus`` and
      preventing ``None.value`` crashes in ``cocotbext-*`` extensions).
    """

    def __init__(
        self, bus: Bus, *, name: str | None = None, entity: Any = None
    ) -> None:
        """Wrap ``bus`` without invoking ``cocotb_bus.bus.Bus.__init__``."""
        # Intentionally bypass cocotb_bus.bus.Bus.__init__ so handles come from `bus`.
        self._bus = bus
        self._name = name if name is not None else type(bus).__name__
        self._entity = entity
        specs = bus.signal_specs()
        self._signals: dict[str, Any] = {
            k: getattr(bus, k) for k in specs if getattr(bus, k) is not None
        }
        self._optional_signals: dict[str, Any] = {
            k: getattr(bus, k)
            for k, s in specs.items()
            if s.optional and getattr(bus, k) is not None
        }

    def __getattr__(self, item: str) -> Any:
        specs = self._bus.signal_specs()
        if item in specs:
            val = getattr(self._bus, item)
            if val is None:
                raise AttributeError(
                    f"Optional signal {item!r} is not present on {self._name!r}"
                )
            return val
        return getattr(self._bus, item)

    def drive(self, obj: Any, strict: bool = False) -> None:
        """Legacy ``cocotb_bus.bus.Bus.drive(obj, strict=False)`` compatibility."""
        for attr_name, hdl in self._signals.items():
            if not hasattr(obj, attr_name):
                if strict:
                    raise AttributeError(
                        f"Unable to drive {self._name}.{attr_name}: missing on "
                        f"{type(obj).__qualname__}"
                    )
                continue
            hdl.value = getattr(obj, attr_name)

    def __repr__(self) -> str:
        return f"LegacyBusShim({self._bus!r})"


class LegacyMonitorShim(_RealCocotbMonitor):  # type: ignore[misc]
    """Wrap any :class:`MonitorProtocol` as a ``cocotb_bus.monitors.Monitor``.

    Real ``cocotb_bus.scoreboard.Scoreboard.add_interface()`` performs an explicit
    ``isinstance(monitor, cocotb_bus.monitors.Monitor)`` check. ``LegacyMonitorShim``
    satisfies that ``isinstance`` check **without** invoking ``Monitor.__init__`` (which
    would spawn a broken ``_monitor_recv`` coroutine) and forwards ``add_callback``
    directly to the wrapped ``cocotb_bfm`` monitor or adapter.
    """

    def __init__(self, monitor: MonitorProtocol[Any]) -> None:
        """Wrap ``monitor`` while bypassing ``cocotb_bus.monitors.Monitor.__init__``."""
        # Intentionally bypass cocotb_bus.monitors.Monitor.__init__!
        self._monitor = monitor
        self.name: str = monitor.name
        self.log: logging.Logger = logging.getLogger(f"cocotb.monitor.{monitor.name}")

    def add_callback(self, callback: Callable[[Any], Any]) -> Any:
        """Forward ``callback`` registration to the wrapped monitor."""
        return self._monitor.add_callback(callback)

    def __repr__(self) -> str:
        return f"LegacyMonitorShim({self._monitor!r})"


def as_legacy_bus(
    bus: Bus, *, name: str | None = None, entity: Any = None
) -> LegacyBusShim:
    """Wrap a typed :class:`Bus` in a :class:`LegacyBusShim` for legacy code."""
    return LegacyBusShim(bus, name=name, entity=entity)


def as_legacy_monitor(monitor: MonitorProtocol[Any]) -> LegacyMonitorShim:
    """Wrap ``monitor`` for ``cocotb_bus.scoreboard.Scoreboard.add_interface()``."""
    return LegacyMonitorShim(monitor)
