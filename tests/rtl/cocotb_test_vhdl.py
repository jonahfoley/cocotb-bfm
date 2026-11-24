"""VHDL integration tests on GHDL exercising LogicArrayObject & non-zero ranges."""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicArrayObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge, Timer

from cocotb_bfm import (
    Bus,
    PackedStruct,
    Reset,
    ResetError,
    ResetPolicy,
    Role,
    Scoreboard,
    bitfield,
)
from tests.rtl.stream_bfm import (
    DummyStreamBus,
    DummyStreamDriver,
    DummyStreamMonitor,
    DummyStreamTx,
    OneBitFlagBus,
)


class VhdlTopBus(Bus):
    clk: LogicObject
    rst_n: LogicObject
    pkt_in: LogicArrayObject
    pkt_out: LogicArrayObject


class NibblePacket(PackedStruct):
    hi: int = bitfield(4)
    lo: int = bitfield(4)


async def _init_dut(
    dut: HierarchyObject,
) -> tuple[VhdlTopBus, DummyStreamBus, DummyStreamBus]:
    top = VhdlTopBus.from_entity(dut)
    in_bus = DummyStreamBus.from_prefix(dut, "in")
    out_bus = DummyStreamBus.from_prefix(dut, "out")
    flag_bus = OneBitFlagBus.from_entity(dut)

    Clock(top.clk, 10, unit="ns").start()
    top.rst_n.value = 0
    in_bus.drive_idle(role=Role.INITIATOR)
    out_bus.drive_idle(role=Role.TARGET)
    flag_bus.flag.value = 0
    top.pkt_in.value = 0
    await ClockCycles(top.clk, 3)
    top.rst_n.value = 1
    await RisingEdge(top.clk)
    return top, in_bus, out_bus


@cocotb.test()
async def test_vhdl_stream_driver_monitor_and_packed_struct(
    dut: HierarchyObject,
) -> None:
    """Verify Bus, Driver, Monitor, Scoreboard, 1-bit vector, and PackedStruct on
    real VHDL ``LogicArrayObject`` handles (including ascending ``1 to 8`` range).
    """
    top, in_bus, out_bus = await _init_dut(dut)
    rst = Reset(top.rst_n, active_low=True, sync=True)

    # 1. Verify PackedStruct round-trip on VHDL `std_logic_vector(1 to 8)`!
    sent_pkt = NibblePacket(hi=0xA, lo=0x5)
    sent_pkt.write_handle(top.pkt_in)
    await ClockCycles(top.clk, 2)
    got_pkt = NibblePacket.from_handle(top.pkt_out)
    assert got_pkt == sent_pkt

    # 2. Verify 1-bit `std_logic_vector(0 downto 0)` tolerance & async Reset trigger.
    flag_bus = OneBitFlagBus.from_entity(dut)
    onebit_rst = Reset(flag_bus.flag, active_low=False, sync=False)
    waiter = cocotb.start_soon(onebit_rst.wait_active())
    await Timer(2, unit="ns")
    flag_bus.flag.value = 1
    await waiter
    await ClockCycles(top.clk, 2)
    assert int(flag_bus.echo.value) == 1

    # 3. End-to-end Driver -> Monitor -> Scoreboard with connect_model.
    async with (
        DummyStreamDriver(in_bus, top.clk, reset=rst, name="vhdl_drv") as drv,
        DummyStreamMonitor(in_bus, top.clk, reset=rst, name="vhdl_in_mon") as in_mon,
        DummyStreamMonitor(out_bus, top.clk, reset=rst, name="vhdl_out_mon") as out_mon,
        Scoreboard(name="vhdl_sb") as sb,
    ):
        out_ch = sb.add_channel("out", out_mon)
        sb.connect_model(in_mon, lambda tx: DummyStreamTx(data=tx.data ^ 0x5A), out_ch)

        txs = [DummyStreamTx(data=b) for b in (0x00, 0x3C, 0xC3, 0xFF)]
        await drv.send_many(txs)
        await ClockCycles(top.clk, 4)
        assert out_ch.matched_count == len(txs)


@cocotb.test()
async def test_vhdl_reset_flush(dut: HierarchyObject) -> None:
    """Verify ResetPolicy.FLUSH cancels stalled transfers cleanly on VHDL."""
    top, in_bus, out_bus = await _init_dut(dut)
    rst = Reset(top.rst_n, active_low=True, sync=True)
    assert in_bus.ready is not None
    assert out_bus.ready is not None

    async with DummyStreamDriver(
        in_bus, top.clk, reset=rst, reset_policy=ResetPolicy.FLUSH, name="vhdl_flush"
    ) as drv:
        out_bus.ready.value = 0
        await drv.send(DummyStreamTx(data=0x11))
        stalled = drv.enqueue(DummyStreamTx(data=0x22))
        await ClockCycles(top.clk, 2)
        assert not stalled.done, (
            f"Expected transfer to stall: in_ready={in_bus.ready.value!r}, "
            f"out_valid={out_bus.valid.value!r}, out_ready={out_bus.ready.value!r}"
        )
        top.rst_n.value = 0
        await ClockCycles(top.clk, 2)
        try:
            await stalled.wait()
            raise AssertionError("Expected ResetError on flushed VHDL transfer")
        except ResetError:
            pass
