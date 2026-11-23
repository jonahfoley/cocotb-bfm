"""Pytest harness running the Avalon-MM CSR SV interface & async reset Verilator suite."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cocotb_tools.runner import get_runner

REPO_ROOT = Path(__file__).resolve().parents[2]
AVALON_SV = REPO_ROOT / "examples" / "avalon_mm_csr" / "avalon_csr_dut.sv"


@pytest.mark.rtl
@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator binary not found")
def test_verilator_avalon_mm_csr_end_to_end(tmp_path: Path) -> None:
    runner = get_runner("verilator")
    build_dir = tmp_path / "sim_build"

    runner.build(
        sources=[AVALON_SV],
        hdl_toplevel="avalon_csr_tb_top",
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
        hdl_toplevel="avalon_csr_tb_top",
        test_module="tests.rtl.cocotb_test_avalon_csr",
        build_dir=build_dir,
        test_dir=tmp_path,
        extra_env={"PYTHONPATH": str(REPO_ROOT)},
    )
