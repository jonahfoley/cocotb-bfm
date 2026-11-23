"""Generic ``Scoreboard`` with pluggable matchers, model wiring, and teardown checks."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Generic, Self, TypeVar, cast

from cocotb_bfm.components.monitor import MonitorProtocol
from cocotb_bfm.exceptions import ScoreboardError
from cocotb_bfm.scoreboard.diff import format_value
from cocotb_bfm.scoreboard.matchers import InOrderMatcher, Matcher

TxT = TypeVar("TxT")
InT = TypeVar("InT")
OutT = TypeVar("OutT")


def _try_sim_time_ns() -> float | None:
    """Return current sim time in nanoseconds, or ``None`` outside a simulator."""
    try:
        from cocotb.utils import get_sim_time

        return float(get_sim_time(unit="ns"))
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class MismatchRecord:
    """Structured record of a single scoreboard mismatch event."""

    channel: str
    tx_index: int
    sim_time_ns: float | None
    expected: Any
    actual: Any
    detail: str

    def format(self) -> str:
        time_str = f" @ {self.sim_time_ns:g} ns" if self.sim_time_ns is not None else ""
        return f"[{self.channel} #{self.tx_index}{time_str}] {self.detail}"


class Channel(Generic[TxT]):
    """Typed verification channel managed by a :class:`Scoreboard`."""

    def __init__(
        self,
        name: str,
        *,
        matcher: Matcher[TxT] | None = None,
        fail_immediately: bool = True,
        log: logging.Logger | None = None,
    ) -> None:
        self.name: str = name
        self.matcher: Matcher[TxT] = (
            matcher if matcher is not None else InOrderMatcher()
        )
        self.fail_immediately: bool = fail_immediately
        self.log: logging.Logger = (
            log
            if log is not None
            else logging.getLogger(f"cocotb_bfm.scoreboard.{name}")
        )
        self._received_count: int = 0
        self._matched_count: int = 0
        self._mismatches: list[MismatchRecord] = []

    @property
    def received_count(self) -> int:
        return self._received_count

    @property
    def matched_count(self) -> int:
        return self._matched_count

    @property
    def pending_count(self) -> int:
        return self.matcher.pending_count()

    @property
    def mismatches(self) -> tuple[MismatchRecord, ...]:
        return tuple(self._mismatches)

    def expect(self, transaction: TxT) -> None:
        """Add one expected transaction to this channel."""
        self.matcher.expect(transaction)

    def expect_many(self, transactions: Iterable[TxT]) -> None:
        """Add multiple expected transactions to this channel in order."""
        for tx in transactions:
            self.matcher.expect(tx)

    def check(self, actual: TxT) -> bool:
        """Compare one observed transaction against the channel's matcher.

        Returns ``True`` on match, ``False`` on mismatch (or raises
        :class:`ScoreboardError` when ``fail_immediately=True``).
        """
        self._received_count += 1
        result = self.matcher.match(actual)
        if result.matched:
            self._matched_count += 1
            return True

        record = MismatchRecord(
            channel=self.name,
            tx_index=self._received_count,
            sim_time_ns=_try_sim_time_ns(),
            expected=result.expected,
            actual=result.actual,
            detail=result.detail,
        )
        self._mismatches.append(record)
        self.log.error("%s", record.format())
        if self.fail_immediately:
            raise ScoreboardError(record.format())
        return False

    def clear(self) -> None:
        """Reset pending expectations and recorded mismatches."""
        self.matcher.clear()
        self._mismatches.clear()
        self._received_count = 0
        self._matched_count = 0


class Scoreboard:
    """Multi-channel typed scoreboard with pluggable matchers and automatic teardown
    checks.

    Use as an async context manager to guarantee undrained expected transactions and
    deferred mismatches are always checked when a test finishes::

        async with Scoreboard(name="dut_sb") as sb:
            egress = sb.add_channel("egress", egress_monitor)
            sb.connect_model(ingress_monitor, ref_model, egress)
            ...
    """

    def __init__(
        self,
        *,
        name: str = "Scoreboard",
        fail_immediately: bool = True,
        allow_pending_on_exit: bool = False,
    ) -> None:
        self.name: str = name
        self.fail_immediately: bool = fail_immediately
        self.allow_pending_on_exit: bool = allow_pending_on_exit
        self.log: logging.Logger = logging.getLogger(f"cocotb_bfm.{name}")
        self._channels: dict[str, Channel[Any]] = {}

    @property
    def channels(self) -> dict[str, Channel[Any]]:
        """Return a shallow copy of registered channels keyed by name."""
        return dict(self._channels)

    def add_channel(
        self,
        name: str,
        monitor: MonitorProtocol[TxT] | None = None,
        *,
        matcher: Matcher[TxT] | None = None,
        fail_immediately: bool | None = None,
    ) -> Channel[TxT]:
        """Register a new typed verification channel and optionally attach a monitor."""
        if name in self._channels:
            raise ValueError(
                f"Scoreboard '{self.name}' already has a channel named {name!r}"
            )
        effective_fail = (
            self.fail_immediately if fail_immediately is None else fail_immediately
        )
        channel: Channel[TxT] = Channel(
            name=name,
            matcher=matcher,
            fail_immediately=effective_fail,
            log=self.log.getChild(name),
        )
        self._channels[name] = channel
        if monitor is not None:
            monitor.add_callback(channel.check)
        return channel

    def get_channel(self, name: str) -> Channel[Any]:
        """Retrieve a registered channel by name."""
        return self._channels[name]

    def connect_model(
        self,
        source_monitor: MonitorProtocol[InT],
        model: Callable[[InT], OutT | Iterable[OutT] | None],
        target: str | Channel[OutT],
        *,
        expand_iterables: bool = False,
    ) -> Callable[[InT], None]:
        """Wire an ingress monitor through a reference model function into a target
        channel.

        Args:
            source_monitor: Passive monitor observing DUT inputs.
            model: Pure reference model returning an expected output transaction,
            ``None``
                (if the input produces no output), or—when ``expand_iterables=True``—an
                iterable of output transactions.
            target: Target channel name or :class:`Channel` instance.
            expand_iterables: When ``True``, non-``bytes``/``str`` iterables returned by
                ``model`` are expanded via ``channel.expect_many(...)``.
        """
        match target:
            case Channel() as ch:
                channel: Channel[OutT] = ch
            case str() as ch_name:
                channel = self._channels[ch_name]

        def _on_input(tx: InT) -> None:
            predicted = model(tx)
            match predicted:
                case None:
                    return
                case bytes() | bytearray() | str():
                    channel.expect(cast(OutT, predicted))
                case Iterable() as batch if expand_iterables:
                    channel.expect_many(cast(Iterable[OutT], batch))
                case single:
                    channel.expect(cast(OutT, single))

        source_monitor.add_callback(_on_input)
        return _on_input

    def summary(self) -> str:
        """Return a formatted multi-line status summary across all channels."""
        lines = [f"Scoreboard '{self.name}' summary:"]
        for ch in self._channels.values():
            lines.append(
                f"  - {ch.name}: received={ch.received_count}, "
                f"matched={ch.matched_count}, "
                f"mismatches={len(ch.mismatches)}, pending={ch.pending_count}"
            )
        return "\n".join(lines)

    def assert_clean(self, *, allow_pending: bool = False) -> None:
        """Raise :class:`ScoreboardError` if any mismatch occurred or expected items
        remain.
        """
        errors: list[str] = []
        for ch in self._channels.values():
            for mismatch in ch.mismatches:
                errors.append(mismatch.format())
            if not allow_pending and ch.pending_count > 0:
                pending = ch.matcher.pending_items()
                preview = ", ".join(format_value(item) for item in pending[:5])
                more = f" (+{len(pending) - 5} more)" if len(pending) > 5 else ""
                errors.append(
                    f"[{ch.name}] {ch.pending_count} expected transaction(s) "
                    f"never received: [{preview}{more}]"
                )
        if errors:
            raise ScoreboardError(
                f"Scoreboard '{self.name}' failed:\n"
                + "\n".join(f"  * {err}" for err in errors)
                + f"\n{self.summary()}"
            )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.assert_clean(allow_pending=self.allow_pending_on_exit)
