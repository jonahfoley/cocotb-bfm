# Avalon-MM CSR Example

This directory provides a complete, end-to-end reference example showing how to build a statically typed **Avalon-MM Control/Status Register (CSR)** BFM on top of `cocotb-bfm`.

## What It Demonstrates

- **[`avalon_csr_dut.sv`](avalon_csr_dut.sv)** — A real SystemVerilog CSR register bank with:
  - SystemVerilog `interface avalon_csr_if` containing `typedef struct packed` sidebands (`csr_meta_t`),
  - A second diagnostic SV `interface csr_diag_if` carrying a packed status struct (`csr_status_t`),
  - `waitrequest` backpressure, `byteenable` masked writes, pipelined `readdatavalid`, read-only and write-1-to-clear (W1C) registers,
  - Asynchronous active-low reset (`arst_n`),
  - A secondary CSR bank wired to flat prefixed ports (`flat_csr_*`) to demonstrate binding the exact same `Bus` class via both `from_interface()` and `from_prefix()`.
- **[`avalon_csr.py`](avalon_csr.py)** — Typed `PackedStruct` codecs (`CsrMeta`, `CsrCtrl`, `CsrStatus`), `AvalonCsrBus` + composite `CsrSubsystemBus`, `AvalonCsrMaster(RequestDriver)`, and `AvalonCsrMonitor(Monitor)`.

This example is exercised directly by the Verilator RTL integration test suite in `tests/rtl/test_avalon_csr_integration.py`.
