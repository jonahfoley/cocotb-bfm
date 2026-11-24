"""Incremental migration and interop with legacy ``cocotb-bus`` and ``cocotbext``.

Demonstrates three low-overhead interop patterns side by side on real SystemVerilog RTL
using the **real** ``cocotb_bus`` package:

1. **New ``cocotb_bfm.Monitor`` -> Real ``cocotb_bus.scoreboard.Scoreboard``**:
   Wrapped with :func:`~cocotb_bfm.as_legacy_monitor` so ``Scoreboard.add_interface``'s
   ``isinstance(monitor, cocotb_bus.monitors.Monitor)`` check succeeds cleanly.
2. **New ``cocotb_bfm.Bus`` (bound to an SV ``interface``) -> Legacy driver via
   :func:`~cocotb_bfm.as_legacy_bus`**:
   Wraps a statically typed ``Bus`` so legacy components checking ``isinstance(...,
   cocotb_bus.bus.Bus)``, ``_signals``, or ``hasattr(bus, optional_sig)`` can drive an
   SV interface directly.
3. **Existing ``cocotbext-*`` async sink (``recv()`` / ``read()``) -> Modern
   ``cocotb_bfm.Scoreboard`` via :class:`~cocotb_bfm.AsyncSourceAdapter`**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb_bus.bus import Bus as LegacyCocotbBus  # type: ignore[import-untyped]
from cocotb_bus.scoreboard import (  # type: ignore[import-untyped]
    Scoreboard as LegacyCocotbScoreboard,
)

from cocotb_bfm import (
    AsyncSourceAdapter,
    Bus,
    Direction,
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
# 1. Typed cocotb-bfm Buses & Monitor (bound to SystemVerilog `interface`s)
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


class ByteStreamMonitor(Monitor[ByteStreamBus, ByteTx]):
    async def _monitor_loop(self) -> None:
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.valid.value) == 1 and int(self.bus.ready.value) == 1:
                self._emit(ByteTx(data=self.bus.data.value.to_unsigned()))


# ---------------------------------------------------------------------------
# 2. Legacy cocotb-bus / cocotbext-style components
# ---------------------------------------------------------------------------


class LegacyStyleDriver:
    """Legacy driver checking ``isinstance(..., cocotb_bus.bus.Bus)``, ``_signals``,
    and ``hasattr(bus, 'keep')`` before touching optional signals.
    """

    def __init__(self, bus: Any, clock: LogicObject) -> None:
        assert isinstance(bus, LegacyCocotbBus)
        assert set(bus._signals.keys()) == {"valid", "data", "ready"}
        assert not hasattr(bus, "keep"), "Missing optional signal must not look present"
        self.bus = bus
        self.clock = clock

    async def send_byte(self, value: int) -> None:
        self.bus.valid.value = 1
        self.bus.data.value = value
        if hasattr(self.bus, "keep"):
            self.bus.keep.value = 1
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.ready.value) == 1:
                break
        self.bus.valid.value = 0


class CocotbextStyleSink:
    """Example ``cocotbext-*`` sink (like ``AxiStreamSink``) exposing ``recv()``."""

    def __init__(self, mon: ByteStreamMonitor) -> None:
        self._mon = mon

    async def recv(self) -> ByteTx:
        return await self._mon.recv()


# ---------------------------------------------------------------------------
# 3. End-to-End Cocotb Test Using the Real `cocotb-bus` Package
# ---------------------------------------------------------------------------


async def _reset_dut(top: LoopbackTopBus) -> None:
    Clock(top.clk, 10, unit="ns").start()
    top.rst_n.value = 0
    top.in_if.drive_idle()
    top.out_if.ready.value = 1
    await ClockCycles(top.clk, 3)
    top.rst_n.value = 1
    await RisingEdge(top.clk)


@cocotb.test()
async def test_legacy_cocotb_bus_and_cocotbext_interop(dut: HierarchyObject) -> None:
    """Run all three interop patterns against the real ``cocotb-bus`` package."""
    top = LoopbackTopBus.from_interface(dut)
    await _reset_dut(top)

    rst = Reset(top.rst_n, active_low=True, sync=True)

    # Pattern 2: Wrap typed SV-interface Bus with `as_legacy_bus()` for a legacy driver.
    legacy_driver = LegacyStyleDriver(as_legacy_bus(top.in_if, name="in_if"), top.clk)

    # Pattern 1: Plug a new `cocotb_bfm.Monitor` into REAL `cocotb_bus.Scoreboard`.
    legacy_expected = [ByteTx(0x11), ByteTx(0x22), ByteTx(0x33), ByteTx(0x44)]
    legacy_sb = LegacyCocotbScoreboard(dut, fail_immediately=True)

    async with ByteStreamMonitor(
        top.out_if, top.clk, reset=rst, name="out_mon"
    ) as out_mon:
        legacy_sb.add_interface(as_legacy_monitor(out_mon), legacy_expected)

        # Pattern 3: Plug a `cocotbext-*`-style async `recv()` sink into `Scoreboard`.
        ext_sink = CocotbextStyleSink(out_mon)
        adapter = AsyncSourceAdapter.from_sink(ext_sink, name="ext_sink_adapter")

        async with adapter, Scoreboard(name="modern_sb") as modern_sb:
            ch = modern_sb.add_channel("loopback", adapter)
            ch.expect_many([ByteTx(0x11), ByteTx(0x22), ByteTx(0x33), ByteTx(0x44)])

            for byte in (0x11, 0x22, 0x33, 0x44):
                await legacy_driver.send_byte(byte)

            await ClockCycles(top.clk, 4)

    assert legacy_expected == []
    assert legacy_sb.errors == 0
    # Accessing `legacy_sb.result` runs real `cocotb_bus.Scoreboard`'s final assertions.
    _ = legacy_sb.result
