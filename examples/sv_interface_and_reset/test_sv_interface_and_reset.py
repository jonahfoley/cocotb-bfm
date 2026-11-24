"""Example: SystemVerilog ``interface`` binding, ``connect_model()``, and ``Reset``."""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge

from cocotb_bfm import Bus, Reset, ResetError, ResetPolicy, Scoreboard, sub_bus
from examples.hello_valid_ready.test_hello_valid_ready import (
    ByteStreamBus,
    ByteStreamDriver,
    ByteStreamMonitor,
    ByteTx,
)


# Reuse the exact same `ByteStreamBus` inside a hierarchical top-level Bus!
class SvIfTopBus(Bus):
    """Top-level DUT bus composing two nested SystemVerilog ``interface`` sub-buses."""

    clk: LogicObject
    rst_n: LogicObject
    in_if: ByteStreamBus = sub_bus()
    out_if: ByteStreamBus = sub_bus()


async def _init(dut: HierarchyObject) -> tuple[SvIfTopBus, Reset]:
    # One call binds `clk`, `rst_n`, `in_if.*`, and `out_if.*` with full typing.
    top = SvIfTopBus.from_interface(dut)
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
async def test_sv_interface_and_connect_model(dut: HierarchyObject) -> None:
    """Wire an ingress monitor through a Python reference model into a Scoreboard."""
    top, rst = await _init(dut)

    async with (
        ByteStreamDriver(top.in_if, top.clk, reset=rst, name="drv") as drv,
        ByteStreamMonitor(top.in_if, top.clk, reset=rst, name="in_mon") as in_mon,
        ByteStreamMonitor(top.out_if, top.clk, reset=rst, name="out_mon") as out_mon,
        Scoreboard(name="xor_sb") as sb,
    ):
        out_ch = sb.add_channel("out", out_mon)
        # Automatically predict every output from observed ingress transactions!
        sb.connect_model(in_mon, lambda tx: ByteTx(data=tx.data ^ 0x5A), out_ch)

        await drv.send_many([ByteTx(data=b) for b in (0x00, 0x12, 0xAB, 0xFF)])
        await ClockCycles(top.clk, 3)

        assert out_ch.matched_count == 4


@cocotb.test()
async def test_reset_flush_and_recovery(dut: HierarchyObject) -> None:
    """Show how `ResetPolicy.FLUSH` cancels a stalled transfer and recovers cleanly."""
    top, rst = await _init(dut)

    async with (
        ByteStreamDriver(
            top.in_if,
            top.clk,
            reset=rst,
            reset_policy=ResetPolicy.FLUSH,
            name="flush_drv",
        ) as drv,
        ByteStreamMonitor(top.out_if, top.clk, reset=rst, name="out_mon") as out_mon,
    ):
        # Backpressure the output so the second transfer stalls in flight.
        top.out_if.ready.value = 0
        await drv.send(ByteTx(data=0x11))
        stalled = drv.enqueue(ByteTx(data=0x22))
        await ClockCycles(top.clk, 2)
        assert not stalled.done

        # Assert reset: Driver cancels the stalled transfer and returns bus to idle.
        top.rst_n.value = 0
        await ClockCycles(top.clk, 2)
        try:
            await stalled.wait()
            raise AssertionError("Expected ResetError when reset flushed driver")
        except ResetError:
            pass
        assert int(top.in_if.valid.value) == 0

        # Release reset & backpressure, then send a fresh transaction cleanly.
        top.out_if.ready.value = 1
        top.rst_n.value = 1
        await RisingEdge(top.clk)

        await drv.send(ByteTx(data=0x33))
        got = await out_mon.recv()
        assert got == ByteTx(data=0x33 ^ 0x5A)
