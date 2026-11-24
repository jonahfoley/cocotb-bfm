"""Declarative signal and sub-bus specifications for typed ``Bus`` definitions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeAlias

from cocotb.handle import LogicArrayObject, PackedObject

if TYPE_CHECKING:
    from cocotb_bfm.bus.base import Bus

VectorObject: TypeAlias = LogicArrayObject | PackedObject[Any]
"""Cross-version (cocotb 2.0/2.1+) and cross-language packed vector handle type."""


class _MissingType:
    """Sentinel representing an omitted default argument."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _MissingType()


class Direction(Enum):
    """Signal ownership direction relative to initiator and target roles."""

    INITIATOR = "initiator"
    TARGET = "target"
    BIDIR = "bidir"
    CLOCK = "clock"
    RESET = "reset"
    UNSPECIFIED = "unspecified"

    def is_driven_by(self, role: Role) -> bool:
        """Return ``True`` if a component with ``role`` may drive this signal."""
        match (role, self):
            case (Role.OBSERVER, _):
                return False
            case (_, Direction.BIDIR | Direction.UNSPECIFIED):
                return True
            case (Role.INITIATOR, Direction.INITIATOR):
                return True
            case (Role.TARGET, Direction.TARGET):
                return True
            case _:
                return False


class Role(Enum):
    """Role played by a component attached to a bus."""

    INITIATOR = "initiator"
    TARGET = "target"
    OBSERVER = "observer"


@dataclass(frozen=True, slots=True)
class SignalSpec:
    """Introspectable metadata for a single signal declared on a :class:`Bus`."""

    attr_name: str
    hdl_name: str
    expected_types: tuple[type[Any], ...]
    optional: bool
    has_default: bool = False
    default: Any = MISSING
    aliases: tuple[str, ...] = ()
    direction: Direction = Direction.UNSPECIFIED
    width: int | None = None
    min_width: int | None = None
    max_width: int | None = None
    idle_value: Any = MISSING
    description: str = ""

    @property
    def candidate_names(self) -> tuple[str, ...]:
        """Return all HDL names to attempt in priority order."""
        if not self.aliases:
            return (self.hdl_name,)
        seen: list[str] = [self.hdl_name]
        for alias in self.aliases:
            if alias not in seen:
                seen.append(alias)
        return tuple(seen)

    @property
    def has_idle_value(self) -> bool:
        """Return ``True`` if an explicit idle value was configured."""
        return self.idle_value is not MISSING


@dataclass(frozen=True, slots=True)
class SubBusSpec:
    """Introspectable metadata for a nested sub-bus declared on a :class:`Bus`."""

    attr_name: str
    bus_type: type[Bus]
    hdl_name: str
    optional: bool
    has_default: bool = False
    default: Any = MISSING
    separator: str | None = None
    aliases: tuple[str, ...] = ()
    description: str = ""

    @property
    def candidate_names(self) -> tuple[str, ...]:
        """Return all HDL names/prefixes to attempt in priority order."""
        if not self.aliases:
            return (self.hdl_name,)
        seen: list[str] = [self.hdl_name]
        for alias in self.aliases:
            if alias not in seen:
                seen.append(alias)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class _SignalMarker:
    """Runtime marker returned by :func:`signal` during class definition."""

    default: Any = MISSING
    hdl_name: str | None = None
    aliases: tuple[str, ...] = ()
    direction: Direction = Direction.UNSPECIFIED
    width: int | None = None
    min_width: int | None = None
    max_width: int | None = None
    idle_value: Any = MISSING
    description: str = ""


@dataclass(frozen=True, slots=True)
class _SubBusMarker:
    """Runtime marker returned by :func:`sub_bus` during class definition."""

    default: Any = MISSING
    hdl_name: str | None = None
    aliases: tuple[str, ...] = ()
    separator: str | None = None
    description: str = ""


def signal(
    *,
    default: Any = MISSING,
    hdl_name: str | None = None,
    aliases: Sequence[str] = (),
    direction: Direction = Direction.UNSPECIFIED,
    width: int | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
    idle_value: Any = MISSING,
    description: str = "",
) -> Any:
    """Declare signal metadata on a :class:`Bus` subclass.

    Recognized as a PEP 681 ``dataclass_transform`` field specifier so static type
    checkers (``mypy``, ``pyright``, Pylance) preserve exact handle types and
    optionality on the resulting ``Bus`` class.

    Args:
        default: Default value when omitted from direct construction (typically ``None``
            for optional signals). Omit for required signals.
        hdl_name: Explicit HDL signal name if it differs from the Python attribute name.
        aliases: Additional fallback HDL signal names to try during resolution.
        direction: Ownership :class:`Direction` relative to initiator/target.
        width: Exact required bit width (validated at bind time when provided).
        min_width: Minimum allowed bit width (validated at bind time when provided).
        max_width: Maximum allowed bit width (validated at bind time when provided).
        idle_value: Value driven by :meth:`Bus.drive_idle` when resetting/initializing.
        description: Optional human-readable documentation string.
    """
    return _SignalMarker(
        default=default,
        hdl_name=hdl_name,
        aliases=tuple(aliases),
        direction=direction,
        width=width,
        min_width=min_width,
        max_width=max_width,
        idle_value=idle_value,
        description=description,
    )


def sub_bus(
    *,
    default: Any = MISSING,
    hdl_name: str | None = None,
    aliases: Sequence[str] = (),
    separator: str | None = None,
    description: str = "",
) -> Any:
    """Declare nested sub-bus metadata on a :class:`Bus` subclass.

    Recognized as a PEP 681 ``dataclass_transform`` field specifier.
    """
    return _SubBusMarker(
        default=default,
        hdl_name=hdl_name,
        aliases=tuple(aliases),
        separator=separator,
        description=description,
    )
