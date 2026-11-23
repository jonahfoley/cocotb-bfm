"""Interoperability adapters for ``cocotbext-*`` and legacy ``cocotb-bus``."""

from cocotb_bfm.compat.adapters import (
    AsyncSourceAdapter,
    LegacyBusShim,
    QueueAdapter,
    as_legacy_bus,
)

__all__ = [
    "AsyncSourceAdapter",
    "LegacyBusShim",
    "QueueAdapter",
    "as_legacy_bus",
]
