"""Lightweight typed fake cocotb 2.0 handles for fast, simulator-free unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from cocotb.handle import (
    HierarchyArrayObject,
    HierarchyObject,
    LogicArrayObject,
    LogicObject,
)
from cocotb.types import Logic, LogicArray


class FakeLogicObject(LogicObject):  # type: ignore[misc]
    """Minimal in-memory ``LogicObject`` subclass for unit testing."""

    def __init__(
        self, name: str, value: int | str = 0, *, path: str | None = None
    ) -> None:
        self._name = name
        self._path = path or name
        self._type = "GPI_LOGIC"
        self._val: Logic = Logic(value)
        self.last_action: Any = None

    @property
    def value(self) -> Logic:  # type: ignore[override]
        return self._val

    @value.setter
    def value(self, val: Any) -> None:  # type: ignore[override]
        self._val = Logic(val)

    def set(self, action: Any) -> None:  # type: ignore[override]
        self.last_action = action
        inner = getattr(action, "value", action)
        self._val = Logic(inner)


class FakeLogicArrayObject(LogicArrayObject):  # type: ignore[misc]
    """Minimal in-memory ``LogicArrayObject`` subclass for unit testing."""

    def __init__(
        self,
        name: str,
        width: int,
        value: int | str = 0,
        *,
        path: str | None = None,
    ) -> None:
        self._name = name
        self._path = path or name
        self._type = "GPI_LOGIC_ARRAY"
        self._width = width
        self._val: LogicArray = LogicArray(value, width)
        self.last_action: Any = None

    def __len__(self) -> int:
        return self._width

    @property
    def value(self) -> LogicArray:  # type: ignore[override]
        return self._val

    @value.setter
    def value(self, val: Any) -> None:  # type: ignore[override]
        self._val = LogicArray(val, self._width)

    def set(self, action: Any) -> None:  # type: ignore[override]
        self.last_action = action
        inner = getattr(action, "value", action)
        self._val = LogicArray(inner, self._width)


class FakeHierarchyObject(HierarchyObject):  # type: ignore[misc]
    """Minimal in-memory ``HierarchyObject`` mimicking a module or SV ``interface``."""

    def __init__(
        self,
        name: str,
        children: dict[str, Any] | None = None,
        *,
        path: str | None = None,
    ) -> None:
        self._name = name
        self._path = path or name
        self._type = "GPI_MODULE"
        self._children: dict[str, Any] = dict(children or {})

    def _keys(self) -> list[str]:  # type: ignore[override]
        return list(self._children.keys())

    def __getitem__(self, key: str) -> Any:  # type: ignore[override]
        return self._children[key]

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        if item in self._children:
            return self._children[item]
        raise AttributeError(f"{self._path!r} has no attribute {item!r}")


class FakeHierarchyArrayObject(HierarchyArrayObject[Any]):  # type: ignore[misc]
    """Minimal in-memory ``HierarchyArrayObject`` mimicking an SV interface array."""

    def __init__(
        self,
        name: str,
        elements: list[FakeHierarchyObject],
        *,
        path: str | None = None,
    ) -> None:
        self._name = name
        self._path = path or name
        self._type = "GPI_GENARRAY"
        self._elements = list(elements)

    def __len__(self) -> int:
        return len(self._elements)

    def __getitem__(self, idx: int) -> Any:  # type: ignore[override]
        return self._elements[idx]

    def __iter__(self) -> Iterator[Any]:  # type: ignore[override]
        return iter(self._elements)
