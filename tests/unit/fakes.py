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
from cocotb.types import Logic, LogicArray, Range


class FakeLogicObject(LogicObject):
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
    def value(self) -> Logic:
        return self._val

    @value.setter
    def value(self, val: Any) -> None:
        self._val = Logic(val)

    def set(self, action: Any) -> None:
        self.last_action = action
        inner = getattr(action, "value", action)
        self._val = Logic(inner)


class FakeLogicArrayObject(LogicArrayObject):
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
    def value(self) -> LogicArray:
        return self._val

    @value.setter
    def value(self, val: Any) -> None:
        self._val = LogicArray(val, self._width)

    def set(self, action: Any) -> None:
        self.last_action = action
        inner = getattr(action, "value", action)
        self._val = LogicArray(inner, self._width)


class FakeHierarchyObject(HierarchyObject):
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

    def _keys(self) -> list[str]:
        return list(self._children.keys())

    def __getitem__(self, key: str) -> Any:
        return self._children[key]

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        if item in self._children:
            return self._children[item]
        raise AttributeError(f"{self._path!r} has no attribute {item!r}")


class FakeHierarchyArrayObject(HierarchyArrayObject[Any]):
    """Minimal in-memory ``HierarchyArrayObject`` mimicking an SV interface array."""

    def __init__(
        self,
        name: str,
        elements: list[FakeHierarchyObject] | dict[int, FakeHierarchyObject],
        *,
        path: str | None = None,
        hdl_range: Range | None = None,
    ) -> None:
        self._name = name
        self._path = path or name
        self._type = "GPI_GENARRAY"
        match elements:
            case dict():
                self._by_idx = dict(elements)
            case _:
                self._by_idx = dict(enumerate(elements))
        self._range = hdl_range

    @property
    def range(self) -> Range | None:  # type: ignore[override]
        return self._range

    def __len__(self) -> int:
        return len(self._by_idx)

    def __getitem__(self, idx: int) -> Any:
        return self._by_idx[idx]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._by_idx.values())
