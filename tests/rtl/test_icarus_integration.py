"""Pytest harness running ``cocotb-bfm`` integration tests on Icarus Verilog."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cocotb_tools.runner import get_runner

RTL_DIR = Path(__file__).resolve().parent / "hdl"


@pytest.mark.rtl
@pytest.mark.skipif(
    shutil.which("iverilog") is None, reason="iverilog binary not found"
)
def test_icarus_4state_and_bfm_suite(tmp_path: Path) -> None:
    runner = get_runner("icarus")
    build_dir = tmp_path / "sim_build"

    runner.build(
        sources=[RTL_DIR / "icarus_stream_dut.v"],
        hdl_toplevel="icarus_stream_dut",
        build_dir=build_dir,
        always=True,
    )

    runner.test(
        hdl_toplevel="icarus_stream_dut",
        test_module="tests.rtl.cocotb_test_icarus",
        build_dir=build_dir,
        test_dir=tmp_path,
    )
