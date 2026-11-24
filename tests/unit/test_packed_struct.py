"""Unit tests for :class:`PackedStruct` and :func:`bitfield`."""

from __future__ import annotations

import pytest
from cocotb.types import LogicArray

from cocotb_bfm import PackedStruct, SignalWidthError, bitfield
from tests.unit.fakes import FakeHierarchyObject, FakeLogicArrayObject, FakeLogicObject


class SampleMeta(PackedStruct):
    tag: int = bitfield(4)
    offset: int = bitfield(4, signed=True)
    enable: int = bitfield(1, default=1)


def test_packed_struct_msb_lsb_layout_and_signed_fields() -> None:
    assert SampleMeta.total_width() == 9
    fields = SampleMeta.bitfields()
    assert [(f.name, f.msb, f.lsb) for f in fields] == [
        ("tag", 8, 5),
        ("offset", 4, 1),
        ("enable", 0, 0),
    ]

    val = SampleMeta(tag=0xA, offset=-3)
    assert val.enable == 1
    # 0b1010_1101_1 = 0x15B
    assert val.to_int() == 0x15B
    arr = val.to_logic_array()
    assert arr == LogicArray("101011011")

    unpacked = SampleMeta.unpack(arr)
    assert unpacked == val
    assert unpacked.tag == 0xA
    assert unpacked.offset == -3
    assert unpacked.enable == 1


def test_packed_struct_range_and_width_validation() -> None:
    with pytest.raises(TypeError, match="missing required bitfield argument: 'offset'"):
        SampleMeta(tag=1)  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="out of range"):
        SampleMeta(tag=16, offset=0)

    with pytest.raises(ValueError, match="out of range"):
        SampleMeta(tag=0, offset=-9)

    with pytest.raises(SignalWidthError, match="expected 9-bit LogicArray, got 8 bits"):
        SampleMeta.unpack(LogicArray("10101010"))


def test_packed_struct_from_handle_flat_and_hierarchical() -> None:
    # Flat vector handle (Verilator PackedObject style)
    flat = FakeLogicArrayObject("meta", width=9)
    meta = SampleMeta(tag=5, offset=-2, enable=0)
    meta.write_handle(flat)
    assert SampleMeta.from_handle(flat) == meta

    # Width mismatch on handle fails loudly
    wrong_width = FakeLogicArrayObject("bad_meta", width=8)
    with pytest.raises(SignalWidthError, match="expected width 9, got 8"):
        meta.write_handle(wrong_width)

    # Hierarchical struct handle (commercial simulator scope style)
    hier = FakeHierarchyObject(
        "meta",
        {
            "tag": FakeLogicArrayObject("tag", width=4),
            "offset": FakeLogicArrayObject("offset", width=4),
            "enable": FakeLogicObject("enable"),
        },
    )
    meta2 = SampleMeta(tag=7, offset=3, enable=1)
    meta2.write_handle(hier)
    assert SampleMeta.from_handle(hier) == meta2
