"""Unit tests for ``Scoreboard``, matchers, diff formatting, and legacy interop."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

import pytest
from cocotb.types import LogicArray
from cocotb_bus.monitors import (  # type: ignore[import-untyped]
    Monitor as LegacyCocotbMonitor,
)
from cocotb_bus.scoreboard import (  # type: ignore[import-untyped]
    Scoreboard as LegacyCocotbScoreboard,
)

from cocotb_bfm import (
    Channel,
    InOrderMatcher,
    KeyedMatcher,
    MonitorProtocol,
    Scoreboard,
    ScoreboardError,
    UnorderedMatcher,
    WindowMatcher,
    as_legacy_monitor,
    format_diff,
)
from tests.unit.fakes import FakeHierarchyObject


@dataclass(frozen=True)
class TaggedTx:
    tid: int
    payload: int


class StubMonitor:
    """Minimal monitor satisfying :class:`MonitorProtocol` for unit tests."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name
        self._callbacks: list[Callable[[Any], Any]] = []

    @property
    def name(self) -> str:
        return self._name

    def add_callback(self, callback: Callable[[Any], Any]) -> Any:
        self._callbacks.append(callback)
        return callback

    def emit(self, tx: Any) -> None:
        for cb in list(self._callbacks):
            cb(tx)


def test_in_order_scoreboard_pass_and_mismatch() -> None:
    mon = StubMonitor("egress")
    assert isinstance(mon, MonitorProtocol)

    sb = Scoreboard(name="test_sb", fail_immediately=True)
    ch = sb.add_channel("egress", mon, matcher=InOrderMatcher())
    ch.expect_many([TaggedTx(0, 10), TaggedTx(1, 20)])

    mon.emit(TaggedTx(0, 10))
    with pytest.raises(
        ScoreboardError, match=r"\.payload: expected 20 \(0x14\), got 99 \(0x63\)"
    ):
        mon.emit(TaggedTx(1, 99))


def test_window_matcher_out_of_order() -> None:
    sb = Scoreboard(name="window_sb", fail_immediately=False)
    ch: Channel[int] = sb.add_channel("ch", matcher=WindowMatcher(depth=2))
    ch.expect_many([1, 2, 3, 4])

    assert ch.check(3) is True
    assert ch.check(1) is True
    assert ch.check(4) is True
    assert ch.check(2) is True
    sb.assert_clean()


def test_keyed_matcher_id_based_ooo() -> None:
    sb = Scoreboard(name="axi_sb")
    ch: Channel[TaggedTx] = sb.add_channel(
        "resp", matcher=KeyedMatcher(key=lambda t: t.tid)
    )

    ch.expect(TaggedTx(tid=1, payload=0xAA))
    ch.expect(TaggedTx(tid=1, payload=0xBB))
    ch.expect(TaggedTx(tid=2, payload=0xCC))

    # Different IDs can arrive out of order.
    assert ch.check(TaggedTx(tid=2, payload=0xCC)) is True
    assert ch.check(TaggedTx(tid=1, payload=0xAA)) is True
    assert ch.check(TaggedTx(tid=1, payload=0xBB)) is True
    sb.assert_clean()

    # Same ID out of order fails cleanly.
    ch.expect(TaggedTx(tid=5, payload=1))
    ch.expect(TaggedTx(tid=5, payload=2))
    with pytest.raises(ScoreboardError, match=r"\[key=5\]"):
        ch.check(TaggedTx(tid=5, payload=2))


def test_unordered_matcher_and_pending_failure() -> None:
    sb = Scoreboard(name="bag_sb")
    ch: Channel[str] = sb.add_channel("bag", matcher=UnorderedMatcher())
    ch.expect_many(["a", "b", "c"])
    ch.check("c")
    ch.check("a")
    with pytest.raises(
        ScoreboardError, match="1 expected transaction\\(s\\) never received"
    ):
        sb.assert_clean()


def test_connect_model_single_filter_and_expand() -> None:
    ingress = StubMonitor("in")
    egress = StubMonitor("out")

    sb = Scoreboard(name="model_sb")
    out_ch = sb.add_channel("out", egress)

    def ref_model(x: int) -> list[int] | None:
        if x == 0:
            return None  # dropped
        return [x * 2, x * 2 + 1]

    sb.connect_model(ingress, ref_model, out_ch, expand_iterables=True)

    ingress.emit(0)
    ingress.emit(5)
    assert out_ch.pending_count == 2

    egress.emit(10)
    egress.emit(11)
    sb.assert_clean()


def test_format_diff_with_logic_array_and_x_bits() -> None:
    exp = LogicArray("10101100")
    act = LogicArray("1010XX00")
    diff = format_diff(exp, act)
    assert "0xac" in diff
    assert "unresolvable" in diff


def test_legacy_cocotb_bus_scoreboard_contract() -> None:
    """Verify real cocotb_bus.scoreboard.Scoreboard works via as_legacy_monitor."""
    dut = FakeHierarchyObject("dut")
    mon = StubMonitor("legacy_mon")
    shim = as_legacy_monitor(mon)
    assert isinstance(shim, LegacyCocotbMonitor)

    expected = [TaggedTx(0, 1), TaggedTx(1, 2)]
    legacy_sb = LegacyCocotbScoreboard(dut, fail_immediately=True)
    legacy_sb.add_interface(shim, expected)

    mon.emit(TaggedTx(0, 1))
    mon.emit(TaggedTx(1, 2))
    assert expected == []
    assert legacy_sb.errors == 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with pytest.raises(BaseException) as exc_info:
            _ = legacy_sb.result
    assert type(exc_info.value).__name__ == "TestSuccess"

    # Also verify real cocotb_bus.Scoreboard fails on unconsumed expected output
    # and on mismatched transactions!
    pending_sb = LegacyCocotbScoreboard(dut, fail_immediately=True)
    pending_sb.add_interface(
        as_legacy_monitor(StubMonitor("pend_mon")), [TaggedTx(0, 1)]
    )
    with pytest.raises(AssertionError, match="Not all expected output was received"):
        _ = pending_sb.result

    expected_bad = [TaggedTx(0, 99)]
    legacy_sb_bad = LegacyCocotbScoreboard(dut, fail_immediately=True)
    mon_bad = StubMonitor("bad_mon")
    legacy_sb_bad.add_interface(as_legacy_monitor(mon_bad), expected_bad)
    with pytest.raises(AssertionError, match="differed from expected"):
        mon_bad.emit(TaggedTx(0, 123))


def test_namedtuple_not_exploded_and_timeout_note_attached() -> None:
    class PairTx(NamedTuple):
        addr: int
        data: int

    ingress = StubMonitor("in")
    egress = StubMonitor("out")
    sb = Scoreboard(name="nt_sb")
    ch = sb.add_channel("out", egress)
    sb.connect_model(
        ingress, lambda x: PairTx(addr=x, data=x + 1), ch, expand_iterables=True
    )

    ingress.emit(10)
    assert ch.pending_count == 1
    egress.emit(PairTx(10, 11))
    sb.assert_clean()

    # Verify __aexit__ attaches PEP 678 note when an outer exception (e.g. timeout)
    # exits while expected items are still pending!
    import asyncio

    async def _sim_timeout() -> None:
        async with Scoreboard(name="timeout_sb") as tsb:
            tch: Channel[PairTx] = tsb.add_channel("egress")
            tch.expect(PairTx(1, 2))
            raise TimeoutError("simulated watchdog timeout")

    with pytest.raises(TimeoutError) as exc_info:
        asyncio.run(_sim_timeout())
    notes = getattr(exc_info.value, "__notes__", [])
    assert any("pending=1" in n for n in notes)
