"""Typed ``Bus`` definitions and pluggable signal resolvers."""

from cocotb_bfm.bus.base import Bus
from cocotb_bfm.bus.packed import BitFieldSpec, PackedStruct, bitfield
from cocotb_bfm.bus.resolvers import (
    CompositeResolver,
    InterfaceResolver,
    MappingResolver,
    PrefixResolver,
    RegexResolver,
    SignalResolver,
    list_scope_keys,
    lookup_child,
    resolve_path,
)
from cocotb_bfm.bus.spec import (
    Direction,
    Role,
    SignalSpec,
    SubBusSpec,
    VectorObject,
    signal,
    sub_bus,
)
from cocotb_bfm.bus.validation import handle_path, handle_width, validate_handle

__all__ = [
    "BitFieldSpec",
    "Bus",
    "CompositeResolver",
    "Direction",
    "InterfaceResolver",
    "MappingResolver",
    "PackedStruct",
    "PrefixResolver",
    "RegexResolver",
    "Role",
    "SignalResolver",
    "SignalSpec",
    "SubBusSpec",
    "VectorObject",
    "bitfield",
    "handle_path",
    "handle_width",
    "list_scope_keys",
    "lookup_child",
    "resolve_path",
    "signal",
    "sub_bus",
    "validate_handle",
]
