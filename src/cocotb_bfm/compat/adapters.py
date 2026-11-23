"""Low-overhead adapters for interoperating with ``cocotbext-*`` and legacy ``cocotb-bus``."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

import cocotb
from cocotb.queue import Queue

from cocotb_bfm.bus.base import Bus
from cocotb_bfm.components.base import Component

TxT = TypeVar("TxT")


class AsyncSourceAdapter(Component, Generic[TxT]):
    """Adapt any ``cocotbext-*`` async sink (``recv()``/``read()``) to :class:`MonitorProtocol`.

    Allows sinks from ``cocotbext-axi`` (``AxiStreamSink.recv``), ``cocotbext-uart``
    (``UartSink.read``), ``cocotbext-spi`` (``SpiMaster.read``), or any custom async
    pull function to feed directly into :class:`~cocotb_bfm.scoreboard.Scoreboard`
    or legacy ``cocotb_bus.scoreboard.Scoreboard``.

    Example::

        adapter = AsyncSourceAdapter(uart_sink.read, name="uart_rx")
        sb.add_channel("uart", adapter)
        async with adapter:
            ...
    """

    def __init__(
        self,
        pull: Callable[[], Awaitable[TxT]],
        *,
        name: str = "AsyncSourceAdapter",
        transform: Callable[[TxT], Any] | None = None,
    ) -> None:
        super().__init__(name=name)
        self._pull = pull
        self._transform = transform
        self._callbacks: list[Callable[[Any], Any]] = []
        self._count: int = 0

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
                f"Cannot adapt {type(sink).__name__}: expected a 'recv' or 'read' coroutine "
                "method, or pass method='...' explicitly."
            )
        resolved_name = name if name is not None else type(sink).__name__
        return cls(pull_fn, name=resolved_name, transform=transform)

    @property
    def count(self) -> int:
        return self._count

    def add_callback(self, callback: Callable[[Any], Any]) -> Callable[[Any], Any]:
        if callback not in self._callbacks:
            self._callbacks.append(callback)
        return callback

    def remove_callback(self, callback: Callable[[Any], Any]) -> None:
        self._callbacks.remove(callback)

    async def _run(self) -> None:
        while True:
            item = await self._pull()
            out = self._transform(item) if self._transform is not None else item
            self._count += 1
            for cb in list(self._callbacks):
                res = cb(out)
                if inspect.isawaitable(res):
                    cocotb.start_soon(self._run_async_cb(res))

    async def _run_async_cb(self, awaitable: Awaitable[Any]) -> None:
        await awaitable


class QueueAdapter(AsyncSourceAdapter[TxT]):
    """Adapt a cocotb 2.0 :class:`~cocotb.queue.Queue` to :class:`MonitorProtocol`."""

    def __init__(
        self,
        queue: Queue[TxT],
        *,
        name: str = "QueueAdapter",
        transform: Callable[[TxT], Any] | None = None,
    ) -> None:
        super().__init__(queue.get, name=name, transform=transform)
        self.queue = queue


class LegacyBusShim:
    """Thin compatibility shim presenting a typed :class:`Bus` with legacy ``cocotb-bus`` metadata.

    Most ``cocotbext-*`` components simply duck-type signal attributes on the bus object
    (so passing a :class:`Bus` directly already works). Wrap with :func:`as_legacy_bus`
    only when a third-party extension inspects legacy private attributes such as
    ``_signals``, ``_optional_signals``, or ``_name``.
    """

    def __init__(self, bus: Bus, *, name: str | None = None, entity: Any = None) -> None:
        self._bus = bus
        self._name = name if name is not None else type(bus).__name__
        self._entity = entity
        specs = bus.signal_specs()
        self._signals: dict[str, Any] = {
            k: getattr(bus, k) for k, s in specs.items() if not s.optional
        }
        self._optional_signals: dict[str, Any] = {
            k: getattr(bus, k)
            for k, s in specs.items()
            if s.optional and getattr(bus, k) is not None
        }

    def __getattr__(self, item: str) -> Any:
        return getattr(self._bus, item)

    def __repr__(self) -> str:
        return f"LegacyBusShim({self._bus!r})"


def as_legacy_bus(bus: Bus, *, name: str | None = None, entity: Any = None) -> LegacyBusShim:
    """Wrap a typed :class:`Bus` in a :class:`LegacyBusShim` for legacy ``cocotbext-*`` code."""
    return LegacyBusShim(bus, name=name, entity=entity)
