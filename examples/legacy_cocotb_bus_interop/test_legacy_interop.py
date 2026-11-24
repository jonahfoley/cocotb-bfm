"""Incremental migration and interop with legacy ``cocotb-bus`` and ``cocotbext``.

Demonstrates four low-overhead interop patterns side by side on real SystemVerilog RTL
using the **real** ``cocotb_bus`` package:

1. **New ``cocotb_bfm.Monitor`` -> Real ``cocotb_bus.scoreboard.Scoreboard``**:
   Wrapped with :func:`~cocotb_bfm.as_legacy_monitor` so ``Scoreboard.add_interface``'s
   ``isinstance(monitor, cocotb_bus.monitors.Monitor)`` check succeeds cleanly.
2. **New ``cocotb_bfm.Bus`` (bound to an SV ``interface``) -> Unmodified
   ``cocotb_bus.drivers.BusDriver`` via :func:`~cocotb_bfm.as_legacy_bus`**:
   Wraps a statically typed ``Bus`` so legacy drivers checking ``isinstance(...,
   cocotb_bus.bus.Bus)``, ``_signals``, or ``hasattr(bus, optional_sig)`` can drive an
   SV interface directly.
3. **Unmodified ``cocotb_bus.monitors.BusMonitor`` -> ``cocotb_bfm.Scoreboard``**:
   ``BusMonitor`` already satisfies ``MonitorProtocol`` (``name`` + ``add_callback``),
   so it plugs straight into ``Scoreboard.add_channel()`` with zero adapter.
4. **Existing ``cocotbext-*`` async sink (``recv()`` / ``read()``) -> Modern
   ``cocotb_bfm.Scoreboard`` via :class:`~cocotb_bfm.AsyncSourceAdapter`**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb_bus.bus import Bus as LegacyCocotbBus  # type: ignore[import-untyped]
from cocotb_bus.drivers import (  # type: ignore[import-untyped]
    BusDriver as LegacyCocotbBusDriver,
)
from cocotb_bus.monitors import (  # type: ignore[import-untyped]
    BusMonitor as LegacyCocotbBusMonitor,
)
from cocotb_bus.scoreboard import (  # type: ignore[import-untyped]
    Scoreboard as LegacyCocotbScoreboard,
)

from cocotb_bfm import (
    AsyncSourceAdapter,
    Bus,
    Direction,
    Driver,
    Monitor,
    Reset,
    Scoreboard,
    VectorObject,
    as_legacy_bus,
    as_legacy_monitor,
    signal,
    sub_bus,
)

# ---------------------------------------------------------------------------
# 1. Typed cocotb-bfm Buses, Driver & Monitor (bound to SV `interface`s)
# ---------------------------------------------------------------------------


class ByteStreamBus(Bus):
    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    data: VectorObject = signal(direction=Direction.INITIATOR, width=8, idle_value=0)
    ready: LogicObject = signal(direction=Direction.TARGET, idle_value=1)
    # Optional signal NOT present in `loopback_dut.sv`—verifies `hasattr(shim, "keep")`
    # is False on `LegacyBusShim` just like legacy `cocotb-bus`!
    keep: LogicObject | None = signal(default=None, direction=Direction.INITIATOR)


class LoopbackTopBus(Bus):
    clk: LogicObject = signal(direction=Direction.CLOCK)
    rst_n: LogicObject = signal(direction=Direction.RESET)
    in_if: ByteStreamBus = sub_bus(hdl_name="in_if")
    out_if: ByteStreamBus = sub_bus(hdl_name="out_if")


@dataclass(frozen=True)
class ByteTx:
    data: int


class ByteStreamDriver(Driver[ByteStreamBus, ByteTx]):
    async def _drive_transaction(self, tx: ByteTx) -> None:
        self.bus.valid.value = 1
        self.bus.data.value = tx.data
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.ready.value) == 1:
                break
        self.bus.valid.value = 0


class ByteStreamMonitor(Monitor[ByteStreamBus, ByteTx]):
    async def _monitor_loop(self) -> None:
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.valid.value) == 1 and int(self.bus.ready.value) == 1:
                self._emit(ByteTx(data=self.bus.data.value.to_unsigned()))


# ---------------------------------------------------------------------------
# 2. Legacy cocotb-bus / cocotbext-style components
# ---------------------------------------------------------------------------


class LegacyStyleDriver(LegacyCocotbBusDriver):  # type: ignore[misc]
    """Unmodified ``cocotb_bus.drivers.BusDriver`` subclass (standard legacy pattern).

    Inherits ``BusDriver.__init__(entity, name, clock)`` directly; ``self.bus`` is
    created automatically from ``_signals`` / ``_optional_signals`` and can also be
    swapped for an :func:`~cocotb_bfm.as_legacy_bus` shim when migrating buses.
    """

    _signals: ClassVar[list[str]] = ["valid", "data", "ready"]
    _optional_signals: ClassVar[list[str]] = ["keep"]

    async def _driver_send(self, transaction: int, sync: bool = True) -> None:
        self.bus.valid.value = 1
        self.bus.data.value = transaction
        if hasattr(self.bus, "keep"):
            self.bus.keep.value = 1
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.ready.value) == 1:
                break
        self.bus.valid.value = 0


class LegacyStyleMonitor(LegacyCocotbBusMonitor):  # type: ignore[misc]
    """Unmodified ``cocotb_bus.monitors.BusMonitor`` subclass.

    Plugs directly into ``cocotb_bfm.Scoreboard.add_channel()`` with zero adapter
    because ``BusMonitor`` already exposes ``name`` and ``add_callback()``.
    """

    _signals: ClassVar[list[str]] = ["valid", "data", "ready"]

    async def _monitor_recv(self) -> None:
        while True:
            await RisingEdge(self.clock)
            if (
                not self.in_reset
                and int(self.bus.valid.value) == 1
                and int(self.bus.ready.value) == 1
            ):
                self._recv(ByteTx(data=self.bus.data.value.to_unsigned()))


class CocotbextStyleSink:
    """Example ``cocotbext-*`` sink (like ``AxiStreamSink``) exposing ``recv()``."""

    def __init__(self, mon: ByteStreamMonitor) -> None:
        self._mon = mon

    async def recv(self) -> ByteTx:
        return await self._mon.recv()


# ---------------------------------------------------------------------------
# 3. End-to-End Cocotb Test Using the Real `cocotb-bus` Package
# ---------------------------------------------------------------------------


SAMPLE_BYTES = (0x11, 0x22, 0x33, 0x44)


async def _init_dut(dut: HierarchyObject) -> tuple[LoopbackTopBus, Reset]:
    top = LoopbackTopBus.from_interface(dut)
    rst = Reset(top.rst_n, active_low=True, sync=True)
    Clock(top.clk, 10, unit="ns").start()
    top.rst_n.value = 0
    top.in_if.drive_idle()
    top.out_if.ready.value = 1
    await ClockCycles(top.clk, 3)
    top.rst_n.value = 1
    await RisingEdge(top.clk)
    return top, rst


@cocotb.test()
async def test_new_monitor_with_legacy_scoreboard(dut: HierarchyObject) -> None:
    """Pattern 1: Plug a new ``cocotb_bfm.Monitor`` into ``cocotb_bus.Scoreboard``."""
    top, rst = await _init_dut(dut)
    legacy_expected = [ByteTx(b) for b in SAMPLE_BYTES]
    legacy_sb = LegacyCocotbScoreboard(dut, fail_immediately=True)

    async with (
        ByteStreamDriver(top.in_if, top.clk, reset=rst, name="in_drv") as drv,
        ByteStreamMonitor(top.out_if, top.clk, reset=rst, name="out_mon") as out_mon,
    ):
        legacy_sb.add_interface(as_legacy_monitor(out_mon), legacy_expected)
        await drv.send_many([ByteTx(b) for b in SAMPLE_BYTES])
        await ClockCycles(top.clk, 4)

    assert legacy_expected == []
    assert legacy_sb.errors == 0
    # Accessing `legacy_sb.result` runs real `cocotb_bus.Scoreboard`'s final assertions.
    _ = legacy_sb.result


@cocotb.test()
async def test_legacy_bus_driver_with_as_legacy_bus(dut: HierarchyObject) -> None:
    """Pattern 2: Drive an SV interface from an unmodified ``cocotb_bus.BusDriver``."""
    top, rst = await _init_dut(dut)
    expected_txs = [ByteTx(b) for b in SAMPLE_BYTES]

    # Instantiate standard `BusDriver` and swap `.bus` to an `as_legacy_bus()` shim.
    legacy_driver = LegacyStyleDriver(dut["in_if"], None, top.clk)
    legacy_shim = as_legacy_bus(top.in_if, name="in_if")
    assert isinstance(legacy_shim, LegacyCocotbBus)
    assert set(legacy_shim._signals.keys()) == {"valid", "data", "ready"}
    assert not hasattr(legacy_shim, "keep")
    legacy_driver.bus = legacy_shim

    try:
        async with (
            ByteStreamMonitor(
                top.out_if, top.clk, reset=rst, name="out_mon"
            ) as out_mon,
            Scoreboard(name="legacy_drv_sb") as sb,
        ):
            ch = sb.add_channel("out", out_mon)
            ch.expect_many(expected_txs)

            for byte in SAMPLE_BYTES:
                await legacy_driver.send(byte)

            await ClockCycles(top.clk, 4)
            assert ch.matched_count == len(SAMPLE_BYTES)
    finally:
        legacy_driver.kill()


@cocotb.test()
async def test_legacy_bus_monitor_with_new_scoreboard(dut: HierarchyObject) -> None:
    """Pattern 3: Plug an unmodified ``cocotb_bus.BusMonitor`` into ``Scoreboard``."""
    top, rst = await _init_dut(dut)
    expected_txs = [ByteTx(b) for b in SAMPLE_BYTES]

    # No adapter required: `BusMonitor` already exposes `name` and `add_callback()`.
    legacy_mon = LegacyStyleMonitor(dut["out_if"], None, top.clk, reset_n=top.rst_n)

    try:
        async with (
            ByteStreamDriver(top.in_if, top.clk, reset=rst, name="in_drv") as drv,
            Scoreboard(name="legacy_mon_sb") as sb,
        ):
            ch = sb.add_channel("from_legacy_mon", legacy_mon)
            ch.expect_many(expected_txs)

            await drv.send_many(expected_txs)
            await ClockCycles(top.clk, 4)
            assert ch.matched_count == len(SAMPLE_BYTES)
    finally:
        legacy_mon.kill()


@cocotb.test()
async def test_cocotbext_sink_with_new_scoreboard(dut: HierarchyObject) -> None:
    """Pattern 4: Plug a ``cocotbext-*`` async ``recv()`` sink into ``Scoreboard``."""
    top, rst = await _init_dut(dut)
    expected_txs = [ByteTx(b) for b in SAMPLE_BYTES]

    async with (
        ByteStreamDriver(top.in_if, top.clk, reset=rst, name="in_drv") as drv,
        ByteStreamMonitor(top.out_if, top.clk, reset=rst, name="out_mon") as out_mon,
    ):
        ext_sink = CocotbextStyleSink(out_mon)
        adapter = AsyncSourceAdapter.from_sink(ext_sink, name="ext_sink_adapter")

        async with adapter, Scoreboard(name="cocotbext_sb") as sb:
            ch = sb.add_channel("from_cocotbext", adapter)
            ch.expect_many(expected_txs)

            await drv.send_many(expected_txs)
            await ClockCycles(top.clk, 4)
            assert ch.matched_count == len(SAMPLE_BYTES)
