"""Static type-checking verification suite for ``cocotb_bfm``.

Checked directly by ``mypy --strict`` to guarantee PEP 681 ``Bus`` definitions,
nested sub-buses, factory constructors, ``Driver``, ``Monitor``, and ``Scoreboard``
preserve exact static types with zero plugins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, assert_type

from cocotb.handle import (
    HierarchyArrayObject,
    HierarchyObject,
    LogicArrayObject,
    LogicObject,
)

from cocotb_bfm import (
    Bus,
    Channel,
    Direction,
    Driver,
    KeyedMatcher,
    Monitor,
    PackedStruct,
    RequestDriver,
    Reset,
    Scoreboard,
    VectorObject,
    bitfield,
    signal,
    sub_bus,
)


class MetaStruct(PackedStruct):
    tag: int = bitfield(4)
    priv: int = bitfield(2)


class ChannelBus(Bus):
    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    data: LogicArrayObject = signal(direction=Direction.INITIATOR)
    ready: LogicObject | None = signal(default=None, direction=Direction.TARGET)
    meta: VectorObject | None = signal(default=None, width=6)


class PlainBus(Bus):
    clk: LogicObject
    payload: LogicArrayObject
    tag: LogicArrayObject | None = None


class CompositeBus(Bus):
    tx: ChannelBus = sub_bus(hdl_name="tx")
    rx: ChannelBus | None = sub_bus(default=None, hdl_name="rx")


@dataclass(frozen=True)
class Packet:
    tid: int
    data: int


class PacketDriver(Driver[ChannelBus, Packet]):
    async def _drive_transaction(self, tx: Packet) -> None:
        assert_type(self.bus, ChannelBus)
        assert_type(self.bus.valid, LogicObject)
        assert_type(self.bus.data, LogicArrayObject)
        assert_type(self.bus.ready, LogicObject | None)
        assert_type(tx, Packet)
        self.bus.valid.value = 1
        self.bus.data.value = tx.data


class PacketMonitor(Monitor[ChannelBus, Packet]):
    async def _monitor_loop(self) -> None:
        assert_type(self.bus.valid, LogicObject)
        self._emit(Packet(tid=0, data=0))


def _check_bus_types(
    dut: HierarchyObject,
    if_array: HierarchyArrayObject[Any],
    valid: LogicObject,
    data: LogicArrayObject,
    ready: LogicObject,
) -> None:
    # Direct constructor is statically type-checked via PEP 681 dataclass_transform.
    b1 = ChannelBus(valid=valid, data=data, ready=ready)
    assert_type(b1, ChannelBus)
    assert_type(b1.valid, LogicObject)
    assert_type(b1.data, LogicArrayObject)
    assert_type(b1.ready, LogicObject | None)

    b2 = PlainBus(clk=valid, payload=data)
    assert_type(b2.clk, LogicObject)
    assert_type(b2.payload, LogicArrayObject)
    assert_type(b2.tag, LogicArrayObject | None)

    # Factory methods preserve Self.
    b_if = ChannelBus.from_interface(dut)
    assert_type(b_if, ChannelBus)
    assert_type(b_if.valid, LogicObject)

    b_pref = ChannelBus.from_prefix(dut, "axis")
    assert_type(b_pref, ChannelBus)

    b_arr = ChannelBus.from_interface_array(if_array)
    assert_type(b_arr, list[ChannelBus])

    # Nested sub-buses preserve exact attribute types.
    comp = CompositeBus.from_interface(dut)
    assert_type(comp.tx, ChannelBus)
    assert_type(comp.tx.valid, LogicObject)
    assert_type(comp.rx, ChannelBus | None)
    if comp.rx is not None:
        assert_type(comp.rx.data, LogicArrayObject)


class PacketRequestDriver(RequestDriver[ChannelBus, Packet, int]):
    async def _handle_request(self, req: Packet) -> int:
        assert_type(self.bus, ChannelBus)
        assert_type(req, Packet)
        return req.data


async def _check_component_and_scoreboard_types(
    bus: ChannelBus,
    clk: LogicObject,
    rst_n: LogicObject,
) -> None:
    rst = Reset(rst_n, active_low=True)
    drv = PacketDriver(bus, clk, reset=rst)
    mon = PacketMonitor(bus, clk, reset=rst)

    tx = await mon.recv()
    assert_type(tx, Packet)
    await drv.send(tx)

    req_drv = PacketRequestDriver(bus, clk, reset=rst)
    resp = await req_drv.request(tx)
    assert_type(resp, int)

    meta = MetaStruct(tag=3, priv=1)
    assert_type(meta.tag, int)
    assert_type(MetaStruct.unpack(0), MetaStruct)

    sb = Scoreboard(name="typed")
    ch = sb.add_channel("packets", mon, matcher=KeyedMatcher(key=lambda p: p.tid))
    assert_type(ch, Channel[Packet])
    ch.expect(Packet(tid=1, data=42))
