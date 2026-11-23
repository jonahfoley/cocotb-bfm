"""Runtime validation of resolved cocotb 2.0 simulation handles."""

from __future__ import annotations

from typing import Any

from cocotb.handle import (
    HierarchyArrayObject,
    HierarchyObject,
    LogicArrayObject,
    LogicObject,
    PackedObject,
)

from cocotb_bfm.bus.spec import SignalSpec
from cocotb_bfm.exceptions import SignalTypeError, SignalWidthError


def handle_path(handle: Any) -> str:
    """Return the best available hierarchical path string for a simulation handle."""
    match getattr(handle, "_path", None):
        case str() as path if path:
            return path
    match getattr(handle, "_name", None):
        case str() as name if name:
            return name
    return repr(handle)


def handle_width(handle: Any) -> int | None:
    """Return the bit/element width of ``handle``, or ``None`` if unsized or a hierarchy scope."""
    match handle:
        case LogicObject():
            return 1
        case HierarchyObject() | HierarchyArrayObject():
            return None
        case _:
            try:
                return len(handle)
            except TypeError:
                return None


def _is_1bit_vector(handle: Any) -> bool:
    """Return ``True`` if ``handle`` is a 1-bit logic vector / packed object."""
    match handle:
        case LogicArrayObject() | PackedObject():
            try:
                return len(handle) == 1
            except TypeError:
                return False
        case _:
            return False


def validate_handle(
    bus_cls_name: str,
    spec: SignalSpec,
    handle: Any,
    *,
    strict_types: bool = False,
) -> Any:
    """Validate that ``handle`` satisfies ``spec``'s type and width constraints.

    When ``strict_types`` is ``False`` (the default), a 1-bit
    :class:`~cocotb.handle.LogicArrayObject` or :class:`~cocotb.handle.PackedObject`
    is accepted for fields annotated as :class:`~cocotb.handle.LogicObject`. This
    prevents spurious failures on parameterized RTL such as ``logic [0:0]`` or
    ``std_logic_vector(0 downto 0)`` across Verilator, Xcelium, and Riviera-PRO.
    """
    if handle is None:
        if spec.optional:
            return None
        raise SignalTypeError(f"{bus_cls_name}.{spec.attr_name}: required signal cannot be None")

    if spec.expected_types:
        matches = isinstance(handle, spec.expected_types)
        if not matches and not strict_types:
            match handle:
                case LogicArrayObject() | PackedObject() if (
                    LogicObject in spec.expected_types and _is_1bit_vector(handle)
                ):
                    matches = True
                case PackedObject() if LogicArrayObject in spec.expected_types:
                    # cocotb 2.1 maps Verilog packed vectors to PackedObject while cocotb 2.0
                    # and VHDL map them to LogicArrayObject; both share _LogicArrayObjectBase.
                    matches = True
                case LogicArrayObject() if PackedObject in spec.expected_types:
                    matches = True
                case _:
                    pass
        if not matches:
            expected_names = " | ".join(t.__name__ for t in spec.expected_types)
            actual_type = type(handle).__name__
            extra = ""
            if strict_types:
                match handle:
                    case _ if LogicObject in spec.expected_types and _is_1bit_vector(handle):
                        extra = (
                            " (pass strict_types=False to allow 1-bit vector handles on "
                            "LogicObject)"
                        )
                    case PackedObject() if LogicArrayObject in spec.expected_types:
                        extra = (
                            " (in cocotb 2.1+, Verilog packed vectors are PackedObject; "
                            "annotate with VectorObject or pass strict_types=False)"
                        )
                    case _:
                        pass
            raise SignalTypeError(
                f"{bus_cls_name}.{spec.attr_name} (handle '{handle_path(handle)}'): "
                f"expected {expected_names}, got {actual_type}{extra}"
            )

    if spec.width is not None or spec.min_width is not None or spec.max_width is not None:
        actual_width = handle_width(handle)
        if actual_width is None:
            raise SignalWidthError(
                f"{bus_cls_name}.{spec.attr_name} (handle '{handle_path(handle)}'): "
                f"handle of type {type(handle).__name__} has no measurable width"
            )
        if spec.width is not None and actual_width != spec.width:
            raise SignalWidthError(
                f"{bus_cls_name}.{spec.attr_name} (handle '{handle_path(handle)}'): "
                f"expected width {spec.width}, got {actual_width}"
            )
        if spec.min_width is not None and actual_width < spec.min_width:
            raise SignalWidthError(
                f"{bus_cls_name}.{spec.attr_name} (handle '{handle_path(handle)}'): "
                f"expected width >= {spec.min_width}, got {actual_width}"
            )
        if spec.max_width is not None and actual_width > spec.max_width:
            raise SignalWidthError(
                f"{bus_cls_name}.{spec.attr_name} (handle '{handle_path(handle)}'): "
                f"expected width <= {spec.max_width}, got {actual_width}"
            )

    return handle
