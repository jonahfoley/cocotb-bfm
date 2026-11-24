# `cocotb-bfm` Examples

Runnable Verilator + cocotb 2.0 examples ordered from beginner quickstarts to full protocol and migration patterns. Run any example directly from the repository root:

| # | Example | Concepts Covered | Run Command |
| :- | :--- | :--- | :--- |
| 1 | [`hello_valid_ready`](./hello_valid_ready/) | First `Bus`, `from_prefix()`, `Driver`, `Monitor`, `Scoreboard` | `python examples/hello_valid_ready/run_example.py` |
| 2 | [`sv_interface_and_reset`](./sv_interface_and_reset/) | Reusing the same `Bus`/BFMs on SystemVerilog `interface`s, `sub_bus()`, `connect_model()`, and `Reset` (`ResetPolicy.FLUSH`) | `python examples/sv_interface_and_reset/run_example.py` |
| 3 | [`packed_struct_and_ooo`](./packed_struct_and_ooo/) | `PackedStruct` + `IntEnum` bitfields and out-of-order `KeyedMatcher` verification | `python examples/packed_struct_and_ooo/run_example.py` |
| 4 | [`avalon_mm_csr`](./avalon_mm_csr/) | Full Avalon-MM `RequestDriver`, CSR register bank, `PackedStruct`, and nested SV interfaces | `python examples/avalon_mm_csr/run_example.py` |
| 5 | [`legacy_cocotb_bus_interop`](./legacy_cocotb_bus_interop/) | Incremental migration with real `cocotb-bus` (`as_legacy_bus`, `as_legacy_monitor`) and `cocotbext-*` (`AsyncSourceAdapter`) | `python examples/legacy_cocotb_bus_interop/run_example.py` |
