"""Rich human-readable transaction and cocotb 2.0 ``LogicArray`` diff formatting."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from cocotb.types import Logic, LogicArray


def format_value(value: Any) -> str:
    """Format a transaction or cocotb 2.0 logic value concisely for mismatch reports."""
    match value:
        case LogicArray():
            bitstr = str(value)
            if value.is_resolvable:
                hex_width = max(1, (len(value) + 3) // 4)
                return f"0x{value.to_unsigned():0{hex_width}x} (0b{bitstr})"
            return f"LogicArray('{bitstr}') [unresolvable]"
        case Logic():
            return f"Logic('{value}')"
        case bytes() | bytearray():
            hex_str = value.hex(" ")
            return f"bytes[{len(value)}]({hex_str})"
        case bool():
            return repr(value)
        case int():
            return (
                f"{value} (0x{value:x})" if value >= 16 or value <= -16 else str(value)
            )
        case _:
            return repr(value)


def format_diff(expected: Any, actual: Any) -> str:
    """Produce a structured field-level diff between ``expected`` and ``actual``."""
    if (
        dataclasses.is_dataclass(expected)
        and dataclasses.is_dataclass(actual)
        and type(expected) is type(actual)
    ):
        diffs: list[str] = []
        for f in dataclasses.fields(expected):
            exp_val = getattr(expected, f.name)
            act_val = getattr(actual, f.name)
            if exp_val != act_val:
                exp_s = format_value(exp_val)
                act_s = format_value(act_val)
                diffs.append(f"  .{f.name}: expected {exp_s}, got {act_s}")
        if diffs:
            return f"{type(expected).__name__} field mismatch:\n" + "\n".join(diffs)

    match (expected, actual):
        case (Mapping() as exp_map, Mapping() as act_map):
            all_keys = list(dict.fromkeys([*exp_map.keys(), *act_map.keys()]))
            lines: list[str] = []
            for key in all_keys:
                if key not in act_map:
                    lines.append(
                        f"  [{key!r}]: missing (expected {format_value(exp_map[key])})"
                    )
                elif key not in exp_map:
                    act_s = format_value(act_map[key])
                    lines.append(f"  [{key!r}]: unexpected key (got {act_s})")
                elif exp_map[key] != act_map[key]:
                    lines.append(
                        f"  [{key!r}]: expected {format_value(exp_map[key])}, "
                        f"got {format_value(act_map[key])}"
                    )
            if lines:
                return "Mapping mismatch:\n" + "\n".join(lines)
        case _:
            pass

    return f"expected {format_value(expected)}, got {format_value(actual)}"
