"""Standalone Verilator runner for the SV Interface & Reset example.

Run directly from the repository root::

    python examples/sv_interface_and_reset/run_example.py
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
    with tempfile.TemporaryDirectory(prefix="cocotb_bfm_sv_if_") as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "sim_build"

        runner.build(
            sources=[EXAMPLE_DIR / "sv_if_dut.sv"],
            hdl_toplevel="sv_if_dut",
            build_dir=build_dir,
            build_args=["--timing", "--public-flat-rw"],
            always=True,
        )
        runner.test(
            hdl_toplevel="sv_if_dut",
            test_module=("examples.sv_interface_and_reset.test_sv_interface_and_reset"),
            build_dir=build_dir,
            test_dir=tmp_path,
        )


if __name__ == "__main__":
    main()
