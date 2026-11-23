"""Unit tests for typed ``Bus`` definitions, resolvers, validation, and helpers."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

import pytest
from cocotb.handle import Immediate, LogicArrayObject, LogicObject

from cocotb_bfm import (
    Bus,
    BusBindingError,
    BusDirectionError,
    Direction,
    Role,
    SignalNotFoundError,
    SignalTypeError,
    SignalWidthError,
    as_legacy_bus,
    signal,
    sub_bus,
)
from tests.unit.fakes import (
    FakeHierarchyArrayObject,
    FakeHierarchyObject,
    FakeLogicArrayObject,
    FakeLogicObject,
)


class StreamBus(Bus):
    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    data: LogicArrayObject = signal(direction=Direction.INITIATOR, min_width=8, max_width=64)
    ready: LogicObject | None = signal(default=None, direction=Direction.TARGET, idle_value=1)
    last: LogicObject | None = signal(default=None, aliases=("tlast", "eop"), idle_value=0)


class ExtendedStreamBus(StreamBus):
    tid: LogicArrayObject | None = signal(default=None, width=4)


class DualBus(Bus):
    tx: StreamBus = sub_bus(hdl_name="tx")
    rx: StreamBus | None = sub_bus(default=None, hdl_name="rx")


class KeywordSignalBus(Bus):
    in_sig: LogicObject = signal(hdl_name="in")
    from_sig: LogicObject = signal(hdl_name="from")


def _make_stream_scope(
    prefix: str = "",
    sep: str = "_",
    *,
    with_ready: bool = True,
) -> FakeHierarchyObject:
    p = f"{prefix}{sep}" if prefix else ""
    children: dict[str, object] = {
        f"{p}valid": FakeLogicObject(f"{p}valid"),
        f"{p}data": FakeLogicArrayObject(f"{p}data", width=16),
    }
    if with_ready:
        children[f"{p}ready"] = FakeLogicObject(f"{p}ready")
    return FakeHierarchyObject("dut", children)


def test_direct_constructor_and_optional_defaults() -> None:
    v = FakeLogicObject("valid")
    d = FakeLogicArrayObject("data", width=8)
    bus = StreamBus(valid=v, data=d)

    assert bus.valid is v
    assert bus.data is d
    assert bus.ready is None
    assert bus.last is None
    assert bus.present_signals() == ("valid", "data")
    assert bus.missing_optional_signals() == ("ready", "last")


def test_direct_constructor_rejects_missing_or_extra_args() -> None:
    v = FakeLogicObject("valid")
    with pytest.raises(TypeError, match="missing required signal argument: 'data'"):
        StreamBus(valid=v)  # type: ignore[call-arg]

    d = FakeLogicArrayObject("data", width=8)
    with pytest.raises(TypeError, match="unexpected keyword argument 'bogus'"):
        StreamBus(valid=v, data=d, bogus=v)  # type: ignore[call-arg]


def test_from_interface_and_aliases_and_overrides() -> None:
    sv_if = FakeHierarchyObject(
        "rx_if",
        {
            "valid": FakeLogicObject("valid", path="dut.rx_if.valid"),
            "data": FakeLogicArrayObject("data", width=32, path="dut.rx_if.data"),
            "tlast": FakeLogicObject("tlast", path="dut.rx_if.tlast"),
            "alt_rdy": FakeLogicObject("alt_rdy", path="dut.rx_if.alt_rdy"),
        },
        path="dut.rx_if",
    )

    bus = StreamBus.from_interface(sv_if, overrides={"ready": "alt_rdy"})
    assert bus.valid is sv_if["valid"]
    assert bus.data is sv_if["data"]
    assert bus.last is sv_if["tlast"]
    assert bus.ready is sv_if["alt_rdy"]


def test_from_interface_handles_python_keyword_hdl_names() -> None:
    sv_if = FakeHierarchyObject(
        "kw_if",
        {
            "in": FakeLogicObject("in"),
            "from": FakeLogicObject("from"),
        },
    )
    bus = KeywordSignalBus.from_interface(sv_if)
    assert bus.in_sig is sv_if["in"]
    assert bus.from_sig is sv_if["from"]


def test_from_interface_array() -> None:
    lanes = FakeHierarchyArrayObject(
        "lanes_if",
        [
            FakeHierarchyObject(
                f"lanes_if[{i}]",
                {
                    "valid": FakeLogicObject("valid", path=f"dut.lanes_if[{i}].valid"),
                    "data": FakeLogicArrayObject("data", 8, path=f"dut.lanes_if[{i}].data"),
                },
                path=f"dut.lanes_if[{i}]",
            )
            for i in range(3)
        ],
    )
    buses = StreamBus.from_interface_array(lanes)
    assert len(buses) == 3
    for i, bus in enumerate(buses):
        assert bus.valid is lanes[i]["valid"]
        assert bus.data is lanes[i]["data"]


def test_from_prefix_and_suffix_and_case_insensitive() -> None:
    dut = FakeHierarchyObject(
        "dut",
        {
            "S_AXIS_VALID_I": FakeLogicObject("S_AXIS_VALID_I"),
            "S_AXIS_DATA_I": FakeLogicArrayObject("S_AXIS_DATA_I", 16),
            "S_AXIS_READY_I": FakeLogicObject("S_AXIS_READY_I"),
        },
    )
    bus = StreamBus.from_prefix(dut, "s_axis", suffix="_i", case_insensitive=True)
    assert bus.valid is dut["S_AXIS_VALID_I"]
    assert bus.data is dut["S_AXIS_DATA_I"]
    assert bus.ready is dut["S_AXIS_READY_I"]


def test_from_mapping_with_dotted_and_indexed_paths() -> None:
    lane0 = FakeHierarchyObject(
        "lane0",
        {"vld": FakeLogicObject("vld"), "dat": FakeLogicArrayObject("dat", 8)},
    )
    dut = FakeHierarchyObject("dut", {"lanes": FakeHierarchyArrayObject("lanes", [lane0])})
    direct_rdy = FakeLogicObject("external_rdy")

    bus = StreamBus.from_mapping(
        dut,
        {
            "valid": "lanes[0].vld",
            "data": "lanes[0].dat",
            "ready": direct_rdy,
        },
    )
    assert bus.valid is lane0["vld"]
    assert bus.data is lane0["dat"]
    assert bus.ready is direct_rdy


def test_from_regex() -> None:
    dut = FakeHierarchyObject(
        "dut",
        {
            "u0_ch3_valid_q": FakeLogicObject("u0_ch3_valid_q"),
            "u0_ch3_data_q": FakeLogicArrayObject("u0_ch3_data_q", 8),
        },
    )
    bus = StreamBus.from_regex(dut, r"u0_ch3_(?P<signal>[a-z]+)_q")
    assert bus.valid is dut["u0_ch3_valid_q"]
    assert bus.data is dut["u0_ch3_data_q"]
    assert bus.ready is None


def test_nested_sub_bus_on_nested_sv_interface_and_flat_fallback() -> None:
    # Nested SV interface
    nested = FakeHierarchyObject(
        "top_if",
        {
            "tx": _make_stream_scope(),
            "rx": _make_stream_scope(with_ready=False),
        },
    )
    bus = DualBus.from_interface(nested)
    assert bus.tx.valid is nested["tx"]["valid"]
    assert bus.rx is not None
    assert bus.rx.data is nested["rx"]["data"]
    assert bus.rx.ready is None

    # Flat signals inside a single interface (optional rx omitted)
    flat_if = FakeHierarchyObject(
        "flat_if",
        {
            "tx_valid": FakeLogicObject("tx_valid"),
            "tx_data": FakeLogicArrayObject("tx_data", 8),
        },
    )
    flat_bus = DualBus.from_interface(flat_if)
    assert flat_bus.tx.valid is flat_if["tx_valid"]
    assert flat_bus.rx is None


def test_1bit_vector_tolerance_and_strict_mode() -> None:
    onebit = FakeLogicArrayObject("valid_vec", width=1)
    eightbit = FakeLogicArrayObject("data", width=8)

    # Default (strict_types=False): 1-bit LogicArrayObject accepted on LogicObject field.
    bus = StreamBus(valid=onebit, data=eightbit)
    assert bus.valid is onebit

    # Multi-bit vector rejected on LogicObject field even in non-strict mode.
    with pytest.raises(SignalTypeError, match="expected LogicObject, got FakeLogicArrayObject"):
        StreamBus(valid=eightbit, data=eightbit)

    # strict_types=True rejects 1-bit LogicArrayObject with helpful hint.
    scope = FakeHierarchyObject("dut", {"valid": onebit, "data": eightbit})
    with pytest.raises(SignalTypeError, match="strict_types=False"):
        StreamBus.from_interface(scope, strict_types=True)


def test_width_validation_and_inheritance() -> None:
    v = FakeLogicObject("valid")
    too_narrow = FakeLogicArrayObject("data", width=4)
    with pytest.raises(SignalWidthError, match="expected width >= 8, got 4"):
        StreamBus(valid=v, data=too_narrow)

    good_data = FakeLogicArrayObject("data", width=8)
    bad_tid = FakeLogicArrayObject("tid", width=5)
    with pytest.raises(SignalWidthError, match="expected width 4, got 5"):
        ExtendedStreamBus(valid=v, data=good_data, tid=bad_tid)

    good_tid = FakeLogicArrayObject("tid", width=4)
    ext = ExtendedStreamBus(valid=v, data=good_data, tid=good_tid)
    assert ext.tid is good_tid


def test_helpful_not_found_diagnostics() -> None:
    dut = FakeHierarchyObject("dut", {"vlaid": FakeLogicObject("vlaid")})
    with pytest.raises(SignalNotFoundError, match="Did you mean: 'vlaid'"):
        StreamBus.from_interface(dut)

    empty_if = FakeHierarchyObject("empty_if", {})
    with pytest.raises(SignalNotFoundError, match="Verilator"):
        StreamBus.from_interface(empty_if)


def test_sample_drive_direction_and_drive_idle() -> None:
    @dataclass
    class Tx:
        valid: int
        data: int

    bus = StreamBus.from_interface(_make_stream_scope())
    bus.drive(Tx(valid=1, data=0xBEEF), role=Role.INITIATOR)
    snap = bus.sample()
    assert int(snap["valid"]) == 1
    assert snap["data"].to_unsigned() == 0xBEEF

    # Role enforcement prevents initiator from driving TARGET signal 'ready'.
    with pytest.raises(BusDirectionError, match="cannot be driven by role 'initiator'"):
        bus.drive({"ready": 1}, role=Role.INITIATOR)

    # Action wrapping works (Immediate).
    bus.drive({"valid": 0}, role=Role.INITIATOR, action=Immediate)
    assert isinstance(bus.valid.last_action, Immediate)  # type: ignore[attr-defined]

    # drive_idle respects role.
    bus.ready.value = 0  # type: ignore[union-attr]
    bus.valid.value = 1
    bus.drive_idle(role=Role.INITIATOR)
    assert int(bus.valid.value) == 0
    assert int(bus.ready.value) == 0  # type: ignore[union-attr]
    bus.drive_idle(role=Role.TARGET)
    assert int(bus.ready.value) == 1  # type: ignore[union-attr]

    # Unknown signal raises in strict mode, ignored in non-strict mode.
    with pytest.raises(BusBindingError, match="unknown signal"):
        bus.drive({"nope": 1}, strict=True)
    bus.drive({"nope": 1}, strict=False)


def test_legacy_bus_shim() -> None:
    bus = StreamBus.from_interface(_make_stream_scope(with_ready=False))
    shim = as_legacy_bus(bus, name="axis")
    assert shim.valid is bus.valid
    assert shim.data is bus.data
    assert set(shim._signals.keys()) == {"valid", "data"}
    assert shim._optional_signals == {}
    assert shim._name == "axis"


def test_mypy_rejects_invalid_bus_attribute_statically(tmp_path) -> None:
    """Negative typing test confirming mypy catches nonexistent signal access."""
    bad_file = tmp_path / "bad_bus_usage.py"
    bad_file.write_text(
        """
from cocotb.handle import HierarchyObject, LogicObject
from cocotb_bfm import Bus

class SmallBus(Bus):
    valid: LogicObject

def check(dut: HierarchyObject) -> None:
    b = SmallBus.from_interface(dut)
    _ = b.nonexistent_signal
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(bad_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert '"SmallBus" has no attribute "nonexistent_signal"' in proc.stdout
