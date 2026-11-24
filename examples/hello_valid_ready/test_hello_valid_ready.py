"""Example 01: Minimal valid/ready Bus, Driver, Monitor, and Scoreboard."""

from __future__ import annotations

from dataclasses import dataclass

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicArrayObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge

from cocotb_bfm import Bus, Direction, Driver, Monitor, Scoreboard, signal


# 1. Declare a typed Bus matching the DUT's valid/ready/data ports.
class TopBus(Bus):
    """Top-level clock and reset handles."""

    clk: LogicObject
    rst_n: LogicObject


class ByteStreamBus(Bus):
    """Simple 8-bit valid/ready stream interface."""

    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    data: LogicArrayObject = signal(
        width=8, direction=Direction.INITIATOR, idle_value=0
    )
    ready: LogicObject = signal(direction=Direction.TARGET, idle_value=1)


# 2. Define an immutable transaction object.
@dataclass(frozen=True)
class ByteTx:
    data: int


# 3. Implement a Driver (drives transactions onto the bus).
class ByteStreamDriver(Driver[ByteStreamBus, ByteTx]):
    async def _drive_transaction(self, tx: ByteTx) -> None:
        self.bus.valid.value = 1
        self.bus.data.value = tx.data
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.ready.value) == 1:
                break
        self.bus.valid.value = 0


# 4. Implement a Monitor (passively observes completed handshakes).
class ByteStreamMonitor(Monitor[ByteStreamBus, ByteTx]):
    async def _monitor_loop(self) -> None:
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.valid.value) == 1 and int(self.bus.ready.value) == 1:
                self._emit(ByteTx(data=self.bus.data.value.to_unsigned()))


@cocotb.test()
async def test_hello_valid_ready(dut: HierarchyObject) -> None:
    """Drive 5 bytes into `adder_dut` and verify every output equals `in + 1`."""
    # Bind typed buses to flat prefixed ports (`in_*` and `out_*`).
    top = TopBus.from_entity(dut)
    in_bus = ByteStreamBus.from_prefix(dut, "in")
    out_bus = ByteStreamBus.from_prefix(dut, "out")

    # Start clock and hold reset for 3 cycles.
    Clock(top.clk, 10, unit="ns").start()
    top.rst_n.value = 0
    in_bus.drive_idle()
    out_bus.ready.value = 1
    await ClockCycles(top.clk, 3)
    top.rst_n.value = 1
    await RisingEdge(top.clk)

    # Start Driver, Monitor, and Scoreboard cleanly with `async with`.
    async with (
        ByteStreamDriver(in_bus, top.clk, name="in_drv") as drv,
        ByteStreamMonitor(out_bus, top.clk, name="out_mon") as mon,
        Scoreboard(name="adder_sb") as sb,
    ):
        ch = sb.add_channel("out", mon)

        inputs = [0x00, 0x10, 0x41, 0x7F, 0xFF]
        ch.expect_many([ByteTx(data=(x + 1) & 0xFF) for x in inputs])

        await drv.send_many([ByteTx(data=x) for x in inputs])
        await ClockCycles(top.clk, 3)

        assert ch.matched_count == len(inputs)
