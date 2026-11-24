"""Real cocotb 2.0 + Verilator integration tests for ``cocotb-bfm``."""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from cocotb_bus.scoreboard import (  # type: ignore[import-untyped]
    Scoreboard as LegacyCocotbScoreboard,
)

from cocotb_bfm import (
    AsyncSourceAdapter,
    Bus,
    ComponentLifecycleError,
    Reset,
    ResetError,
    ResetPolicy,
    Scoreboard,
    as_legacy_monitor,
)
from tests.rtl.stream_bfm import (
    DummyStreamBus,
    DummyStreamDriver,
    DummyStreamMonitor,
    DummyStreamTx,
    OneBitFlagBus,
)


class StreamTbCtrlBus(Bus):
    """Typed top-level clock and reset handles for ``stream_tb_top``."""

    clk: LogicObject
    rst_n: LogicObject


async def _init_dut(
    dut: HierarchyObject,
) -> tuple[StreamTbCtrlBus, DummyStreamBus, DummyStreamBus]:
    tb = StreamTbCtrlBus.from_entity(dut)
    raw_in = DummyStreamBus.from_prefix(dut, "raw_in")
    raw_out = DummyStreamBus.from_prefix(dut, "raw_out")
    if_in = DummyStreamBus.from_interface(dut["if_in"])
    if_out = DummyStreamBus.from_interface(dut["if_out"])
    lanes = DummyStreamBus.from_interface_array(dut["lanes_if"])
    flag_bus = OneBitFlagBus.from_entity(dut)

    Clock(tb.clk, 10, unit="ns").start()
    tb.rst_n.value = 0
    raw_in.drive_idle()
    raw_out.drive_idle()
    if_in.drive_idle()
    if_out.drive_idle()
    lanes[0].drive_idle()
    lanes[1].drive_idle()
    flag_bus.flag.value = 0

    await ClockCycles(tb.clk, 3)
    tb.rst_n.value = 1
    await RisingEdge(tb.clk)
    return tb, if_in, if_out


@cocotb.test()
async def test_sv_interface_binding_and_scoreboard(dut: HierarchyObject) -> None:
    """Bind SV interfaces (`if_in`, `if_out`) and verify end-to-end with model."""
    tb, in_bus, out_bus = await _init_dut(dut)
    rst = Reset(tb.rst_n, active_low=True, sync=True)

    # Toggle backpressure on out_bus.ready while transactions stream through.
    async def _backpressure() -> None:
        pattern = [1, 0, 1, 1, 0, 0, 1]
        idx = 0
        while True:
            assert out_bus.ready is not None
            out_bus.ready.value = pattern[idx % len(pattern)]
            idx += 1
            await RisingEdge(tb.clk)

    bp_task = cocotb.start_soon(_backpressure())

    try:
        async with (
            DummyStreamDriver(in_bus, tb.clk, reset=rst, name="if_drv") as drv,
            DummyStreamMonitor(in_bus, tb.clk, reset=rst, name="if_in_mon") as in_mon,
            DummyStreamMonitor(
                out_bus, tb.clk, reset=rst, name="if_out_mon"
            ) as out_mon,
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
            await ClockCycles(tb.clk, 10)

            assert out_ch.matched_count == len(txs)
            assert drv.count == len(txs)
            assert out_mon.count == len(txs)
    finally:
        bp_task.cancel()


@cocotb.test()
async def test_flat_prefix_and_sv_interface_array_and_1bit_vector(
    dut: HierarchyObject,
) -> None:
    """Verify `from_prefix`, `from_interface_array`, and 1-bit vector tolerance."""
    tb, _, _ = await _init_dut(dut)

    # 1. Flat prefixed bus (`raw_in_*` -> `raw_out_*`, XOR 0xA5)
    raw_in = DummyStreamBus.from_prefix(dut, "raw_in")
    raw_out = DummyStreamBus.from_prefix(dut, "raw_out")

    # 2. SystemVerilog interface array (`lanes_if[0]` -> `lanes_if[1]`, XOR 0xFF)
    lanes = DummyStreamBus.from_interface_array(dut["lanes_if"])
    assert len(lanes) == 2

    # 3. 1-bit `logic [0:0]` vector tolerance + async Reset trigger on PackedObject!
    flag_bus = OneBitFlagBus.from_entity(dut)
    onebit_rst = Reset(flag_bus.flag, active_low=False, sync=False)
    waiter = cocotb.start_soon(onebit_rst.wait_active())
    await Timer(2, unit="ns")
    flag_bus.flag.value = 1
    await waiter
    await RisingEdge(tb.clk)
    assert int(flag_bus.echo.value) == 1

    # Sync Reset without a clock handle must raise ValueError rather than falling back!
    clkless_rst = Reset(tb.rst_n, active_low=True, sync=True)
    try:
        await clkless_rst.wait_active()
        raise AssertionError("Expected ValueError for clockless sync Reset")
    except ValueError:
        pass

    async with (
        DummyStreamDriver(raw_in, tb.clk, name="raw_drv") as raw_drv,
        DummyStreamMonitor(raw_out, tb.clk, name="raw_mon") as raw_mon,
        DummyStreamDriver(lanes[0], tb.clk, name="lane_drv") as lane_drv,
        DummyStreamMonitor(lanes[1], tb.clk, name="lane_mon") as lane_mon,
        Scoreboard(name="multi_bus_sb") as sb,
    ):
        raw_ch = sb.add_channel("raw", raw_mon)
        lane_ch = sb.add_channel("lane", lane_mon)

        raw_ch.expect(DummyStreamTx(data=0x33 ^ 0xA5, last=1))
        lane_ch.expect(DummyStreamTx(data=0x33 ^ 0xFF, last=0))

        await raw_drv.send(DummyStreamTx(data=0x33, last=1))
        await lane_drv.send(DummyStreamTx(data=0x33, last=0))
        await ClockCycles(tb.clk, 4)


@cocotb.test()
async def test_explicit_lifecycle_and_reset_flush_and_pause(
    dut: HierarchyObject,
) -> None:
    """Verify explicit lifecycle, ResetPolicy.FLUSH (default), and PAUSE."""
    tb, in_bus, out_bus = await _init_dut(dut)
    rst = Reset(tb.rst_n, active_low=True, sync=True)

    # Explicit lifecycle: not running until started; double-start raises.
    drv_flush = DummyStreamDriver(in_bus, tb.clk, reset=rst, name="flush_drv")
    assert not bool(drv_flush.running)
    drv_flush.start()
    assert bool(drv_flush.running)
    try:
        drv_flush.start()
        raise AssertionError("Expected ComponentLifecycleError on double start")
    except ComponentLifecycleError:
        pass

    # Stall output so driver blocks mid-transaction, then assert reset (FLUSH).
    assert out_bus.ready is not None
    out_bus.ready.value = 0

    # Fill the 1-deep pipe first so the second transaction stalls on in_ready == 0.
    await drv_flush.send(DummyStreamTx(data=0x11))
    h_stalled = drv_flush.enqueue(DummyStreamTx(data=0x22))
    h_queued = drv_flush.enqueue(DummyStreamTx(data=0x33))
    await ClockCycles(tb.clk, 2)
    assert not h_stalled.done

    # Assert synchronous active-low reset.
    tb.rst_n.value = 0
    await ClockCycles(tb.clk, 2)

    # Stalled and queued transactions must fail with ResetError and bus be idle.
    for h in (h_stalled, h_queued):
        try:
            await h.wait()
            raise AssertionError("Expected ResetError for flushed transaction")
        except ResetError:
            pass
    assert int(in_bus.valid.value) == 0

    # Release reset and verify post-reset recovery.
    out_bus.ready.value = 1
    tb.rst_n.value = 1
    await RisingEdge(tb.clk)

    async with DummyStreamMonitor(
        out_bus, tb.clk, reset=rst, name="post_rst_mon"
    ) as mon:
        await drv_flush.send(DummyStreamTx(data=0x44, last=1))
        got = await mon.recv()
        assert got == DummyStreamTx(data=0x44 ^ 0x5A, last=1)

    # Stopping a busy driver must immediately fail pending TxHandles with
    # ComponentLifecycleError instead of deadlocking!
    out_bus.ready.value = 0
    await drv_flush.send(DummyStreamTx(data=0x51))
    h_aborted_on_stop = drv_flush.enqueue(DummyStreamTx(data=0x52))
    await ClockCycles(tb.clk, 2)
    await drv_flush.stop()
    assert not bool(drv_flush.running)
    await RisingEdge(tb.clk)
    assert int(in_bus.valid.value) == 0
    try:
        await h_aborted_on_stop.wait()
        raise AssertionError("Expected ComponentLifecycleError when driver stops")
    except ComponentLifecycleError:
        pass
    out_bus.ready.value = 1
    tb.rst_n.value = 0
    await ClockCycles(tb.clk, 2)
    tb.rst_n.value = 1
    await RisingEdge(tb.clk)

    # Now test explicit ResetPolicy.PAUSE: interrupted transaction retries after reset!
    async with (
        DummyStreamDriver(
            in_bus,
            tb.clk,
            reset=rst,
            reset_policy=ResetPolicy.PAUSE,
            name="pause_drv",
        ) as drv_pause,
        DummyStreamMonitor(out_bus, tb.clk, reset=rst, name="pause_mon") as pause_mon,
    ):
        out_bus.ready.value = 0
        await drv_pause.send(DummyStreamTx(data=0x01))
        h_retry = drv_pause.enqueue(DummyStreamTx(data=0x77, last=1))
        await ClockCycles(tb.clk, 2)
        assert not h_retry.done

        # Pulse reset, then unstall.
        tb.rst_n.value = 0
        await ClockCycles(tb.clk, 2)
        assert int(in_bus.valid.value) == 0
        out_bus.ready.value = 1
        tb.rst_n.value = 1

        # Transaction 0x77 should complete cleanly after reset deasserts!
        await h_retry.wait()
        got_retry = await pause_mon.recv()
        assert got_retry == DummyStreamTx(data=0x77 ^ 0x5A, last=1)


@cocotb.test()
async def test_cocotbext_adapter_and_legacy_scoreboard_interop(
    dut: HierarchyObject,
) -> None:
    """Verify `AsyncSourceAdapter`, bounded Monitor ring buffer, and REAL
    `cocotb_bus.scoreboard.Scoreboard`.
    """
    tb, in_bus, out_bus = await _init_dut(dut)

    # Simulate a cocotbext-style sink exposing `async def recv()`.
    class FakeCocotbextSink:
        def __init__(self, mon: DummyStreamMonitor) -> None:
            self._mon = mon

        async def recv(self) -> DummyStreamTx:
            return await self._mon.recv()

    async with (
        DummyStreamDriver(in_bus, tb.clk, name="interop_drv") as drv,
        # Bounded ring buffer (max_queue_size=2) with 4 items verifies zero QueueFull
        # crash when callbacks/Scoreboards are active!
        DummyStreamMonitor(
            out_bus, tb.clk, name="bounded_mon", max_queue_size=2
        ) as bounded_mon,
        DummyStreamMonitor(out_bus, tb.clk, name="interop_mon") as mon,
    ):
        sink = FakeCocotbextSink(mon)
        adapter = AsyncSourceAdapter.from_sink(sink, name="ext_sink")

        # Real `cocotb_bus.scoreboard.Scoreboard` from `cocotb-bus` package!
        expected_txs = [
            DummyStreamTx(data=0x0F ^ 0x5A),
            DummyStreamTx(data=0xF0 ^ 0x5A),
            DummyStreamTx(data=0x3C ^ 0x5A),
            DummyStreamTx(data=0xC3 ^ 0x5A),
        ]
        legacy_expected = list(expected_txs)
        legacy_sb = LegacyCocotbScoreboard(dut, fail_immediately=True)
        legacy_sb.add_interface(as_legacy_monitor(bounded_mon), legacy_expected)

        async with adapter, Scoreboard(name="adapter_sb") as sb:
            ch = sb.add_channel("ext", adapter)
            ch.expect_many(expected_txs)

            for raw in (0x0F, 0xF0, 0x3C, 0xC3):
                await drv.send(DummyStreamTx(data=raw))
            await ClockCycles(tb.clk, 4)

        assert legacy_expected == []
        assert legacy_sb.errors == 0
        assert bounded_mon.count == 4
        assert bounded_mon.pending == 2
        assert bounded_mon.dropped_count == 2

    # Evaluate `legacy_sb.result` at test end (calls `cocotb.pass_test()` on 2.x).
    _ = legacy_sb.result
