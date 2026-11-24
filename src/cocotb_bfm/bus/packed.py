"""Statically typed, immutable SystemVerilog packed-struct codec for cocotb 2.0."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, ClassVar, Self, dataclass_transform, get_type_hints

from cocotb.types import LogicArray

from cocotb_bfm.bus.base import write_handle_value
from cocotb_bfm.bus.spec import MISSING
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
    default: Any = MISSING
    enum_type: type[IntEnum] | None = None

    @property
    def has_default(self) -> bool:
        """Return ``True`` if an explicit default value was declared."""
        return self.default is not MISSING


@dataclass(frozen=True, slots=True)
class _BitFieldMarker:
    width: int
    default: Any = MISSING
    signed: bool = False
    enum_type: type[IntEnum] | None = None


def bitfield(
    width: int,
    *,
    default: Any = MISSING,
    signed: bool = False,
    enum: type[IntEnum] | None = None,
) -> Any:
    """Declare a bitfield on a :class:`PackedStruct` (MSB-first SV packed order).

    Args:
        width: Field width in bits (must be >= 1).
        default: Explicit default integer/IntEnum making this field optional in both
            static type checking (PEP 681) and runtime construction.
        signed: Interpret the bitfield as two's-complement signed on unpack.
        enum: Optional :class:`~enum.IntEnum` subclass inferred automatically from
            annotations or specified explicitly for typed enum fields.
    """
    if width < 1:
        raise ValueError(f"bitfield width must be >= 1, got {width}")
    return _BitFieldMarker(width=width, default=default, signed=signed, enum_type=enum)


@dataclass_transform(
    eq_default=True,
    frozen_default=True,
    kw_only_default=False,
    field_specifiers=(bitfield,),
)
class PackedStruct:
    """Statically typed, immutable codec for SystemVerilog ``struct packed`` signals.

    Declare fields in the exact **MSB-to-LSB** order of the SystemVerilog
    ``typedef struct packed`` declaration::

        class CsrMeta(PackedStruct):
            tag: int = bitfield(4, default=0)
            priv: PrivLevel = bitfield(2, default=PrivLevel.USER)
            enable: int = bitfield(1, default=0)
            irq_en: int = bitfield(1, default=0)
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
                            enum_type=spec.enum_type,
                        ),
                    )
                )

        own_annotations = inspect.get_annotations(cls)
        hints = get_type_hints(cls, include_extras=False) if own_annotations else {}
        for attr_name in own_annotations:
            if attr_name.startswith("_"):
                continue
            marker = cls.__dict__.get(attr_name)
            match marker:
                case _BitFieldMarker():
                    hint = hints.get(attr_name)
                    inferred_enum: type[IntEnum] | None = marker.enum_type
                    if inferred_enum is None:
                        match hint:
                            case type() if issubclass(hint, IntEnum):
                                inferred_enum = hint
                    resolved_marker = _BitFieldMarker(
                        width=marker.width,
                        default=marker.default,
                        signed=marker.signed,
                        enum_type=inferred_enum,
                    )
                    raw_fields = [(n, m) for n, m in raw_fields if n != attr_name]
                    raw_fields.append((attr_name, resolved_marker))
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
                    enum_type=marker.enum_type,
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
            if spec.name in bound:
                raw_val = bound[spec.name]
            elif spec.has_default:
                raw_val = int(spec.default)
            else:
                cls_name = type(self).__name__
                raise TypeError(
                    f"{cls_name}() missing required bitfield argument: {spec.name!r}"
                )
            _check_field_range(type(self).__name__, spec, raw_val)
            stored: Any = (
                spec.enum_type(raw_val) if spec.enum_type is not None else raw_val
            )
            object.__setattr__(self, spec.name, stored)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"{type(self).__name__} is immutable; cannot modify field {name!r}"
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"{type(self).__name__} is immutable; cannot delete field {name!r}"
        )

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
    def unpack(cls, value: int | LogicArray, *, source: str | None = None) -> Self:
        """Unpack an integer or :class:`~cocotb.types.LogicArray` into ``cls``."""
        match value:
            case LogicArray() as arr:
                if len(arr) != cls._total_width:
                    ctx = f" from '{source}'" if source else ""
                    raise SignalWidthError(
                        f"{cls.__name__}.unpack{ctx}: expected {cls._total_width}-bit "
                        f"LogicArray, got {len(arr)} bits"
                    )
                if not arr.is_resolvable:
                    ctx = f" ('{source}')" if source else ""
                    raise ValueError(
                        f"{cls.__name__}.unpack{ctx}: cannot unpack unresolved X/Z "
                        f"LogicArray('{arr}')"
                    )
                raw_int = arr.to_unsigned()
            case int() as integer:
                raw_int = integer

        kwargs: dict[str, Any] = {}
        for spec in cls._bitfields:
            mask = (1 << spec.width) - 1
            bits = (raw_int >> spec.lsb) & mask
            if spec.signed and (bits & (1 << (spec.width - 1))):
                bits -= 1 << spec.width
            kwargs[spec.name] = (
                spec.enum_type(bits) if spec.enum_type is not None else bits
            )
        return cls(**kwargs)

    @classmethod
    def from_handle(cls, handle: Any) -> Self:
        """Read and unpack a packed-struct handle (``PackedObject`` or scope)."""
        path = handle_path(handle)
        actual_width = handle_width(handle)
        if actual_width is not None:
            if actual_width != cls._total_width:
                raise SignalWidthError(
                    f"{cls.__name__}.from_handle('{path}'): "
                    f"expected width {cls._total_width}, got {actual_width}"
                )
            return cls.unpack(handle.value, source=path)

        kwargs: dict[str, Any] = {}
        for spec in cls._bitfields:
            child = handle[spec.name]
            val = child.value
            if getattr(val, "is_resolvable", True) is False:
                raise ValueError(
                    f"{cls.__name__}.from_handle('{path}.{spec.name}'): "
                    f"cannot unpack unresolved X/Z value {val!r}"
                )
            raw_val = (
                val.to_signed()
                if spec.signed and hasattr(val, "to_signed")
                else val.to_unsigned()
                if hasattr(val, "to_unsigned")
                else int(val)
            )
            kwargs[spec.name] = (
                spec.enum_type(raw_val) if spec.enum_type is not None else raw_val
            )
        return cls(**kwargs)

    def write_handle(self, handle: Any, *, action: type[Any] | None = None) -> None:
        """Pack and drive this struct onto a handle (``PackedObject`` or scope)."""
        actual_width = handle_width(handle)
        if actual_width is not None:
            if actual_width != self._total_width:
                raise SignalWidthError(
                    f"{type(self).__name__}.write_handle('{handle_path(handle)}'): "
                    f"expected width {self._total_width}, got {actual_width}"
                )
            write_handle_value(handle, self.to_logic_array(), action=action)
            return

        for spec in self._bitfields:
            write_handle_value(
                handle[spec.name], int(getattr(self, spec.name)), action=action
            )

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
