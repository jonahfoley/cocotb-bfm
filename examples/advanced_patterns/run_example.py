"""Standalone Verilator runner for the Advanced Patterns example.

Run directly from the repository root::

    python examples/advanced_patterns/run_example.py
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
    with tempfile.TemporaryDirectory(prefix="cocotb_bfm_patterns_") as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "sim_build"

        runner.build(
            sources=[EXAMPLE_DIR / "patterns_dut.sv"],
            hdl_toplevel="patterns_dut",
            build_dir=build_dir,
            build_args=["--timing", "--public-flat-rw"],
            always=True,
        )
        runner.test(
            hdl_toplevel="patterns_dut",
            test_module="examples.advanced_patterns.test_advanced_patterns",
            build_dir=build_dir,
            test_dir=tmp_path,
        )


if __name__ == "__main__":
    main()
