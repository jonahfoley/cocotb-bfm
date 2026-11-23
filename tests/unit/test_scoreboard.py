"""Unit tests for ``Scoreboard``, matchers, diff formatting, and legacy interop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from cocotb.types import LogicArray

from cocotb_bfm import (
    InOrderMatcher,
    KeyedMatcher,
    MonitorProtocol,
    Scoreboard,
    ScoreboardError,
    UnorderedMatcher,
    WindowMatcher,
    format_diff,
)


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
    ch = sb.add_channel("ch", matcher=WindowMatcher(depth=2))
    ch.expect_many([1, 2, 3, 4])

    assert ch.check(3) is True
    assert ch.check(1) is True
    assert ch.check(4) is True
    assert ch.check(2) is True
    sb.assert_clean()


def test_keyed_matcher_id_based_ooo() -> None:
    sb = Scoreboard(name="axi_sb")
    ch = sb.add_channel("resp", matcher=KeyedMatcher(key=lambda t: t.tid))

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
    ch = sb.add_channel("bag", matcher=UnorderedMatcher())
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
    """Verify monitor contract works with legacy Scoreboard.add_interface."""

    class LegacyScoreboardMock:
        """Mirrors the exact monitor interaction in cocotb_bus.scoreboard.Scoreboard."""

        def __init__(self) -> None:
            self.received: list[Any] = []

        def add_interface(self, monitor: Any, expected_output: list[Any]) -> None:
            def _check(tx: Any) -> None:
                exp = expected_output.pop(0)
                assert tx == exp
                self.received.append(tx)

            monitor.add_callback(_check)

    mon = StubMonitor("legacy_mon")
    expected = [TaggedTx(0, 1), TaggedTx(1, 2)]
    legacy = LegacyScoreboardMock()
    legacy.add_interface(mon, expected)

    mon.emit(TaggedTx(0, 1))
    mon.emit(TaggedTx(1, 2))
    assert expected == []
    assert len(legacy.received) == 2
