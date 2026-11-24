"""Interoperability adapters for ``cocotbext-*`` and legacy ``cocotb-bus``."""

from cocotb_bfm.compat.adapters import (
    AsyncSourceAdapter,
    LegacyBusShim,
    LegacyMonitorShim,
    QueueAdapter,
    as_legacy_bus,
    as_legacy_monitor,
)

__all__ = [
    "AsyncSourceAdapter",
    "LegacyBusShim",
    "LegacyMonitorShim",
    "QueueAdapter",
    "as_legacy_bus",
    "as_legacy_monitor",
]
