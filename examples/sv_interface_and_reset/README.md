# Example: SystemVerilog `interface` Binding & `Reset`

Builds directly on [`hello_valid_ready`](../hello_valid_ready/) by reusing the exact same `ByteStreamBus`, `ByteStreamDriver`, and `ByteStreamMonitor` against SystemVerilog `interface` instances instead of flat prefixed ports.

## What This Demonstrates

- Composing nested SystemVerilog `interface`s with `sub_bus()` and binding the whole hierarchy in one `SvIfTopBus.from_interface(dut)` call
- Wiring an ingress `Monitor` into a `Scoreboard` channel through a 1-line Python reference model via `Scoreboard.connect_model()`
- Using `Reset(top.rst_n, active_low=True, sync=True)` with `ResetPolicy.FLUSH` to automatically abort stalled transfers, return the bus to idle, and recover after reset

## Run

```bash
python examples/sv_interface_and_reset/run_example.py
```
