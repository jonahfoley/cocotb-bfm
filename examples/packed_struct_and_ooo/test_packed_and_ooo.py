"""Example: ``PackedStruct`` bitfields + out-of-order ``KeyedMatcher`` Scoreboard."""

from __future__ import annotations

from enum import IntEnum

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge

from cocotb_bfm import (
    Bus,
    Direction,
    Driver,
    KeyedMatcher,
    Monitor,
    PackedStruct,
    Scoreboard,
    VectorObject,
    bitfield,
    signal,
)


# 1. Mirror the SystemVerilog `typedef struct packed` in Python (MSB -> LSB).
class OpCode(IntEnum):
    ADD1 = 0
    INV = 1


class Packet(PackedStruct):
    """Matches ``typedef struct packed { tid[3:0]; op[1:0]; payload[7:0]; } pkt_t``."""

    tid: int = bitfield(4)
    op: OpCode = bitfield(2)
    payload: int = bitfield(8)


# 2. Declare typed Buses (no backpressure on this simple responder).
class TopBus(Bus):
    clk: LogicObject
    rst_n: LogicObject


class PktBus(Bus):
    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    pkt: VectorObject = signal(width=Packet.total_width(), idle_value=0)


# 3. Minimal Driver and Monitor using `PackedStruct` read/write helpers.
class PktDriver(Driver[PktBus, Packet]):
    async def _drive_transaction(self, tx: Packet) -> None:
        self.bus.valid.value = 1
        tx.write_handle(self.bus.pkt)
        await RisingEdge(self.clock)
        self.bus.valid.value = 0


class PktMonitor(Monitor[PktBus, Packet]):
    async def _monitor_loop(self) -> None:
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.valid.value) == 1:
                self._emit(Packet.from_handle(self.bus.pkt))


def _ref_model(req: Packet) -> Packet:
    match req.op:
        case OpCode.ADD1:
            out_payload = (req.payload + 1) & 0xFF
        case OpCode.INV:
            out_payload = (~req.payload) & 0xFF
    return Packet(tid=req.tid, op=req.op, payload=out_payload)


@cocotb.test()
async def test_packed_struct_and_ooo_scoreboard(dut: HierarchyObject) -> None:
    """Send even-tid (slow) then odd-tid (fast) packets and verify OoO responses."""
    top = TopBus.from_entity(dut)
    req_bus = PktBus.from_prefix(dut, "req")
    resp_bus = PktBus.from_prefix(dut, "resp")

    Clock(top.clk, 10, unit="ns").start()
    top.rst_n.value = 0
    req_bus.drive_idle()
    await ClockCycles(top.clk, 3)
    top.rst_n.value = 1
    await RisingEdge(top.clk)

    async with (
        PktDriver(req_bus, top.clk, name="req_drv") as drv,
        PktMonitor(req_bus, top.clk, name="req_mon") as req_mon,
        PktMonitor(resp_bus, top.clk, name="resp_mon") as resp_mon,
        Scoreboard(name="ooo_sb") as sb,
    ):
        # KeyedMatcher allows different `tid`s to complete out of order while still
        # enforcing strict FIFO order within each individual `tid`.
        resp_ch = sb.add_channel(
            "resp", resp_mon, matcher=KeyedMatcher(key=lambda p: p.tid)
        )
        sb.connect_model(req_mon, _ref_model, resp_ch)

        observed_tids: list[int] = []
        resp_mon.add_callback(lambda pkt: observed_tids.append(pkt.tid))

        # Send even-tid (3-cycle slow lane) followed immediately by odd-tid (1-cycle
        # fast lane) so the fast response overtakes the slow response!
        await drv.send(Packet(tid=2, op=OpCode.ADD1, payload=0x10))
        await drv.send(Packet(tid=3, op=OpCode.INV, payload=0x0F))
        await ClockCycles(top.clk, 2)

        await drv.send(Packet(tid=2, op=OpCode.INV, payload=0xAA))
        await drv.send(Packet(tid=5, op=OpCode.ADD1, payload=0xFE))
        await ClockCycles(top.clk, 4)

        assert observed_tids == [3, 2, 5, 2]
        assert resp_ch.matched_count == 4
