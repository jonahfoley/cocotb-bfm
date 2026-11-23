"""Pytest harness running real Verilator 5 + cocotb 2.0 SystemVerilog integration tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cocotb_tools.runner import get_runner

RTL_DIR = Path(__file__).parent / "hdl"


@pytest.mark.rtl
@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator binary not found")
def test_verilator_sv_interface_and_bfm_suite(tmp_path: Path) -> None:
    runner = get_runner("verilator")
    build_dir = tmp_path / "sim_build"

    runner.build(
        sources=[RTL_DIR / "stream_dut.sv"],
        hdl_toplevel="stream_tb_top",
        build_dir=build_dir,
        build_args=[
            "--timing",
            "--public-flat-rw",
            "--trace-structs",
            "-Wno-WIDTHTRUNC",
        ],
        always=True,
    )

    runner.test(
        hdl_toplevel="stream_tb_top",
        test_module="tests.rtl.cocotb_test_stream",
        build_dir=build_dir,
        test_dir=tmp_path,
    )
