"""Pluggable transaction matchers for :class:`Scoreboard` channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from cocotb_bfm.scoreboard.diff import format_diff, format_value

TxT = TypeVar("TxT")
KeyT = TypeVar("KeyT")


def _default_compare(expected: Any, actual: Any) -> bool:
    return bool(expected == actual)


@dataclass(frozen=True, slots=True)
class MatchResult(Generic[TxT]):
    """Outcome of matching one actual transaction against expected transactions."""

    matched: bool
    expected: TxT | None
    actual: TxT
    detail: str = ""


class Matcher(ABC, Generic[TxT]):
    """Abstract matching strategy for a :class:`Channel`."""

    @abstractmethod
    def expect(self, transaction: TxT) -> None:
        """Record an expected transaction."""

    @abstractmethod
    def match(self, actual: TxT) -> MatchResult[TxT]:
        """Match an observed transaction against expected state."""

    @abstractmethod
    def pending_count(self) -> int:
        """Return the number of expected transactions not yet observed."""

    @abstractmethod
    def pending_items(self) -> list[TxT]:
        """Return a snapshot list of all unmatched expected transactions."""

    @abstractmethod
    def clear(self) -> None:
        """Discard all pending expected transactions."""


class InOrderMatcher(Matcher[TxT]):
    """Strict FIFO matcher comparing each observed item to the oldest expected item."""

    def __init__(
        self,
        *,
        compare: Callable[[TxT, TxT], bool] | None = None,
    ) -> None:
        self._compare: Callable[[TxT, TxT], bool] = (
            compare if compare is not None else _default_compare
        )
        self._expected: deque[TxT] = deque()

    def expect(self, transaction: TxT) -> None:
        self._expected.append(transaction)

    def match(self, actual: TxT) -> MatchResult[TxT]:
        if not self._expected:
            return MatchResult(
                matched=False,
                expected=None,
                actual=actual,
                detail=(
                    f"Unexpected transaction (expected queue empty): "
                    f"{format_value(actual)}"
                ),
            )
        expected = self._expected.popleft()
        if self._compare(expected, actual):
            return MatchResult(matched=True, expected=expected, actual=actual)
        return MatchResult(
            matched=False,
            expected=expected,
            actual=actual,
            detail=format_diff(expected, actual),
        )

    def pending_count(self) -> int:
        return len(self._expected)

    def pending_items(self) -> list[TxT]:
        return list(self._expected)

    def clear(self) -> None:
        self._expected.clear()


class WindowMatcher(Matcher[TxT]):
    """Bounded out-of-order matcher searching up to ``depth`` items ahead in the
    expected queue.

    ``depth=0`` behaves identically to :class:`InOrderMatcher`; ``depth=N`` searches
    the first ``N + 1`` expected transactions and removes the earliest matching item.
    """

    def __init__(
        self,
        depth: int,
        *,
        compare: Callable[[TxT, TxT], bool] | None = None,
    ) -> None:
        if depth < 0:
            raise ValueError(f"WindowMatcher depth must be >= 0, got {depth}")
        self.depth = depth
        self._compare: Callable[[TxT, TxT], bool] = (
            compare if compare is not None else _default_compare
        )
        self._expected: deque[TxT] = deque()

    def expect(self, transaction: TxT) -> None:
        self._expected.append(transaction)

    def match(self, actual: TxT) -> MatchResult[TxT]:
        if not self._expected:
            return MatchResult(
                matched=False,
                expected=None,
                actual=actual,
                detail=(
                    f"Unexpected transaction (expected queue empty): "
                    f"{format_value(actual)}"
                ),
            )
        limit = min(self.depth + 1, len(self._expected))
        for idx in range(limit):
            candidate = self._expected[idx]
            if self._compare(candidate, actual):
                del self._expected[idx]
                return MatchResult(matched=True, expected=candidate, actual=actual)

        head = self._expected[0]
        return MatchResult(
            matched=False,
            expected=head,
            actual=actual,
            detail=(
                f"No match within reorder window of {limit} item(s). "
                f"Head diff: {format_diff(head, actual)}"
            ),
        )

    def pending_count(self) -> int:
        return len(self._expected)

    def pending_items(self) -> list[TxT]:
        return list(self._expected)

    def clear(self) -> None:
        self._expected.clear()


class KeyedMatcher(Matcher[TxT], Generic[TxT, KeyT]):
    """Per-key FIFO matcher for ID/tag-based out-of-order protocols (e.g., AXI, PCIe).

    Transactions with distinct keys may complete in any order, while transactions
    sharing the same key must arrive in FIFO order.
    """

    def __init__(
        self,
        key: Callable[[TxT], KeyT],
        *,
        compare: Callable[[TxT, TxT], bool] | None = None,
    ) -> None:
        self._key = key
        self._compare: Callable[[TxT, TxT], bool] = (
            compare if compare is not None else _default_compare
        )
        self._queues: dict[KeyT, deque[TxT]] = defaultdict(deque)

    def expect(self, transaction: TxT) -> None:
        self._queues[self._key(transaction)].append(transaction)

    def match(self, actual: TxT) -> MatchResult[TxT]:
        tx_key = self._key(actual)
        bucket = self._queues.get(tx_key)
        if not bucket:
            return MatchResult(
                matched=False,
                expected=None,
                actual=actual,
                detail=(
                    f"Unexpected transaction for key {tx_key!r} "
                    f"(no pending expected items for this key): {format_value(actual)}"
                ),
            )
        expected = bucket.popleft()
        if not bucket:
            del self._queues[tx_key]
        if self._compare(expected, actual):
            return MatchResult(matched=True, expected=expected, actual=actual)
        return MatchResult(
            matched=False,
            expected=expected,
            actual=actual,
            detail=f"[key={tx_key!r}] {format_diff(expected, actual)}",
        )

    def pending_count(self) -> int:
        return sum(len(q) for q in self._queues.values())

    def pending_items(self) -> list[TxT]:
        items: list[TxT] = []
        for q in self._queues.values():
            items.extend(q)
        return items

    def clear(self) -> None:
        self._queues.clear()


class UnorderedMatcher(Matcher[TxT]):
    """Unordered bag/multiset matcher allowing transactions to arrive in any order."""

    def __init__(
        self,
        *,
        compare: Callable[[TxT, TxT], bool] | None = None,
    ) -> None:
        self._compare: Callable[[TxT, TxT], bool] = (
            compare if compare is not None else _default_compare
        )
        self._expected: list[TxT] = []

    def expect(self, transaction: TxT) -> None:
        self._expected.append(transaction)

    def match(self, actual: TxT) -> MatchResult[TxT]:
        for idx, candidate in enumerate(self._expected):
            if self._compare(candidate, actual):
                self._expected.pop(idx)
                return MatchResult(matched=True, expected=candidate, actual=actual)
        return MatchResult(
            matched=False,
            expected=self._expected[0] if self._expected else None,
            actual=actual,
            detail=(
                f"No matching expected transaction among {len(self._expected)} "
                f"pending item(s): got {format_value(actual)}"
            ),
        )

    def pending_count(self) -> int:
        return len(self._expected)

    def pending_items(self) -> list[TxT]:
        return list(self._expected)

    def clear(self) -> None:
        self._expected.clear()
