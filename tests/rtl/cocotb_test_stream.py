"""Real cocotb 2.0 + Verilator integration tests for ``cocotb-bfm``."""

from __future__ import annotations

from typing import Any

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject
from cocotb.triggers import ClockCycles, RisingEdge

from cocotb_bfm import (
    AsyncSourceAdapter,
    ComponentLifecycleError,
    Reset,
    ResetError,
    ResetPolicy,
    Scoreboard,
)
from tests.rtl.stream_bfm import (
    DummyStreamBus,
    DummyStreamDriver,
    DummyStreamMonitor,
    DummyStreamTx,
    OneBitFlagBus,
)


async def _init_dut(dut: HierarchyObject) -> None:
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())  # type: ignore[arg-type]
    dut.rst_n.value = 0
    dut.raw_in_valid.value = 0
    dut.raw_in_data.value = 0
    dut.raw_in_last.value = 0
    dut.raw_out_ready.value = 1
    dut.if_in.valid.value = 0
    dut.if_in.data.value = 0
    dut.if_in.last.value = 0
    dut.if_out.ready.value = 1
    dut.lanes_if[0].valid.value = 0
    dut.lanes_if[0].data.value = 0
    dut.lanes_if[0].last.value = 0
    dut.lanes_if[1].ready.value = 1
    dut.onebit_flag.value = 0
    await ClockCycles(dut.clk, 3)  # type: ignore[arg-type]
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)  # type: ignore[arg-type]


@cocotb.test()
async def test_sv_interface_binding_and_scoreboard(dut: HierarchyObject) -> None:
    """Bind real SystemVerilog interfaces (`if_in`, `if_out`) and verify end-to-end with model."""
    await _init_dut(dut)

    in_bus = DummyStreamBus.from_interface(dut.if_in)
    out_bus = DummyStreamBus.from_interface(dut.if_out)
    rst = Reset(dut.rst_n, active_low=True, sync=True)

    # Toggle backpressure on out_bus.ready while transactions stream through.
    async def _backpressure() -> None:
        pattern = [1, 0, 1, 1, 0, 0, 1]
        idx = 0
        while True:
            assert out_bus.ready is not None
            out_bus.ready.value = pattern[idx % len(pattern)]
            idx += 1
            await RisingEdge(dut.clk)  # type: ignore[arg-type]

    bp_task = cocotb.start_soon(_backpressure())

    try:
        async with (
            DummyStreamDriver(in_bus, dut.clk, reset=rst, name="if_drv") as drv,
            DummyStreamMonitor(in_bus, dut.clk, reset=rst, name="if_in_mon") as in_mon,
            DummyStreamMonitor(out_bus, dut.clk, reset=rst, name="if_out_mon") as out_mon,
            Scoreboard(name="sv_if_sb") as sb,
        ):
            out_ch = sb.add_channel("out", out_mon)
            sb.connect_model(
                in_mon,
                lambda tx: DummyStreamTx(data=tx.data ^ 0x5A, last=tx.last),
                out_ch,
            )

            txs = [
                DummyStreamTx(data=0x00, last=0),
                DummyStreamTx(data=0x12, last=0),
                DummyStreamTx(data=0xAB, last=1),
                DummyStreamTx(data=0xFF, last=0),
                DummyStreamTx(data=0x5A, last=1),
            ]
            await drv.send_many(txs)
            await ClockCycles(dut.clk, 10)  # type: ignore[arg-type]

            assert out_ch.matched_count == len(txs)
            assert drv.count == len(txs)
            assert out_mon.count == len(txs)
    finally:
        bp_task.cancel()


@cocotb.test()
async def test_flat_prefix_and_sv_interface_array_and_1bit_vector(dut: HierarchyObject) -> None:
    """Verify `from_prefix`, `from_interface_array`, and 1-bit `logic [0:0]` tolerance."""
    await _init_dut(dut)

    # 1. Flat prefixed bus (`raw_in_*` -> `raw_out_*`, XOR 0xA5)
    raw_in = DummyStreamBus.from_prefix(dut, "raw_in")
    raw_out = DummyStreamBus.from_prefix(dut, "raw_out")

    # 2. SystemVerilog interface array (`lanes_if[0]` -> `lanes_if[1]`, XOR 0xFF)
    lanes = DummyStreamBus.from_interface_array(dut.lanes_if)
    assert len(lanes) == 2

    # 3. 1-bit `logic [0:0]` vector tolerance on real Verilator handles
    flag_bus = OneBitFlagBus.from_entity(dut)
    flag_bus.flag.value = 1
    await RisingEdge(dut.clk)  # type: ignore[arg-type]
    assert int(flag_bus.echo.value) == 1

    async with (
        DummyStreamDriver(raw_in, dut.clk, name="raw_drv") as raw_drv,
        DummyStreamMonitor(raw_out, dut.clk, name="raw_mon") as raw_mon,
        DummyStreamDriver(lanes[0], dut.clk, name="lane_drv") as lane_drv,
        DummyStreamMonitor(lanes[1], dut.clk, name="lane_mon") as lane_mon,
        Scoreboard(name="multi_bus_sb") as sb,
    ):
        raw_ch = sb.add_channel("raw", raw_mon)
        lane_ch = sb.add_channel("lane", lane_mon)

        raw_ch.expect(DummyStreamTx(data=0x33 ^ 0xA5, last=1))
        lane_ch.expect(DummyStreamTx(data=0x33 ^ 0xFF, last=0))

        await raw_drv.send(DummyStreamTx(data=0x33, last=1))
        await lane_drv.send(DummyStreamTx(data=0x33, last=0))
        await ClockCycles(dut.clk, 4)  # type: ignore[arg-type]


@cocotb.test()
async def test_explicit_lifecycle_and_reset_flush_and_pause(dut: HierarchyObject) -> None:
    """Verify explicit start/stop, ResetPolicy.FLUSH (default), and ResetPolicy.PAUSE."""
    await _init_dut(dut)

    in_bus = DummyStreamBus.from_interface(dut.if_in)
    out_bus = DummyStreamBus.from_interface(dut.if_out)
    rst = Reset(dut.rst_n, active_low=True, sync=True)

    # Explicit lifecycle: not running until started; double-start raises.
    drv_flush = DummyStreamDriver(in_bus, dut.clk, reset=rst, name="flush_drv")
    assert not drv_flush.running
    drv_flush.start()
    assert drv_flush.running
    try:
        drv_flush.start()
        raise AssertionError("Expected ComponentLifecycleError on double start")
    except ComponentLifecycleError:
        pass

    # Stall output so driver blocks mid-transaction, then assert reset under default FLUSH.
    assert out_bus.ready is not None
    out_bus.ready.value = 0

    # Fill the 1-deep pipe first so the second transaction stalls on in_ready == 0.
    await drv_flush.send(DummyStreamTx(data=0x11))
    h_stalled = drv_flush.enqueue(DummyStreamTx(data=0x22))
    h_queued = drv_flush.enqueue(DummyStreamTx(data=0x33))
    await ClockCycles(dut.clk, 2)  # type: ignore[arg-type]
    assert not h_stalled.done

    # Assert synchronous active-low reset.
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)  # type: ignore[arg-type]

    # Both stalled and queued transactions must fail with ResetError and bus must be idle.
    for h in (h_stalled, h_queued):
        try:
            await h.wait()
            raise AssertionError("Expected ResetError for flushed transaction")
        except ResetError:
            pass
    assert int(in_bus.valid.value) == 0

    # Release reset and verify post-reset recovery.
    out_bus.ready.value = 1
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)  # type: ignore[arg-type]

    async with DummyStreamMonitor(out_bus, dut.clk, reset=rst, name="post_rst_mon") as mon:
        await drv_flush.send(DummyStreamTx(data=0x44, last=1))
        got = await mon.recv()
        assert got == DummyStreamTx(data=0x44 ^ 0x5A, last=1)
    await drv_flush.stop()
    assert not drv_flush.running

    # Now test explicit ResetPolicy.PAUSE: interrupted transaction retries after reset!
    async with (
        DummyStreamDriver(
            in_bus,
            dut.clk,
            reset=rst,
            reset_policy=ResetPolicy.PAUSE,
            name="pause_drv",
        ) as drv_pause,
        DummyStreamMonitor(out_bus, dut.clk, reset=rst, name="pause_mon") as pause_mon,
    ):
        out_bus.ready.value = 0
        await drv_pause.send(DummyStreamTx(data=0x01))
        h_retry = drv_pause.enqueue(DummyStreamTx(data=0x77, last=1))
        await ClockCycles(dut.clk, 2)  # type: ignore[arg-type]
        assert not h_retry.done

        # Pulse reset, then unstall.
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 2)  # type: ignore[arg-type]
        assert int(in_bus.valid.value) == 0
        out_bus.ready.value = 1
        dut.rst_n.value = 1

        # Transaction 0x77 should complete cleanly after reset deasserts!
        await h_retry.wait()
        got_retry = await pause_mon.recv()
        assert got_retry == DummyStreamTx(data=0x77 ^ 0x5A, last=1)


@cocotb.test()
async def test_cocotbext_adapter_and_legacy_scoreboard_interop(dut: HierarchyObject) -> None:
    """Verify `AsyncSourceAdapter` (cocotbext sink style) and legacy `add_callback` interop."""
    await _init_dut(dut)

    in_bus = DummyStreamBus.from_interface(dut.if_in)
    out_bus = DummyStreamBus.from_interface(dut.if_out)

    # Simulate a cocotbext-style sink exposing `async def recv()`.
    class FakeCocotbextSink:
        def __init__(self, mon: DummyStreamMonitor) -> None:
            self._mon = mon

        async def recv(self) -> DummyStreamTx:
            return await self._mon.recv()

    # Simulate legacy cocotb_bus.scoreboard.Scoreboard.add_interface(monitor, expected).
    legacy_received: list[Any] = []

    class LegacyScoreboardContract:
        def add_interface(self, monitor: Any, expected: list[Any]) -> None:
            def _cb(tx: Any) -> None:
                exp = expected.pop(0)
                assert tx == exp
                legacy_received.append(tx)

            monitor.add_callback(_cb)

    async with (
        DummyStreamDriver(in_bus, dut.clk, name="interop_drv") as drv,
        DummyStreamMonitor(out_bus, dut.clk, name="interop_mon") as mon,
    ):
        sink = FakeCocotbextSink(mon)
        adapter = AsyncSourceAdapter.from_sink(sink, name="ext_sink")
        legacy_expected = [DummyStreamTx(data=0x0F ^ 0x5A), DummyStreamTx(data=0xF0 ^ 0x5A)]
        LegacyScoreboardContract().add_interface(mon, legacy_expected)

        async with adapter, Scoreboard(name="adapter_sb") as sb:
            ch = sb.add_channel("ext", adapter)
            ch.expect_many([DummyStreamTx(data=0x0F ^ 0x5A), DummyStreamTx(data=0xF0 ^ 0x5A)])

            await drv.send(DummyStreamTx(data=0x0F))
            await drv.send(DummyStreamTx(data=0xF0))
            await ClockCycles(dut.clk, 4)  # type: ignore[arg-type]

        assert legacy_expected == []
        assert len(legacy_received) == 2
