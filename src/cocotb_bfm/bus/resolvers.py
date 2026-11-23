"""Pluggable signal resolvers for flat buses, SystemVerilog interfaces, and custom maps."""

from __future__ import annotations

import difflib
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from cocotb.handle import HierarchyObject, SimHandleBase

from cocotb_bfm.bus.spec import SignalSpec, SubBusSpec
from cocotb_bfm.bus.validation import handle_path
from cocotb_bfm.exceptions import SignalNotFoundError

_INDEX_PART_RE = re.compile(r"^(?P<name>[^\[]+)(?P<indices>(?:\[\d+\])+)$")
_SINGLE_INDEX_RE = re.compile(r"\[(\d+)\]")


def list_scope_keys(scope: Any) -> list[str]:
    """Return child object names exposed by a cocotb 2.0 scope handle.

    Uses ``HierarchyObject._keys()`` when available (standard cocotb 2.0 API),
    falling back to dictionary keys for test mocks.
    """
    keys_fn = getattr(scope, "_keys", None)
    if callable(keys_fn):
        try:
            return [str(k) for k in keys_fn()]
        except Exception:
            pass
    match scope:
        case Mapping():
            return [str(k) for k in scope]
        case _:
            return []


def lookup_child(
    scope: Any,
    name: str,
    *,
    case_insensitive: bool = False,
) -> Any | None:
    """Look up an immediate child handle inside ``scope`` without Python keyword collisions.

    Prefers cocotb 2.0's bracket lookup (``scope[name]``) so HDL names such as ``in``,
    ``from``, or ``class`` never collide with Python attributes.
    """
    if not name:
        return None

    # 1. Exact bracket lookup (cocotb 2.0 HierarchyObject.__getitem__ or Mapping).
    try:
        return scope[name]
    except (KeyError, IndexError, TypeError, AttributeError):
        pass

    # 2. Exact attribute fallback (for handles/mocks without __getitem__).
    try:
        if not name.startswith("_") and hasattr(scope, name):
            val = getattr(scope, name)
            if not callable(val):
                return val
    except AttributeError:
        pass

    # 3. Case-insensitive fallback via scope key discovery.
    if case_insensitive:
        target = name.lower()
        for key in list_scope_keys(scope):
            if key.lower() == target:
                try:
                    return scope[key]
                except (KeyError, IndexError, TypeError):
                    return getattr(scope, key, None)

    return None


def resolve_path(
    scope: Any,
    path: str,
    *,
    case_insensitive: bool = False,
) -> Any | None:
    """Resolve a possibly dotted/indexed HDL path such as ``'sub_if.lanes[0].valid'``."""
    current: Any = scope
    for segment in path.split("."):
        match = _INDEX_PART_RE.match(segment)
        if match:
            base_name = match.group("name")
            current = lookup_child(current, base_name, case_insensitive=case_insensitive)
            if current is None:
                return None
            for idx_str in _SINGLE_INDEX_RE.findall(match.group("indices")):
                try:
                    current = current[int(idx_str)]
                except (KeyError, IndexError, TypeError):
                    return None
        else:
            current = lookup_child(current, segment, case_insensitive=case_insensitive)
            if current is None:
                return None
    return current


def format_not_found_error(
    bus_cls_name: str,
    attr_name: str,
    scope: Any,
    attempted: Sequence[str],
) -> SignalNotFoundError:
    """Build an actionable :class:`SignalNotFoundError` with fuzzy suggestions."""
    available = list_scope_keys(scope)
    scope_desc = handle_path(scope)
    tried_str = ", ".join(repr(c) for c in attempted) if attempted else "<none>"

    lines = [
        f"Could not resolve required signal {bus_cls_name}.{attr_name} "
        f"in scope '{scope_desc}' (tried: {tried_str})."
    ]

    if available:
        suggestions: list[str] = []
        for cand in attempted:
            for m in difflib.get_close_matches(cand, available, n=3, cutoff=0.5):
                if m not in suggestions:
                    suggestions.append(m)
            # Also try case-insensitive fuzzy match on tail segment.
            lower_map = {k.lower(): k for k in available}
            for lm in difflib.get_close_matches(cand.lower(), lower_map.keys(), n=2, cutoff=0.5):
                orig = lower_map[lm]
                if orig not in suggestions:
                    suggestions.append(orig)
        if suggestions:
            lines.append(f"Did you mean: {', '.join(repr(s) for s in suggestions[:5])}?")
        preview = available[:16]
        suffix = f" ... (+{len(available) - len(preview)} more)" if len(available) > 16 else ""
        lines.append(f"Available handles in '{scope_desc}': {preview}{suffix}")
    else:
        lines.append(
            f"No child handles were discovered in '{scope_desc}'. "
            "If this is a SystemVerilog interface under Verilator, ensure interface signals "
            "are exposed to VPI (e.g. Verilator 5+ with --public-flat-rw / --trace-structs "
            "or non-optimized interface connections)."
        )

    return SignalNotFoundError("\n".join(lines))


class SignalResolver(ABC):
    """Abstract strategy that resolves declared bus signals and nested sub-buses."""

    @abstractmethod
    def resolve_signal(
        self,
        scope: Any,
        spec: SignalSpec,
    ) -> tuple[Any | None, tuple[str, ...]]:
        """Attempt to resolve ``spec`` in ``scope``.

        Returns:
            A ``(handle_or_none, attempted_names)`` tuple.
        """

    def resolve_sub_bus(
        self,
        scope: Any,
        spec: SubBusSpec,
    ) -> tuple[Any, SignalResolver] | None:
        """Return ``(child_scope, child_resolver)`` for a nested sub-bus, or ``None``.

        Subclasses override this to support nested scopes (e.g., SystemVerilog sub-interfaces)
        or composed prefixes.
        """
        return None


class InterfaceResolver(SignalResolver):
    """Resolve signals directly inside a SystemVerilog ``interface`` or scope handle.

    For nested sub-buses, first looks for a child scope handle (e.g., ``dut.axi_if.aw``).
    If no child scope exists, falls back cleanly to prefixed members inside the same
    interface (e.g., ``dut.axi_if.awvalid`` or ``dut.axi_if.aw_valid``).
    """

    def __init__(
        self,
        *,
        case_insensitive: bool = False,
        flat_sub_bus_separators: Sequence[str] = ("_", ""),
    ) -> None:
        self.case_insensitive = case_insensitive
        self.flat_sub_bus_separators = tuple(flat_sub_bus_separators)

    def resolve_signal(
        self,
        scope: Any,
        spec: SignalSpec,
    ) -> tuple[Any | None, tuple[str, ...]]:
        attempted: list[str] = []
        for candidate in spec.candidate_names:
            attempted.append(candidate)
            handle = resolve_path(scope, candidate, case_insensitive=self.case_insensitive)
            if handle is not None:
                return handle, tuple(attempted)
        return None, tuple(attempted)

    def resolve_sub_bus(
        self,
        scope: Any,
        spec: SubBusSpec,
    ) -> tuple[Any, SignalResolver] | None:
        # 1. Try nested interface / hierarchy scope (e.g., axi_if.aw -> HierarchyObject).
        for candidate in spec.candidate_names:
            child = lookup_child(scope, candidate, case_insensitive=self.case_insensitive)
            if child is not None and _is_scope_like(child):
                return child, self

        # 2. Fallback: flat prefixed signals within the same interface
        #    (e.g., axi_if.aw_valid or axi_if.awvalid).
        seps = (spec.separator,) if spec.separator is not None else self.flat_sub_bus_separators
        resolvers: list[SignalResolver] = [
            PrefixResolver(
                prefix=candidate,
                separator=sep,
                case_insensitive=self.case_insensitive,
            )
            for candidate in spec.candidate_names
            for sep in seps
        ]
        return scope, CompositeResolver(resolvers)


class PrefixResolver(SignalResolver):
    """Resolve signals using ``<prefix><separator><signal><suffix>`` naming."""

    def __init__(
        self,
        prefix: str | None = None,
        *,
        separator: str = "_",
        suffix: str = "",
        case_insensitive: bool = False,
    ) -> None:
        self.prefix = prefix
        self.separator = separator
        self.suffix = suffix
        self.case_insensitive = case_insensitive

    def _build_name(self, signal_name: str) -> str:
        if self.prefix:
            return f"{self.prefix}{self.separator}{signal_name}{self.suffix}"
        return f"{signal_name}{self.suffix}"

    def resolve_signal(
        self,
        scope: Any,
        spec: SignalSpec,
    ) -> tuple[Any | None, tuple[str, ...]]:
        attempted: list[str] = []
        for candidate in spec.candidate_names:
            full_name = self._build_name(candidate)
            attempted.append(full_name)
            handle = resolve_path(scope, full_name, case_insensitive=self.case_insensitive)
            if handle is not None:
                return handle, tuple(attempted)
        return None, tuple(attempted)

    def resolve_sub_bus(
        self,
        scope: Any,
        spec: SubBusSpec,
    ) -> tuple[Any, SignalResolver] | None:
        sep = self.separator if spec.separator is None else spec.separator
        resolvers: list[SignalResolver] = []
        for candidate in spec.candidate_names:
            child_prefix = f"{self.prefix}{self.separator}{candidate}" if self.prefix else candidate
            # Try with explicit separator first, then direct concatenation (e.g., m_axi_awvalid)
            # when the sub-bus did not lock a custom separator.
            resolvers.append(
                PrefixResolver(
                    prefix=child_prefix,
                    separator=sep,
                    suffix=self.suffix,
                    case_insensitive=self.case_insensitive,
                )
            )
            if spec.separator is None and sep != "":
                resolvers.append(
                    PrefixResolver(
                        prefix=child_prefix,
                        separator="",
                        suffix=self.suffix,
                        case_insensitive=self.case_insensitive,
                    )
                )
        return scope, CompositeResolver(resolvers)


class MappingResolver(SignalResolver):
    """Resolve signals via an explicit map of ``attr_name -> hdl_path_or_handle``.

    Supports:
    - Direct pre-resolved handles (any ``SimHandleBase`` or non-string object).
    - String HDL paths (including dotted/indexed paths like ``"rx_if.lanes[0].data"``).
    - Nested dicts for sub-buses (e.g., ``{"aw": {"valid": "aw_vld", ...}}``).
    """

    def __init__(
        self,
        mapping: Mapping[str, Any],
        *,
        case_insensitive: bool = False,
    ) -> None:
        self.mapping = dict(mapping)
        self.case_insensitive = case_insensitive

    def resolve_signal(
        self,
        scope: Any,
        spec: SignalSpec,
    ) -> tuple[Any | None, tuple[str, ...]]:
        if spec.attr_name not in self.mapping:
            return None, ()
        match self.mapping[spec.attr_name]:
            case None:
                return None, ()
            case str() as path:
                handle = resolve_path(scope, path, case_insensitive=self.case_insensitive)
                return handle, (path,)
            case target:
                return target, (f"<direct:{spec.attr_name}>",)

    def resolve_sub_bus(
        self,
        scope: Any,
        spec: SubBusSpec,
    ) -> tuple[Any, SignalResolver] | None:
        if spec.attr_name not in self.mapping:
            return None
        match self.mapping[spec.attr_name]:
            case Mapping() as nested_map:
                return scope, MappingResolver(nested_map, case_insensitive=self.case_insensitive)
            case str() as path:
                child = resolve_path(scope, path, case_insensitive=self.case_insensitive)
                if child is not None and _is_scope_like(child):
                    return child, InterfaceResolver(case_insensitive=self.case_insensitive)
                return scope, PrefixResolver(
                    prefix=path,
                    case_insensitive=self.case_insensitive,
                )
            case target if _is_scope_like(target):
                return target, InterfaceResolver(case_insensitive=self.case_insensitive)
            case _:
                return None


class RegexResolver(SignalResolver):
    """Resolve signals by matching scope keys against a regular expression.

    The regex must contain either a named group ``(?P<signal>...)`` or at least one
    capturing group whose extracted value is matched against each signal's candidate names.
    """

    def __init__(
        self,
        pattern: str | re.Pattern[str],
        *,
        case_insensitive: bool = False,
    ) -> None:
        flags = re.IGNORECASE if case_insensitive else 0
        match pattern:
            case str():
                self.pattern = re.compile(pattern, flags)
            case _:
                self.pattern = pattern
        self.case_insensitive = case_insensitive

    def _build_index(self, scope: Any) -> dict[str, str]:
        index: dict[str, str] = {}
        for key in list_scope_keys(scope):
            match = self.pattern.fullmatch(key)
            if match is None:
                continue
            group_dict = match.groupdict()
            if "signal" in group_dict and group_dict["signal"] is not None:
                sig_name = group_dict["signal"]
            elif match.groups():
                sig_name = match.group(1)
            else:
                continue
            norm = sig_name.lower() if self.case_insensitive else sig_name
            index.setdefault(norm, key)
        return index

    def resolve_signal(
        self,
        scope: Any,
        spec: SignalSpec,
    ) -> tuple[Any | None, tuple[str, ...]]:
        index = self._build_index(scope)
        attempted: list[str] = []
        for candidate in spec.candidate_names:
            norm = candidate.lower() if self.case_insensitive else candidate
            attempted.append(f"/{self.pattern.pattern}/ -> {candidate}")
            matched_key = index.get(norm)
            if matched_key is not None:
                handle = lookup_child(scope, matched_key, case_insensitive=self.case_insensitive)
                if handle is not None:
                    return handle, tuple(attempted)
        return None, tuple(attempted)


class CompositeResolver(SignalResolver):
    """Try multiple resolvers in order, returning the first match."""

    def __init__(self, resolvers: Sequence[SignalResolver]) -> None:
        self.resolvers = tuple(resolvers)

    def resolve_signal(
        self,
        scope: Any,
        spec: SignalSpec,
    ) -> tuple[Any | None, tuple[str, ...]]:
        attempted: list[str] = []
        for resolver in self.resolvers:
            handle, tried = resolver.resolve_signal(scope, spec)
            attempted.extend(tried)
            if handle is not None:
                return handle, tuple(attempted)
        return None, tuple(attempted)

    def resolve_sub_bus(
        self,
        scope: Any,
        spec: SubBusSpec,
    ) -> tuple[Any, SignalResolver] | None:
        for resolver in self.resolvers:
            result = resolver.resolve_sub_bus(scope, spec)
            if result is not None:
                return result
        return None


def _is_scope_like(obj: Any) -> bool:
    """Return ``True`` if ``obj`` looks like a cocotb hierarchy scope or mock scope."""
    match obj:
        case HierarchyObject() | Mapping():
            return True
        case SimHandleBase():
            return callable(getattr(obj, "_keys", None))
        case _:
            return callable(getattr(obj, "_keys", None))
