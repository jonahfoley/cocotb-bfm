"""Example: Incremental Migration & Interop with Legacy ``cocotb-bus`` and
``cocotbext-*``.

Demonstrates three low-overhead interop patterns side by side on real SystemVerilog RTL:

1. **New ``cocotb_bfm.Monitor`` -> Legacy ``cocotb_bus.scoreboard.Scoreboard``**:
   ``Monitor`` exposes ``add_callback()`` so ``legacy_sb.add_interface(mon, expected)``
   works with zero glue code.
2. **New ``cocotb_bfm.Bus`` (bound to an SV ``interface``) -> Legacy driver via
   :func:`~cocotb_bfm.as_legacy_bus`**:
   Wraps a statically typed ``Bus`` so legacy components expecting ``_signals`` /
   ``_optional_signals`` / duck-typed handle attributes can drive an SV interface.
3. **Existing ``cocotbext-*`` async sink (``recv()`` / ``read()``) -> Modern
   ``cocotb_bfm.Scoreboard`` via :class:`~cocotb_bfm.AsyncSourceAdapter`**:
   Bridges ``cocotbext-axi``, ``cocotbext-spi``, ``cocotbext-uart``, etc. into
   ``cocotb_bfm.Scoreboard`` without rewriting those protocol VIPs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge

from cocotb_bfm import (
    AsyncSourceAdapter,
    Bus,
    Direction,
    Monitor,
    Reset,
    Scoreboard,
    VectorObject,
    as_legacy_bus,
    signal,
    sub_bus,
)


class _FallbackLegacyScoreboard:
    """Minimal stand-in for ``cocotb_bus.scoreboard.Scoreboard`` when not installed."""

    def __init__(self, dut: Any, *, fail_immediately: bool = True) -> None:
        self.dut = dut
        self.fail_immediately = fail_immediately
        self.errors = 0

    def add_interface(
        self,
        monitor: Any,
        expected_output: list[Any],
        compare_fn: Callable[[Any, Any], bool] | None = None,
        reorder_depth: int = 0,
        strict_type: bool = True,
    ) -> None:
        del reorder_depth, strict_type

        def _check(got: Any) -> None:
            if not expected_output:
                self.errors += 1
                raise AssertionError(f"Unexpected transaction: {got!r}")
            exp = expected_output.pop(0)
            ok = compare_fn(got, exp) if compare_fn is not None else (got == exp)
            if not ok:
                self.errors += 1
                if self.fail_immediately:
                    raise AssertionError(f"Mismatch: expected {exp!r}, got {got!r}")

        monitor.add_callback(_check)

    @property
    def result(self) -> Exception | None:
        if self.errors:
            return AssertionError(f"{self.errors} scoreboard mismatch(es)")
        return None


# Use real `cocotb_bus.scoreboard.Scoreboard` when `cocotb-bus` is installed;
# otherwise fall back cleanly so this example runs out of the box without extra
# installs.
LegacyScoreboardCls: Any
try:
    from cocotb_bus.scoreboard import (  # type: ignore[import-not-found]
        Scoreboard as _RealLegacyScoreboard,
    )

    LegacyScoreboardCls = _RealLegacyScoreboard
    USING_REAL_COCOTB_BUS = True
except ModuleNotFoundError:
    LegacyScoreboardCls = _FallbackLegacyScoreboard
    USING_REAL_COCOTB_BUS = False


# ---------------------------------------------------------------------------
# 1. Typed cocotb-bfm Buses & Monitor (zero untyped `dut.*` attribute lookups!)
# ---------------------------------------------------------------------------


class ByteStreamBus(Bus):
    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    data: VectorObject = signal(direction=Direction.INITIATOR, width=8, idle_value=0)
    ready: LogicObject = signal(direction=Direction.TARGET, idle_value=1)


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
# 2. Simulated legacy cocotb-bus driver & cocotbext-style async sink
# ---------------------------------------------------------------------------


class LegacyStyleDriver:
    """Example legacy driver expecting ``bus._signals`` and duck-typed attributes."""

    def __init__(self, bus: Any, clock: LogicObject) -> None:
        assert "valid" in bus._signals and "data" in bus._signals
        self.bus = bus
        self.clock = clock

    async def send_byte(self, value: int) -> None:
        self.bus.valid.value = 1
        self.bus.data.value = value
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.ready.value) == 1:
                break
        self.bus.valid.value = 0


class CocotbextStyleSink:
    """Example ``cocotbext-*`` sink (like ``AxiStreamSink`` / ``UartSink``) exposing
    ``recv()``.
    """

    def __init__(self, mon: ByteStreamMonitor) -> None:
        self._mon = mon

    async def recv(self) -> ByteTx:
        return await self._mon.recv()


# ---------------------------------------------------------------------------
# 3. End-to-End Cocotb Test Demonstrating All 3 Interop Patterns Together
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
    """Run all three interop patterns simultaneously on a real SV interface loopback."""
    top = LoopbackTopBus.from_interface(dut)
    await _reset_dut(top)
    dut._log.info("Using real cocotb-bus package: %s", USING_REAL_COCOTB_BUS)

    rst = Reset(top.rst_n, active_low=True, sync=True)

    # Pattern 2: Wrap typed SV-interface Bus with `as_legacy_bus()` for a legacy driver.
    legacy_driver = LegacyStyleDriver(as_legacy_bus(top.in_if, name="in_if"), top.clk)

    # Pattern 1: Plug a new `cocotb_bfm.Monitor` straight into legacy
    # `cocotb_bus.Scoreboard`.
    legacy_expected = [ByteTx(0x11), ByteTx(0x22), ByteTx(0x33), ByteTx(0x44)]
    legacy_sb = LegacyScoreboardCls(dut, fail_immediately=True)

    async with ByteStreamMonitor(
        top.out_if, top.clk, reset=rst, name="out_mon"
    ) as out_mon:
        legacy_sb.add_interface(out_mon, legacy_expected)

        # Pattern 3: Plug a `cocotbext-*`-style async `recv()` sink into
        # `cocotb_bfm.Scoreboard`.
        ext_sink = CocotbextStyleSink(out_mon)
        adapter = AsyncSourceAdapter.from_sink(ext_sink, name="ext_sink_adapter")

        async with adapter, Scoreboard(name="modern_sb") as modern_sb:
            ch = modern_sb.add_channel("loopback", adapter)
            ch.expect_many([ByteTx(0x11), ByteTx(0x22), ByteTx(0x33), ByteTx(0x44)])

            for byte in (0x11, 0x22, 0x33, 0x44):
                await legacy_driver.send_byte(byte)

            await ClockCycles(top.clk, 4)

    # Confirm legacy scoreboard drained and recorded zero errors.
    assert legacy_expected == []
    if legacy_sb.result is not None:
        raise legacy_sb.result
