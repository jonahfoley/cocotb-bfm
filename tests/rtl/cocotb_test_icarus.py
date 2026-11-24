"""Icarus Verilog integration tests (flat ports, 4-state X/Z, and real cocotb-bus)."""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from cocotb.types import Logic, LogicArray

from cocotb_bfm import (
    Bus,
    PackedStruct,
    Reset,
    ResetError,
    ResetPolicy,
    Scoreboard,
    VectorObject,
    bitfield,
    format_diff,
)
from tests.rtl.stream_bfm import (
    DummyStreamBus,
    DummyStreamDriver,
    DummyStreamMonitor,
    DummyStreamTx,
    OneBitFlagBus,
)


class IcarusTopBus(Bus):
    clk: LogicObject
    rst_n: LogicObject
    xz_rst_n: LogicObject
    xz_vec: VectorObject


class BytePacket(PackedStruct):
    hi: int = bitfield(4)
    lo: int = bitfield(4)


async def _init_dut(
    dut: HierarchyObject,
) -> tuple[IcarusTopBus, DummyStreamBus, DummyStreamBus]:
    top = IcarusTopBus.from_entity(dut)
    in_bus = DummyStreamBus.from_prefix(dut, "in")
    out_bus = DummyStreamBus.from_prefix(dut, "out")
    flag_bus = OneBitFlagBus.from_entity(dut)

    Clock(top.clk, 10, unit="ns").start()
    top.rst_n.value = 0
    in_bus.drive_idle()
    assert out_bus.ready is not None
    out_bus.ready.value = 1
    flag_bus.flag.value = 0
    await ClockCycles(top.clk, 3)
    top.rst_n.value = 1
    await RisingEdge(top.clk)
    return top, in_bus, out_bus


@cocotb.test()
async def test_icarus_stream_driver_monitor_scoreboard(dut: HierarchyObject) -> None:
    """Verify Driver, Monitor, Scoreboard, and connect_model on Icarus Verilog."""
    top, in_bus, out_bus = await _init_dut(dut)
    rst = Reset(top.rst_n, active_low=True, sync=True)

    async with (
        DummyStreamDriver(in_bus, top.clk, reset=rst, name="ic_drv") as drv,
        DummyStreamMonitor(in_bus, top.clk, reset=rst, name="ic_in_mon") as in_mon,
        DummyStreamMonitor(out_bus, top.clk, reset=rst, name="ic_out_mon") as out_mon,
        Scoreboard(name="ic_sb") as sb,
    ):
        out_ch = sb.add_channel("out", out_mon)
        sb.connect_model(
            in_mon,
            lambda tx: DummyStreamTx(data=tx.data ^ 0x5A),
            out_ch,
        )

        txs = [DummyStreamTx(data=b) for b in (0x00, 0x12, 0xAB, 0xFF)]
        await drv.send_many(txs)
        await ClockCycles(top.clk, 4)
        assert out_ch.matched_count == len(txs)


@cocotb.test()
async def test_icarus_4state_xz_reset_and_packed_struct(dut: HierarchyObject) -> None:
    """Verify real 4-state X/Z handling on Reset, PackedStruct, and format_diff."""
    top, in_bus, out_bus = await _init_dut(dut)

    # 1. Drive literal 'X' onto xz_rst_n and verify treat_x_as_active flag.
    top.xz_rst_n.value = Logic("X")
    await Timer(1, unit="ns")
    rst_ignore_x = Reset(top.xz_rst_n, active_low=True, treat_x_as_active=False)
    rst_active_x = Reset(top.xz_rst_n, active_low=True, treat_x_as_active=True)
    assert rst_ignore_x.is_active() is False
    assert rst_active_x.is_active() is True

    # 2. Unpacking a real 4-state X/Z vector raises a clear ValueError naming handle.
    try:
        BytePacket.from_handle(top.xz_vec)
        raise AssertionError("Expected ValueError when unpacking 4-state X/Z vector")
    except ValueError as exc:
        msg = str(exc)
        assert "BytePacket.unpack" in msg
        assert "xz_vec" in msg

    # 3. format_diff renders real 4-state LogicArray cleanly.
    diff = format_diff(LogicArray("10100010"), top.xz_vec.value)
    assert "unresolvable" in diff

    # 4. 1-bit [0:0] vector tolerance + async Reset edge trigger on Icarus.
    flag_bus = OneBitFlagBus.from_entity(dut)
    onebit_rst = Reset(flag_bus.flag, active_low=False, sync=False)
    waiter = cocotb.start_soon(onebit_rst.wait_active())
    await Timer(2, unit="ns")
    flag_bus.flag.value = 1
    await waiter
    await RisingEdge(top.clk)
    await Timer(1, unit="ns")
    assert int(flag_bus.echo.value) == 1

    # 5. ResetPolicy.FLUSH cancels stalled transfer on Icarus cleanly.
    rst = Reset(top.rst_n, active_low=True, sync=True)
    assert out_bus.ready is not None
    async with DummyStreamDriver(
        in_bus, top.clk, reset=rst, reset_policy=ResetPolicy.FLUSH, name="flush_drv"
    ) as drv:
        out_bus.ready.value = 0
        await drv.send(DummyStreamTx(data=0x11))
        stalled = drv.enqueue(DummyStreamTx(data=0x22))
        await ClockCycles(top.clk, 2)
        top.rst_n.value = 0
        await ClockCycles(top.clk, 2)
        try:
            await stalled.wait()
            raise AssertionError("Expected ResetError on flushed transfer")
        except ResetError:
            pass
