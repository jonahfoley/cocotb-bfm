# Legacy `cocotb-bus` & `cocotbext-*` Interoperability Example

This example shows how to migrate an existing testbench incrementally to `cocotb-bfm` against the real `cocotb-bus` package without rewriting legacy scoreboards, drivers, monitors, or `cocotbext-*` VIPs all at once.

## Patterns Demonstrated in [`test_legacy_interop.py`](test_legacy_interop.py)

1. **`cocotb_bfm.Monitor` → Real `cocotb_bus.scoreboard.Scoreboard` via `as_legacy_monitor()`**
   Wraps a modern `Monitor` in `LegacyMonitorShim` (a `cocotb_bus.monitors.Monitor` subclass) so `legacy_sb.add_interface(as_legacy_monitor(mon), expected)` passes `isinstance(..., Monitor)` cleanly without spawning unused coroutines.
2. **`cocotb_bfm.Bus` (on a SystemVerilog `interface`) → Unmodified `cocotb_bus.drivers.BusDriver` via `as_legacy_bus()`**
   Wraps a statically typed `Bus.from_interface(dut.in_if)` in `LegacyBusShim` (a `cocotb_bus.bus.Bus` subclass) so an unmodified `BusDriver` subclass can swap in `driver.bus = as_legacy_bus(...)` directly.
3. **Unmodified `cocotb_bus.monitors.BusMonitor` → `cocotb_bfm.Scoreboard` (zero adapter)**
   `cocotb_bus.monitors.BusMonitor` already exposes `name` and `add_callback()`, so it plugs straight into `modern_sb.add_channel("from_legacy_mon", legacy_mon)`.
4. **Existing `cocotbext-*` async sink (`recv()` / `read()`) → `cocotb_bfm.Scoreboard` via `AsyncSourceAdapter`**
   Adapts sinks from `cocotbext-axi`, `cocotbext-spi`, `cocotbext-uart`, etc. into `MonitorProtocol` so they feed directly into `cocotb_bfm.Scoreboard`.

## Running the Example (Verilator)

```bash
python examples/legacy_cocotb_bus_interop/run_example.py
```
