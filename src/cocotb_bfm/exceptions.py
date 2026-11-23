"""Exception hierarchy for ``cocotb_bfm``."""

from __future__ import annotations


class BfmError(Exception):
    """Base exception for all ``cocotb_bfm`` errors."""


class BusBindingError(BfmError):
    """Raised when a ``Bus`` cannot bind one or more declared signals."""


class SignalNotFoundError(BusBindingError):
    """Raised when a required signal handle cannot be found in the target scope."""


class SignalTypeError(BusBindingError):
    """Raised when a handle does not match the declared cocotb 2.0 handle type."""


class SignalWidthError(BusBindingError):
    """Raised when a handle's bit width violates a declared width constraint."""


class BusDirectionError(BfmError):
    """Raised when driving a signal whose direction forbids the caller's role."""


class ComponentLifecycleError(BfmError):
    """Raised on invalid component lifecycle transitions (e.g., starting twice)."""


class ResetError(BfmError):
    """Raised when an operation is aborted because reset asserted."""


class ScoreboardError(BfmError, AssertionError):
    """Raised when a scoreboard detects a mismatch or undrained expected transactions.

    Inherits from both :class:`BfmError` and :class:`AssertionError` so cocotb and
    pytest treat scoreboard failures as standard test failures.
    """
