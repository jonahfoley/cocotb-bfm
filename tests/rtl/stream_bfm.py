"""Minimal dummy Valid/Ready stream protocol strictly for RTL integration testing."""

from __future__ import annotations

from dataclasses import dataclass

from cocotb.handle import LogicArrayObject, LogicObject
from cocotb.triggers import RisingEdge

from cocotb_bfm import Bus, Direction, Driver, Monitor, signal


class DummyStreamBus(Bus):
    """Minimal valid/ready/data/last bus used only in integration tests."""

    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    data: LogicArrayObject = signal(direction=Direction.INITIATOR, idle_value=0)
    ready: LogicObject | None = signal(
        default=None, direction=Direction.TARGET, idle_value=1
    )
    last: LogicObject | None = signal(
        default=None, direction=Direction.INITIATOR, idle_value=0
    )


class OneBitFlagBus(Bus):
    """Test bus exercising 1-bit vector (`logic [0:0]`) tolerance on ``LogicObject``."""

    flag: LogicObject = signal(hdl_name="onebit_flag")
    echo: LogicObject = signal(hdl_name="onebit_echo")


@dataclass(frozen=True)
class DummyStreamTx:
    data: int
    last: int = 0


class DummyStreamDriver(Driver[DummyStreamBus, DummyStreamTx]):
    """Initiator driver for :class:`DummyStreamBus`."""

    async def _drive_transaction(self, tx: DummyStreamTx) -> None:
        self.bus.valid.value = 1
        self.bus.data.value = tx.data
        if self.bus.last is not None:
            self.bus.last.value = tx.last

        while True:
            await RisingEdge(self.clock)
            if self.bus.ready is None or int(self.bus.ready.value) == 1:
                break

        self.bus.valid.value = 0
        if self.bus.last is not None:
            self.bus.last.value = 0


class DummyStreamMonitor(Monitor[DummyStreamBus, DummyStreamTx]):
    """Passive monitor sampling completed valid/ready handshakes."""

    async def _monitor_loop(self) -> None:
        while True:
            await RisingEdge(self.clock)
            valid = int(self.bus.valid.value) == 1
            ready = self.bus.ready is None or int(self.bus.ready.value) == 1
            if valid and ready:
                last_val = int(self.bus.last.value) if self.bus.last is not None else 0
                self._emit(
                    DummyStreamTx(
                        data=self.bus.data.value.to_unsigned(),
                        last=last_val,
                    )
                )
