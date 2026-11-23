"""First-class reset abstraction and driver reset policies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

from cocotb.handle import LogicArrayObject, LogicObject, PackedObject
from cocotb.triggers import Edge, FallingEdge, RisingEdge


class ResetPolicy(Enum):
    """Behavior of a :class:`Driver` when reset asserts during or between transfers."""

    FLUSH = "flush"
    """Cancel the in-flight transfer, drive idle, and discard queued transactions."""

    PAUSE = "pause"
    """Cancel the in-flight transfer, drive idle values, and retry it after reset
    deasserts.
    """


def _logic_is_high(value: Any, *, treat_x_as_active: bool, active_low: bool) -> bool:
    """Safely evaluate a cocotb 2.0 ``Logic`` / ``LogicArray`` / ``int`` reset value."""
    match value:
        case bool():
            raw = 1 if value else 0
        case int():
            raw = 1 if value != 0 else 0
        case _:
            if getattr(value, "is_resolvable", None) is False:
                return treat_x_as_active
            try:
                raw = 1 if int(value) != 0 else 0
            except (ValueError, TypeError):
                return treat_x_as_active
    return (raw == 0) if active_low else (raw == 1)


@dataclass(frozen=True, slots=True)
class Reset:
    """Declarative reset signal descriptor supporting active-high/low and sync/async
    resets.

    Args:
        signal: Reset signal handle (``LogicObject`` or 1-bit ``LogicArrayObject``).
        active_low: ``True`` for active-low resets such as ``rst_n`` / ``aresetn``.
        sync: ``True`` for synchronous reset (sampled on rising clock edges); ``False``
            for asynchronous reset (monitored directly via signal edges).
        clock: Optional default clock handle for synchronous resets. Components also
            supply their own clock when awaiting reset transitions.
        treat_x_as_active: Whether unresolved ``X``/``Z`` values count as active reset.
            Defaults to ``False``.
    """

    signal: LogicObject | LogicArrayObject | PackedObject[Any] | Any
    active_low: bool = False
    sync: bool = True
    clock: LogicObject | LogicArrayObject | PackedObject[Any] | Any | None = None
    treat_x_as_active: bool = False

    def is_active(self) -> bool:
        """Return ``True`` if reset is currently asserted."""
        return _logic_is_high(
            self.signal.value,
            treat_x_as_active=self.treat_x_as_active,
            active_low=self.active_low,
        )

    async def wait_active(self, clock: Any | None = None) -> None:
        """Block until reset is asserted."""
        if self.is_active():
            return
        clk = clock if clock is not None else self.clock
        if self.sync and clk is not None:
            clk_handle = cast(LogicObject, clk)
            while not self.is_active():
                await RisingEdge(clk_handle)
        else:
            sig_handle = cast(LogicObject, self.signal)
            edge_cls = FallingEdge if self.active_low else RisingEdge
            while not self.is_active():
                try:
                    await edge_cls(sig_handle)
                except Exception:
                    await Edge(sig_handle)

    async def wait_inactive(self, clock: Any | None = None) -> None:
        """Block until reset is deasserted."""
        if not self.is_active():
            return
        clk = clock if clock is not None else self.clock
        if self.sync and clk is not None:
            clk_handle = cast(LogicObject, clk)
            while self.is_active():
                await RisingEdge(clk_handle)
        else:
            sig_handle = cast(LogicObject, self.signal)
            edge_cls = RisingEdge if self.active_low else FallingEdge
            while self.is_active():
                try:
                    await edge_cls(sig_handle)
                except Exception:
                    await Edge(sig_handle)
