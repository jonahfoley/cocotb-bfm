"""Standalone Verilator runner for the Avalon-MM CSR example.

Run directly from the repository root::

    python examples/avalon_mm_csr/run_example.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from cocotb_tools.runner import get_runner

EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parents[1]


def main() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    runner = get_runner("verilator")
    with tempfile.TemporaryDirectory(prefix="cocotb_bfm_avalon_csr_") as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "sim_build"

        runner.build(
            sources=[EXAMPLE_DIR / "avalon_csr_dut.sv"],
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
        )


if __name__ == "__main__":
    main()
