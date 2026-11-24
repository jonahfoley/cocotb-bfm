"""Example: Resolvers, ``Component``, ``Role``, ``QueueAdapter``, and matchers.

Demonstrates the remaining user-facing ``cocotb-bfm`` classes in focused tests:

1. ``RegexResolver`` (``from_regex``), ``MappingResolver`` (``from_mapping``),
   ``CompositeResolver``, ``from_interface_array``, and ``from_interface_map``.
2. Custom ``Component`` subclass (backpressure generator) and ``Role.TARGET``
   direction-safe driving.
3. ``QueueAdapter``, ``WindowMatcher``, and ``UnorderedMatcher``.
"""

from __future__ import annotations

from collections.abc import Sequence

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.queue import Queue
from cocotb.triggers import ClockCycles, RisingEdge

from cocotb_bfm import (
    Bus,
    BusDirectionError,
    Component,
    CompositeResolver,
    MappingResolver,
    QueueAdapter,
    RegexResolver,
    Role,
    Scoreboard,
    UnorderedMatcher,
    WindowMatcher,
)
from examples.hello_valid_ready.test_hello_valid_ready import (
    ByteStreamBus,
    ByteStreamDriver,
    ByteStreamMonitor,
    ByteTx,
)


class TopBus(Bus):
    clk: LogicObject
    rst_n: LogicObject


class ReadyBackpressureGenerator(Component):
    """Standalone :class:`Component` toggling ``bus.ready`` on a repeating pattern."""

    def __init__(
        self,
        bus: ByteStreamBus,
        clock: LogicObject,
        pattern: Sequence[int] = (1, 0, 1, 1, 0),
        *,
        name: str = "ready_bp",
    ) -> None:
        super().__init__(name=name)
        self.bus = bus
        self.clock = clock
        self.pattern = tuple(pattern)

    async def _run(self) -> None:
        idx = 0
        while True:
            # Role.TARGET allows driving TARGET signals (`ready`) while blocking
            # accidental writes to INITIATOR signals (`valid` / `data`).
            self.bus.drive(
                {"ready": self.pattern[idx % len(self.pattern)]},
                role=Role.TARGET,
            )
            idx += 1
            await RisingEdge(self.clock)

    async def _on_stop(self) -> None:
        self.bus.drive_idle(role=Role.TARGET)


async def _init_dut(dut: HierarchyObject) -> TopBus:
    top = TopBus.from_entity(dut)
    rx_bus = ByteStreamBus.from_regex(dut, r"rx0_(?P<signal>\w+)_[io]")
    tx_bus = ByteStreamBus.from_regex(dut, r"tx0_(?P<signal>\w+)_[io]")
    cha_bus = ByteStreamBus.from_mapping(
        dut, {"valid": "chA_vld_sig", "ready": "chA_rdy_sig", "data": "chA_dat_sig"}
    )
    chb_bus = ByteStreamBus.from_mapping(
        dut, {"valid": "chB_vld_sig", "ready": "chB_rdy_sig", "data": "chB_dat_sig"}
    )
    lanes = ByteStreamBus.from_interface_array(dut["lanes"])

    Clock(top.clk, 10, unit="ns").start()
    top.rst_n.value = 0
    for bus in (rx_bus, cha_bus, lanes[0]):
        bus.drive_idle(role=Role.INITIATOR)
    for bus in (tx_bus, chb_bus, lanes[1]):
        bus.drive_idle(role=Role.TARGET)

    await ClockCycles(top.clk, 3)
    top.rst_n.value = 1
    await RisingEdge(top.clk)
    return top


@cocotb.test()
async def test_resolvers_and_interface_array(dut: HierarchyObject) -> None:
    """Bind ports via ``from_regex``, ``from_mapping``, ``CompositeResolver``, and
    ``from_interface_array`` / ``from_interface_map``.
    """
    top = await _init_dut(dut)

    # 1. RegexResolver via `Bus.from_regex()` (`rx0_<sig>_[io]` / `tx0_<sig>_[io]`)
    rx_bus = ByteStreamBus.from_regex(dut, r"rx0_(?P<signal>\w+)_[io]")
    tx_bus = ByteStreamBus.from_resolver(
        dut,
        CompositeResolver([RegexResolver(r"tx0_(?P<signal>\w+)_[io]")]),
    )

    # 2. MappingResolver via `Bus.from_mapping()` for irregular legacy port names
    cha_bus = ByteStreamBus.from_mapping(
        dut,
        {"valid": "chA_vld_sig", "ready": "chA_rdy_sig", "data": "chA_dat_sig"},
    )
    chb_bus = ByteStreamBus.from_resolver(
        dut,
        MappingResolver(
            {"valid": "chB_vld_sig", "ready": "chB_rdy_sig", "data": "chB_dat_sig"}
        ),
    )

    # 3. SV interface array via `from_interface_array()` and `from_interface_map()`
    lanes_list = ByteStreamBus.from_interface_array(dut["lanes"])
    lanes_map = ByteStreamBus.from_interface_map(dut["lanes"])
    assert len(lanes_list) == 2
    assert set(lanes_map.keys()) == {0, 1}

    async with (
        ByteStreamDriver(rx_bus, top.clk, name="rx_drv") as rx_drv,
        ByteStreamMonitor(tx_bus, top.clk, name="tx_mon") as tx_mon,
        ByteStreamDriver(cha_bus, top.clk, name="cha_drv") as cha_drv,
        ByteStreamMonitor(chb_bus, top.clk, name="chb_mon") as chb_mon,
        ByteStreamDriver(lanes_map[0], top.clk, name="lane0_drv") as lane0_drv,
        ByteStreamMonitor(lanes_map[1], top.clk, name="lane1_mon") as lane1_mon,
        Scoreboard(name="resolvers_sb") as sb,
    ):
        tx_ch = sb.add_channel("regex_tx", tx_mon)
        chb_ch = sb.add_channel("mapped_chb", chb_mon)
        lane_ch = sb.add_channel("array_lane1", lane1_mon)

        tx_ch.expect(ByteTx(0x42))
        chb_ch.expect(ByteTx(0xAA ^ 0xFF))
        lane_ch.expect(ByteTx(0x10 + 1))

        await rx_drv.send(ByteTx(0x42))
        await cha_drv.send(ByteTx(0xAA))
        await lane0_drv.send(ByteTx(0x10))
        await ClockCycles(top.clk, 3)


@cocotb.test()
async def test_component_backpressure_and_target_role(dut: HierarchyObject) -> None:
    """Use a custom ``Component`` for backpressure and enforce ``Role.TARGET``."""
    top = await _init_dut(dut)
    rx_bus = ByteStreamBus.from_regex(dut, r"rx0_(?P<signal>\w+)_[io]")
    tx_bus = ByteStreamBus.from_regex(dut, r"tx0_(?P<signal>\w+)_[io]")

    # Direction safety: a TARGET role cannot drive INITIATOR signal `valid`.
    try:
        tx_bus.drive({"valid": 1}, role=Role.TARGET)
        raise AssertionError("Expected BusDirectionError for TARGET driving valid")
    except BusDirectionError:
        pass

    async with (
        ReadyBackpressureGenerator(tx_bus, top.clk, pattern=(0, 1, 0, 1)) as _bp,
        ByteStreamDriver(rx_bus, top.clk, name="rx_drv") as drv,
        ByteStreamMonitor(tx_bus, top.clk, name="tx_mon") as mon,
        Scoreboard(name="bp_sb") as sb,
    ):
        ch = sb.add_channel("tx", mon)
        txs = [ByteTx(0x11), ByteTx(0x22), ByteTx(0x33), ByteTx(0x44)]
        ch.expect_many(txs)

        await drv.send_many(txs)
        await ClockCycles(top.clk, 6)
        assert ch.matched_count == len(txs)


@cocotb.test()
async def test_window_unordered_matchers_and_queue_adapter(
    dut: HierarchyObject,
) -> None:
    """Feed a ``cocotb.queue.Queue`` via ``QueueAdapter`` into ``WindowMatcher`` and
    ``UnorderedMatcher`` scoreboard channels.
    """
    top = await _init_dut(dut)
    win_q: Queue[int] = Queue()
    bag_q: Queue[str] = Queue()

    async with (
        QueueAdapter(win_q, name="win_src") as win_src,
        QueueAdapter(bag_q, name="bag_src") as bag_src,
        Scoreboard(name="matcher_sb") as sb,
    ):
        # WindowMatcher(depth=2): items may arrive up to 2 positions ahead of FIFO head.
        win_ch = sb.add_channel("window", win_src, matcher=WindowMatcher(depth=2))
        win_ch.expect_many([10, 20, 30, 40])

        # UnorderedMatcher: items may arrive in any order at all.
        bag_ch = sb.add_channel("unordered", bag_src, matcher=UnorderedMatcher())
        bag_ch.expect_many(["alpha", "beta", "gamma"])

        for val in (30, 10, 40, 20):
            win_q.put_nowait(val)
        for tag in ("gamma", "alpha", "beta"):
            bag_q.put_nowait(tag)

        await ClockCycles(top.clk, 2)
        assert win_ch.matched_count == 4
        assert bag_ch.matched_count == 3
