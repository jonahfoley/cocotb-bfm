"""Modern generic ``Scoreboard``, matchers, and diff formatters."""

from cocotb_bfm.scoreboard.diff import format_diff, format_value
from cocotb_bfm.scoreboard.matchers import (
    InOrderMatcher,
    KeyedMatcher,
    Matcher,
    MatchResult,
    UnorderedMatcher,
    WindowMatcher,
)
from cocotb_bfm.scoreboard.scoreboard import Channel, MismatchRecord, Scoreboard

__all__ = [
    "Channel",
    "InOrderMatcher",
    "KeyedMatcher",
    "MatchResult",
    "Matcher",
    "MismatchRecord",
    "Scoreboard",
    "UnorderedMatcher",
    "WindowMatcher",
    "format_diff",
    "format_value",
]
