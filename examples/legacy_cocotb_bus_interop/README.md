# Legacy `cocotb-bus` & `cocotbext-*` Interoperability Example

This example shows how to migrate an existing testbench incrementally to `cocotb-bfm` without rewriting legacy scoreboards, drivers, or `cocotbext-*` VIPs all at once.

## Patterns Demonstrated in [`test_legacy_interop.py`](test_legacy_interop.py)

1. **`cocotb_bfm.Monitor` → Legacy `cocotb_bus.scoreboard.Scoreboard`**
   `Monitor` implements `add_callback()`, so `legacy_sb.add_interface(mon, expected)` works out of the box (using the real `cocotb-bus` package when installed, or a minimal contract stand-in otherwise).
2. **`cocotb_bfm.Bus` (on a SystemVerilog `interface`) → Legacy driver via `as_legacy_bus()`**
   Wraps a statically typed `Bus.from_interface(dut.in_if)` in `LegacyBusShim` so legacy components inspecting `_signals` / `_optional_signals` or duck-typed handle attributes can drive an SV interface immediately.
3. **Existing `cocotbext-*` async sink (`recv()` / `read()`) → `cocotb_bfm.Scoreboard` via `AsyncSourceAdapter`**
   Adapts sinks from `cocotbext-axi`, `cocotbext-spi`, `cocotbext-uart`, etc. into `MonitorProtocol` so they feed directly into `cocotb_bfm.Scoreboard`.

## Running the Example (Verilator)

```bash
python examples/legacy_cocotb_bus_interop/run_example.py
```
