"""Statically typed SystemVerilog packed-struct codec for cocotb 2.0 handles."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Self, dataclass_transform

from cocotb.types import LogicArray

from cocotb_bfm.bus.validation import handle_path, handle_width
from cocotb_bfm.exceptions import BusBindingError, SignalWidthError


@dataclass(frozen=True, slots=True)
class BitFieldSpec:
    """Bit-slice layout for one :class:`PackedStruct` field (MSB-first SV order)."""

    name: str
    width: int
    lsb: int
    msb: int
    signed: bool = False
    default: int = 0


@dataclass(frozen=True, slots=True)
class _BitFieldMarker:
    width: int
    default: int = 0
    signed: bool = False


def bitfield(width: int, *, default: int = 0, signed: bool = False) -> Any:
    """Declare a bitfield on a :class:`PackedStruct` (MSB-first SV packed order).

    Args:
        width: Field width in bits (must be >= 1).
        default: Default integer value when omitted from construction.
        signed: Interpret the bitfield as two's-complement signed on unpack.
    """
    if width < 1:
        raise ValueError(f"bitfield width must be >= 1, got {width}")
    return _BitFieldMarker(width=width, default=default, signed=signed)


@dataclass_transform(
    eq_default=True,
    kw_only_default=False,
    field_specifiers=(bitfield,),
)
class PackedStruct:
    """Statically typed codec for SystemVerilog ``struct packed`` signals.

    Declare fields in the exact **MSB-to-LSB** order of the SystemVerilog
    ``typedef struct packed`` declaration::

        class CsrMeta(PackedStruct):
            tag: int = bitfield(4)
            priv: int = bitfield(2)
            enable: int = bitfield(1)
            irq_en: int = bitfield(1)

    Works identically across Verilator (which exposes packed structs as flat
    ``PackedObject`` vectors), Xcelium, and Riviera-PRO (whether exposed as a
    ``PackedObject`` or a hierarchical scope of sub-fields).
    """

    _bitfields: ClassVar[tuple[BitFieldSpec, ...]] = ()
    _total_width: ClassVar[int] = 0

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        raw_fields: list[tuple[str, _BitFieldMarker]] = []
        for base in reversed(cls.__mro__[1:]):
            if base is PackedStruct or not issubclass(base, PackedStruct):
                continue
            for spec in base._bitfields:
                raw_fields.append(
                    (
                        spec.name,
                        _BitFieldMarker(
                            width=spec.width,
                            default=spec.default,
                            signed=spec.signed,
                        ),
                    )
                )

        own_annotations = inspect.get_annotations(cls)
        for attr_name in own_annotations:
            if attr_name.startswith("_"):
                continue
            marker = cls.__dict__.get(attr_name)
            match marker:
                case _BitFieldMarker():
                    raw_fields = [(n, m) for n, m in raw_fields if n != attr_name]
                    raw_fields.append((attr_name, marker))
                    delattr(cls, attr_name)
                case _:
                    raise BusBindingError(
                        f"{cls.__name__}.{attr_name}: PackedStruct fields must use "
                        "bitfield(width)"
                    )

        total = sum(m.width for _, m in raw_fields)
        bit_pos = total
        specs: list[BitFieldSpec] = []
        for name, marker in raw_fields:
            msb = bit_pos - 1
            lsb = bit_pos - marker.width
            bit_pos = lsb
            specs.append(
                BitFieldSpec(
                    name=name,
                    width=marker.width,
                    lsb=lsb,
                    msb=msb,
                    signed=marker.signed,
                    default=marker.default,
                )
            )

        cls._bitfields = tuple(specs)
        cls._total_width = total

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        specs = self._bitfields
        if len(args) > len(specs):
            cls_name = type(self).__name__
            raise TypeError(
                f"{cls_name}() takes at most {len(specs)} positional arguments "
                f"but {len(args)} were given"
            )

        bound: dict[str, int] = {}
        for spec, val in zip(specs, args, strict=False):
            bound[spec.name] = int(val)

        valid_names = {s.name for s in specs}
        for key, val in kwargs.items():
            if key not in valid_names:
                cls_name = type(self).__name__
                raise TypeError(
                    f"{cls_name}() got an unexpected keyword argument {key!r}"
                )
            if key in bound:
                raise TypeError(
                    f"{type(self).__name__}() got multiple values for argument {key!r}"
                )
            bound[key] = int(val)

        for spec in specs:
            val = bound.get(spec.name, spec.default)
            _check_field_range(type(self).__name__, spec, val)
            object.__setattr__(self, spec.name, val)

    @classmethod
    def total_width(cls) -> int:
        """Total bit width of the packed struct."""
        return cls._total_width

    @classmethod
    def bitfields(cls) -> tuple[BitFieldSpec, ...]:
        """Ordered bitfield layout specifications (MSB to LSB)."""
        return cls._bitfields

    def to_int(self) -> int:
        """Pack all fields into a single unsigned integer."""
        acc = 0
        for spec in self._bitfields:
            raw = int(getattr(self, spec.name))
            mask = (1 << spec.width) - 1
            acc |= (raw & mask) << spec.lsb
        return acc

    def to_logic_array(self) -> LogicArray:
        """Pack all fields into a cocotb 2.0 :class:`~cocotb.types.LogicArray`."""
        return LogicArray.from_unsigned(self.to_int(), self._total_width)

    @classmethod
    def unpack(cls, value: int | LogicArray) -> Self:
        """Unpack an integer or :class:`~cocotb.types.LogicArray` into ``cls``."""
        match value:
            case LogicArray() as arr:
                if len(arr) != cls._total_width:
                    raise SignalWidthError(
                        f"{cls.__name__}.unpack: expected {cls._total_width}-bit "
                        f"LogicArray, got {len(arr)} bits"
                    )
                raw_int = arr.to_unsigned()
            case int() as integer:
                raw_int = integer

        kwargs: dict[str, int] = {}
        for spec in cls._bitfields:
            mask = (1 << spec.width) - 1
            bits = (raw_int >> spec.lsb) & mask
            if spec.signed and (bits & (1 << (spec.width - 1))):
                bits -= 1 << spec.width
            kwargs[spec.name] = bits
        return cls(**kwargs)

    @classmethod
    def from_handle(cls, handle: Any) -> Self:
        """Read and unpack a packed-struct handle (``PackedObject`` or scope)."""
        actual_width = handle_width(handle)
        if actual_width is not None:
            if actual_width != cls._total_width:
                raise SignalWidthError(
                    f"{cls.__name__}.from_handle('{handle_path(handle)}'): "
                    f"expected width {cls._total_width}, got {actual_width}"
                )
            return cls.unpack(handle.value)

        # Fallback for commercial simulators exposing a struct as a navigable scope.
        kwargs: dict[str, int] = {}
        for spec in cls._bitfields:
            child = handle[spec.name]
            val = child.value
            kwargs[spec.name] = (
                val.to_signed()
                if spec.signed and hasattr(val, "to_signed")
                else val.to_unsigned()
                if hasattr(val, "to_unsigned")
                else int(val)
            )
        return cls(**kwargs)

    def write_handle(self, handle: Any) -> None:
        """Pack and drive this struct onto a handle (``PackedObject`` or scope)."""
        actual_width = handle_width(handle)
        if actual_width is not None:
            if actual_width != self._total_width:
                raise SignalWidthError(
                    f"{type(self).__name__}.write_handle('{handle_path(handle)}'): "
                    f"expected width {self._total_width}, got {actual_width}"
                )
            handle.value = self.to_logic_array()
            return

        for spec in self._bitfields:
            handle[spec.name].value = getattr(self, spec.name)

    def as_dict(self) -> Mapping[str, int]:
        """Return field values as a dictionary."""
        return {spec.name: int(getattr(self, spec.name)) for spec in self._bitfields}

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return all(
            getattr(self, spec.name) == getattr(other, spec.name)
            for spec in self._bitfields
        )

    def __hash__(self) -> int:
        return hash(
            (type(self), *(getattr(self, spec.name) for spec in self._bitfields))
        )

    def __repr__(self) -> str:
        fields_str = ", ".join(
            f"{s.name}={getattr(self, s.name)!r}" for s in self._bitfields
        )
        return f"{type(self).__name__}({fields_str})"


def _check_field_range(cls_name: str, spec: BitFieldSpec, value: int) -> None:
    if spec.signed:
        lo = -(1 << (spec.width - 1))
        hi = (1 << (spec.width - 1)) - 1
    else:
        lo = 0
        hi = (1 << spec.width) - 1
    if not (lo <= value <= hi):
        kind = "signed" if spec.signed else "unsigned"
        raise ValueError(
            f"{cls_name}.{spec.name} ({spec.width}-bit {kind}): "
            f"value {value} out of range [{lo}, {hi}]"
        )
