"""Statically typed, immutable ``Bus`` base class for cocotb 2.0+."""

from __future__ import annotations

import inspect
import re
import types
from collections.abc import Mapping
from typing import (
    Any,
    ClassVar,
    Self,
    Union,
    dataclass_transform,
    get_args,
    get_origin,
    get_type_hints,
)

from cocotb.handle import HierarchyArrayObject, HierarchyObject, SimHandleBase

from cocotb_bfm.bus.resolvers import (
    CompositeResolver,
    InterfaceResolver,
    MappingResolver,
    PrefixResolver,
    RegexResolver,
    SignalResolver,
    SubBusResolution,
    format_not_found_error,
)
from cocotb_bfm.bus.spec import (
    MISSING,
    Role,
    SignalSpec,
    SubBusSpec,
    _SignalMarker,
    _SubBusMarker,
    signal,
    sub_bus,
)
from cocotb_bfm.bus.validation import handle_path, validate_handle
from cocotb_bfm.exceptions import (
    BusBindingError,
    BusDirectionError,
    SignalNotFoundError,
)


def _parse_annotation(annotation: Any) -> tuple[tuple[type[Any], ...], bool]:
    """Extract runtime-checkable handle/bus types and optionality from a type hint."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = get_args(annotation)
        optional = type(None) in args
        non_none = tuple(a for a in args if a is not type(None))
    else:
        optional = False
        non_none = (annotation,)

    resolved: list[type[Any]] = []
    for item in non_none:
        if item is Any or item is object:
            continue
        item_origin = get_origin(item)
        match item_origin if item_origin is not None else item:
            case type() as candidate:
                resolved.append(candidate)

    return tuple(resolved), optional


def _extract_sub_bus_type(expected_types: tuple[type[Any], ...]) -> type[Bus] | None:
    """Return the :class:`Bus` subclass when ``expected_types`` is a single sub-bus."""
    bus_types = [t for t in expected_types if issubclass(t, Bus)]
    if len(bus_types) == 1 and len(expected_types) == 1:
        return bus_types[0]
    return None


def _sub_bus_has_any_signal(
    bus_type: type[Bus],
    scope: Any,
    resolver: SignalResolver,
) -> bool:
    """Return ``True`` if at least one declared signal/sub-bus resolves in ``scope``."""
    for spec in bus_type._signal_specs.values():
        handle, _ = resolver.resolve_signal(scope, spec)
        if handle is not None:
            return True
    for sub_spec in bus_type._sub_bus_specs.values():
        res = resolver.resolve_sub_bus(scope, sub_spec)
        if res is not None and (
            res.dedicated_scope
            or _sub_bus_has_any_signal(sub_spec.bus_type, res.scope, res.resolver)
        ):
            return True
    return False


@dataclass_transform(
    eq_default=False,
    frozen_default=True,
    kw_only_default=False,
    field_specifiers=(signal, sub_bus),
)
class Bus:
    """Base class for statically typed, immutable cocotb 2.0 bus definitions.

    Declare signals as standard typed class attributes (optionally annotated with
    :func:`signal` or :func:`sub_bus` metadata). Attributes hold raw cocotb 2.0
    simulation handles directly, preserving full static type-checking and IDE
    autocompletion with zero plugins::

        class ValidReadyBus(Bus):
            valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
            data: LogicArrayObject = signal(direction=Direction.INITIATOR)
            ready: LogicObject | None = signal(default=None, direction=Direction.TARGET)

        bus = ValidReadyBus.from_interface(dut.rx_if)
        bus = ValidReadyBus.from_prefix(dut, "rx")
        bus = ValidReadyBus(valid=dut.rx_valid, data=dut.rx_data, ready=dut.rx_ready)
    """

    _field_order: ClassVar[tuple[str, ...]] = ()
    _signal_specs: ClassVar[dict[str, SignalSpec]] = {}
    _sub_bus_specs: ClassVar[dict[str, SubBusSpec]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        field_order: list[str] = []
        signal_specs: dict[str, SignalSpec] = {}
        sub_bus_specs: dict[str, SubBusSpec] = {}

        for base in reversed(cls.__mro__[1:]):
            if base is Bus or not issubclass(base, Bus):
                continue
            for name in base._field_order:
                if name not in field_order:
                    field_order.append(name)
            signal_specs.update(base._signal_specs)
            sub_bus_specs.update(base._sub_bus_specs)

        own_annotations = inspect.get_annotations(cls)
        if own_annotations:
            try:
                hints = get_type_hints(cls, include_extras=False)
            except Exception as exc:
                raise BusBindingError(
                    f"Failed to resolve type annotations on {cls.__name__}: {exc}"
                ) from exc

            for attr_name in own_annotations:
                if attr_name.startswith("_"):
                    continue
                hint = hints.get(attr_name, Any)
                if get_origin(hint) is ClassVar:
                    continue

                raw_default = cls.__dict__.get(attr_name, MISSING)
                expected_types, hint_optional = _parse_annotation(hint)
                sub_type = _extract_sub_bus_type(expected_types)

                match raw_default:
                    case _SubBusMarker() as marker:
                        if sub_type is None:
                            raise BusBindingError(
                                f"{cls.__name__}.{attr_name}: sub_bus() field must be "
                                "annotated with a Bus subclass (or Optional)."
                            )
                        has_default = marker.default is not MISSING
                        optional = hint_optional or marker.default is None
                        sub_bus_specs[attr_name] = SubBusSpec(
                            attr_name=attr_name,
                            bus_type=sub_type,
                            hdl_name=marker.hdl_name
                            if marker.hdl_name is not None
                            else attr_name,
                            optional=optional,
                            has_default=has_default,
                            default=marker.default,
                            separator=marker.separator,
                            aliases=marker.aliases,
                            description=marker.description,
                        )
                        signal_specs.pop(attr_name, None)
                        delattr(cls, attr_name)
                    case _SignalMarker() as sig_marker:
                        has_default = sig_marker.default is not MISSING
                        optional = hint_optional or sig_marker.default is None
                        signal_specs[attr_name] = SignalSpec(
                            attr_name=attr_name,
                            hdl_name=(
                                sig_marker.hdl_name
                                if sig_marker.hdl_name is not None
                                else attr_name
                            ),
                            expected_types=expected_types,
                            optional=optional,
                            has_default=has_default,
                            default=sig_marker.default,
                            aliases=sig_marker.aliases,
                            direction=sig_marker.direction,
                            width=sig_marker.width,
                            min_width=sig_marker.min_width,
                            max_width=sig_marker.max_width,
                            idle_value=sig_marker.idle_value,
                            description=sig_marker.description,
                        )
                        sub_bus_specs.pop(attr_name, None)
                        delattr(cls, attr_name)
                    case _ if sub_type is not None:
                        has_default = raw_default is not MISSING
                        optional = hint_optional or raw_default is None
                        sub_bus_specs[attr_name] = SubBusSpec(
                            attr_name=attr_name,
                            bus_type=sub_type,
                            hdl_name=attr_name,
                            optional=optional,
                            has_default=has_default,
                            default=raw_default,
                        )
                        signal_specs.pop(attr_name, None)
                        if has_default:
                            delattr(cls, attr_name)
                    case _:
                        has_default = raw_default is not MISSING
                        optional = hint_optional or raw_default is None
                        signal_specs[attr_name] = SignalSpec(
                            attr_name=attr_name,
                            hdl_name=attr_name,
                            expected_types=expected_types,
                            optional=optional,
                            has_default=has_default,
                            default=raw_default,
                        )
                        sub_bus_specs.pop(attr_name, None)
                        if has_default:
                            delattr(cls, attr_name)

                if attr_name not in field_order:
                    field_order.append(attr_name)

        # Enforce PEP 681 / dataclass ordering: required fields cannot follow defaulted
        # fields.
        seen_default_field: str | None = None
        for name in field_order:
            field_has_default = (
                signal_specs[name].has_default
                if name in signal_specs
                else sub_bus_specs[name].has_default
            )
            if field_has_default and seen_default_field is None:
                seen_default_field = name
            elif not field_has_default and seen_default_field is not None:
                raise BusBindingError(
                    f"{cls.__name__}.{name}: non-default field cannot follow "
                    f"defaulted field {seen_default_field!r} (PEP 681 dataclass rule)"
                )

        cls._field_order = tuple(field_order)
        cls._signal_specs = signal_specs
        cls._sub_bus_specs = sub_bus_specs

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Bind signal and sub-bus handles directly and validate types and widths."""
        strict_types = bool(kwargs.pop("_strict_types", False))
        field_order = self._field_order
        if len(args) > len(field_order):
            raise TypeError(
                f"{type(self).__name__}() takes at most {len(field_order)} positional "
                f"arguments but {len(args)} were given"
            )

        bound: dict[str, Any] = {}
        for name, val in zip(field_order, args, strict=False):
            bound[name] = val

        for name, val in kwargs.items():
            if name not in self._signal_specs and name not in self._sub_bus_specs:
                cls_name = type(self).__name__
                raise TypeError(
                    f"{cls_name}() got an unexpected keyword argument {name!r}"
                )
            if name in bound:
                raise TypeError(
                    f"{type(self).__name__}() got multiple values for argument {name!r}"
                )
            bound[name] = val

        for name in field_order:
            if name in self._signal_specs:
                spec = self._signal_specs[name]
                if name in bound:
                    value = bound[name]
                elif spec.has_default:
                    value = spec.default
                else:
                    cls_name = type(self).__name__
                    raise TypeError(
                        f"{cls_name}() missing required signal argument: {name!r}"
                    )
                validated = validate_handle(
                    type(self).__name__,
                    spec,
                    value,
                    strict_types=strict_types,
                )
                object.__setattr__(self, name, validated)
            else:
                sub_spec = self._sub_bus_specs[name]
                if name in bound:
                    sub_val = bound[name]
                elif sub_spec.has_default:
                    sub_val = sub_spec.default
                else:
                    cls_name = type(self).__name__
                    raise TypeError(
                        f"{cls_name}() missing required sub-bus argument: {name!r}"
                    )
                if sub_val is not None and not isinstance(sub_val, sub_spec.bus_type):
                    cls_name = type(self).__name__
                    exp_name = sub_spec.bus_type.__name__
                    raise BusBindingError(
                        f"{cls_name}.{name}: expected {exp_name}, "
                        f"got {type(sub_val).__name__}"
                    )
                object.__setattr__(self, name, sub_val)

    def __setattr__(self, name: str, value: Any) -> None:
        cls_name = type(self).__name__
        if name in self._signal_specs:
            raise AttributeError(
                f"Cannot reassign signal handle '{cls_name}.{name}'; "
                f"did you mean 'bus.{name}.value = {value!r}'?"
            )
        raise AttributeError(f"{cls_name} is immutable; cannot set attribute {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"{type(self).__name__} is immutable; cannot delete attribute {name!r}"
        )

    # ------------------------------------------------------------------
    # Class-level metadata & factory constructors
    # ------------------------------------------------------------------

    @classmethod
    def signal_specs(cls) -> Mapping[str, SignalSpec]:
        """Return a read-only view of declared signal specifications."""
        return types.MappingProxyType(cls._signal_specs)

    @classmethod
    def sub_bus_specs(cls) -> Mapping[str, SubBusSpec]:
        """Return a read-only view of declared nested sub-bus specifications."""
        return types.MappingProxyType(cls._sub_bus_specs)

    @classmethod
    def from_resolver(
        cls,
        scope: Any,
        resolver: SignalResolver,
        *,
        strict_types: bool = False,
    ) -> Self:
        """Bind a ``Bus`` instance from ``scope`` using any :class:`SignalResolver`."""
        kwargs: dict[str, Any] = {}

        for name in cls._field_order:
            if name in cls._signal_specs:
                spec = cls._signal_specs[name]
                handle, attempted = resolver.resolve_signal(scope, spec)
                if handle is None:
                    if spec.optional:
                        kwargs[name] = None
                    else:
                        raise format_not_found_error(
                            cls.__name__, name, scope, attempted
                        )
                else:
                    kwargs[name] = handle
            else:
                sub_spec = cls._sub_bus_specs[name]
                sub_res: SubBusResolution | None = resolver.resolve_sub_bus(
                    scope, sub_spec
                )
                if sub_res is None:
                    if sub_spec.optional:
                        kwargs[name] = None
                        continue
                    raise SignalNotFoundError(
                        f"Could not resolve required sub-bus {cls.__name__}.{name} "
                        f"({sub_spec.bus_type.__name__}) in scope "
                        f"'{handle_path(scope)}'."
                    )
                try:
                    kwargs[name] = sub_spec.bus_type.from_resolver(
                        sub_res.scope,
                        sub_res.resolver,
                        strict_types=strict_types,
                    )
                except SignalNotFoundError:
                    # Only collapse an optional sub-bus to None when NO dedicated
                    # child scope exists AND zero of its signals resolved; otherwise
                    # a partially wired/misspelled interface would fail silently!
                    if (
                        sub_spec.optional
                        and not sub_res.dedicated_scope
                        and not _sub_bus_has_any_signal(
                            sub_spec.bus_type, sub_res.scope, sub_res.resolver
                        )
                    ):
                        kwargs[name] = None
                    else:
                        raise

        return cls(**kwargs, _strict_types=strict_types)

    @classmethod
    def from_interface(
        cls,
        interface: HierarchyObject | Any,
        *,
        case_insensitive: bool = False,
        strict_types: bool = False,
        overrides: Mapping[str, Any] | None = None,
    ) -> Self:
        """Bind directly to a SystemVerilog ``interface`` or hierarchical scope."""
        base: SignalResolver = InterfaceResolver(case_insensitive=case_insensitive)
        resolver: SignalResolver = (
            CompositeResolver(
                [MappingResolver(overrides, case_insensitive=case_insensitive), base]
            )
            if overrides
            else base
        )
        return cls.from_resolver(interface, resolver, strict_types=strict_types)

    @classmethod
    def from_interface_array(
        cls,
        interface_array: HierarchyArrayObject[Any] | Any,
        *,
        case_insensitive: bool = False,
        strict_types: bool = False,
        overrides: Mapping[str, Any] | None = None,
    ) -> list[Self]:
        """Bind every element of an SV interface array in declaration order."""
        return [
            cls.from_interface(
                elem,
                case_insensitive=case_insensitive,
                strict_types=strict_types,
                overrides=overrides,
            )
            for elem in interface_array
        ]

    @classmethod
    def from_interface_map(
        cls,
        interface_array: HierarchyArrayObject[Any] | Any,
        *,
        case_insensitive: bool = False,
        strict_types: bool = False,
        overrides: Mapping[str, Any] | None = None,
    ) -> dict[int, Self]:
        """Bind an SV interface array into ``{hdl_index: Bus}`` preserving HDL bounds.

        Supports non-zero and negative SystemVerilog generate/interface ranges such as
        ``lanes_if[2:5]`` or ``lanes_if[-2:1]``.
        """
        rng = getattr(interface_array, "range", None)
        if rng is not None:
            indices: list[int] = list(rng)
            return {
                idx: cls.from_interface(
                    interface_array[idx],
                    case_insensitive=case_insensitive,
                    strict_types=strict_types,
                    overrides=overrides,
                )
                for idx in indices
            }
        return {
            idx: cls.from_interface(
                elem,
                case_insensitive=case_insensitive,
                strict_types=strict_types,
                overrides=overrides,
            )
            for idx, elem in enumerate(interface_array)
        }

    @classmethod
    def from_prefix(
        cls,
        entity: HierarchyObject | Any,
        prefix: str | None = None,
        *,
        separator: str = "_",
        suffix: str = "",
        case_insensitive: bool = False,
        strict_types: bool = False,
        overrides: Mapping[str, Any] | None = None,
    ) -> Self:
        """Bind flat ``<prefix><separator><signal><suffix>`` signals on ``entity``."""
        base: SignalResolver = PrefixResolver(
            prefix=prefix,
            separator=separator,
            suffix=suffix,
            case_insensitive=case_insensitive,
        )
        resolver: SignalResolver = (
            CompositeResolver(
                [MappingResolver(overrides, case_insensitive=case_insensitive), base]
            )
            if overrides
            else base
        )
        return cls.from_resolver(entity, resolver, strict_types=strict_types)

    @classmethod
    def from_entity(
        cls,
        entity: HierarchyObject | Any,
        *,
        case_insensitive: bool = False,
        strict_types: bool = False,
        overrides: Mapping[str, Any] | None = None,
    ) -> Self:
        """Bind directly to unprefixed signals on ``entity`` (``from_interface``)."""
        return cls.from_interface(
            entity,
            case_insensitive=case_insensitive,
            strict_types=strict_types,
            overrides=overrides,
        )

    @classmethod
    def from_mapping(
        cls,
        entity: HierarchyObject | Any,
        mapping: Mapping[str, Any],
        *,
        case_insensitive: bool = False,
        strict_types: bool = False,
    ) -> Self:
        """Bind signals via an explicit ``{attr_name: hdl_path_or_handle}`` map."""
        return cls.from_resolver(
            entity,
            MappingResolver(mapping, case_insensitive=case_insensitive),
            strict_types=strict_types,
        )

    @classmethod
    def from_regex(
        cls,
        entity: HierarchyObject | Any,
        pattern: str | re.Pattern[str],
        *,
        case_insensitive: bool = False,
        strict_types: bool = False,
        overrides: Mapping[str, Any] | None = None,
    ) -> Self:
        """Bind signals by matching handle names in ``entity`` against ``pattern``."""
        base: SignalResolver = RegexResolver(pattern, case_insensitive=case_insensitive)
        resolver: SignalResolver = (
            CompositeResolver(
                [MappingResolver(overrides, case_insensitive=case_insensitive), base]
            )
            if overrides
            else base
        )
        return cls.from_resolver(entity, resolver, strict_types=strict_types)

    # ------------------------------------------------------------------
    # Instance inspection & optional drive/sample helpers
    # ------------------------------------------------------------------

    def signals(self, *, recursive: bool = False) -> dict[str, Any]:
        """Return a dict mapping signal names to their bound non-``None`` handles."""
        result: dict[str, Any] = {}
        for name in self._field_order:
            match getattr(self, name):
                case None:
                    continue
                case Bus() as sub if recursive:
                    for sub_name, sub_handle in sub.signals(recursive=True).items():
                        result[f"{name}.{sub_name}"] = sub_handle
                case val if name in self._signal_specs:
                    result[name] = val
        return result

    def present_signals(self) -> tuple[str, ...]:
        """Return names of all direct signals that are bound (non-``None``)."""
        return tuple(
            name for name in self._signal_specs if getattr(self, name) is not None
        )

    def missing_optional_signals(self) -> tuple[str, ...]:
        """Return names of optional direct signals that were not present in RTL."""
        return tuple(
            name
            for name, spec in self._signal_specs.items()
            if spec.optional and getattr(self, name) is None
        )

    def sample(self, *, include_sub_buses: bool = True) -> dict[str, Any]:
        """Snapshot ``.value`` for all bound signals (and nested sub-buses)."""
        snapshot: dict[str, Any] = {}
        for name in self._field_order:
            match getattr(self, name):
                case None:
                    continue
                case Bus() as sub if include_sub_buses:
                    snapshot[name] = sub.sample(include_sub_buses=True)
                case val if name in self._signal_specs:
                    snapshot[name] = val.value
        return snapshot

    def drive(
        self,
        values: Mapping[str, Any] | object,
        *,
        role: Role | None = None,
        strict: bool = True,
        action: type[Any] | None = None,
    ) -> None:
        """Drive multiple bus signals from a mapping or attribute-bearing object."""
        match values:
            case Mapping():
                items = list(values.items())
            case _:
                items = [
                    (name, getattr(values, name))
                    for name in self._field_order
                    if hasattr(values, name)
                ]

        for name, raw_val in items:
            if name in self._sub_bus_specs:
                sub = getattr(self, name)
                if sub is None:
                    if strict:
                        cls_name = type(self).__name__
                        raise BusBindingError(
                            f"{cls_name}.{name}: optional sub-bus is not bound"
                        )
                    continue
                sub.drive(raw_val, role=role, strict=strict, action=action)
                continue

            if name not in self._signal_specs:
                if strict:
                    raise BusBindingError(
                        f"{type(self).__name__}: unknown signal {name!r} in drive()"
                    )
                continue

            spec = self._signal_specs[name]
            handle = getattr(self, name)
            if handle is None:
                if strict:
                    raise BusBindingError(
                        f"{type(self).__name__}.{name}: optional signal is not bound"
                    )
                continue

            if role is not None and not spec.direction.is_driven_by(role):
                cls_name = type(self).__name__
                raise BusDirectionError(
                    f"{cls_name}.{name} has direction {spec.direction.value!r} "
                    f"and cannot be driven by role {role.value!r}"
                )

            write_handle_value(handle, raw_val, action=action)

    def drive_idle(
        self,
        *,
        role: Role | None = None,
        action: type[Any] | None = None,
    ) -> None:
        """Drive all bound signals declaring an ``idle_value`` to idle."""
        for name in self._field_order:
            if name in self._sub_bus_specs:
                sub = getattr(self, name)
                if sub is not None:
                    sub.drive_idle(role=role, action=action)
                continue

            spec = self._signal_specs[name]
            if not spec.has_idle_value:
                continue
            handle = getattr(self, name)
            if handle is None:
                continue
            if role is not None and not spec.direction.is_driven_by(role):
                continue
            write_handle_value(handle, spec.idle_value, action=action)

    def __repr__(self) -> str:
        parts: list[str] = []
        for name in self._field_order:
            match getattr(self, name):
                case None:
                    parts.append(f"{name}=None")
                case Bus() as sub:
                    parts.append(f"{name}={sub!r}")
                case SimHandleBase() as handle:
                    parts.append(f"{name}={handle_path(handle)!r}")
                case val:
                    parts.append(f"{name}={handle_path(val)!r}")
        return f"{type(self).__name__}({', '.join(parts)})"


def write_handle_value(
    handle: Any, value: Any, *, action: type[Any] | None = None
) -> None:
    """Assign ``value`` to ``handle``, optionally using a cocotb 2.0 write action."""
    if action is not None:
        wrapped = action(value)
        set_fn = getattr(handle, "set", None)
        if callable(set_fn):
            set_fn(wrapped)
            return
        handle.value = wrapped
        return
    handle.value = value
