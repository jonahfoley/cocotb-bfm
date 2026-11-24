"""Pytest harness running ``cocotb-bfm`` VHDL integration tests on GHDL (or NVC)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cocotb_tools.runner import get_runner

RTL_DIR = Path(__file__).resolve().parent / "hdl"


@pytest.mark.rtl
@pytest.mark.skipif(shutil.which("ghdl") is None, reason="ghdl binary not found")
def test_ghdl_vhdl_bfm_suite(tmp_path: Path) -> None:
    runner = get_runner("ghdl")
    build_dir = tmp_path / "sim_build"

    runner.build(
        sources=[RTL_DIR / "vhdl_stream_dut.vhd"],
        hdl_toplevel="vhdl_stream_dut",
        build_dir=build_dir,
        build_args=["--std=08"],
        always=True,
    )

    runner.test(
        hdl_toplevel="vhdl_stream_dut",
        test_module="tests.rtl.cocotb_test_vhdl",
        build_dir=build_dir,
        test_dir=build_dir,
        test_args=["--std=08"],
    )
