# `cocotb-bfm`

Modern, statically typed **Bus**, **Driver**, **Monitor**, **Reset**, and **Scoreboard** primitives for **cocotb 2.0+** with first-class **SystemVerilog `interface`** support.

Built as a clean-slate redesign addressing the architectural pain points of legacy `cocotb-bus`:

- **First-class static typing & LSP support** — Declarative `Bus` classes backed by PEP 681 (`@dataclass_transform`), giving full `mypy --strict`, `pyright`, and IDE autocomplete with zero plugins.
- **Raw cocotb 2.0 handles** — Bus attributes hold real `LogicObject`, `LogicArrayObject`, `ArrayObject`, and `HierarchyObject` handles directly (with optional `sample()`, `drive()`, and `drive_idle()` helpers).
- **SystemVerilog `interface` support** — Bind the exact same `Bus` definition to SV `interface` instances (`from_interface`), interface arrays (`from_interface_array`), flat prefixed/suffixed signals (`from_prefix`), regexes (`from_regex`), or explicit mappings (`from_mapping`), including nested sub-buses.
- **Composition over inheritance & explicit lifecycle** — `Bus` is owned independently of `Driver` and `Monitor`. Components never spawn hidden tasks in `__init__`; use `start()` / `stop()` or `async with`.
- **Reset-aware components** — First-class `Reset` descriptor supporting active-high/low and sync/async resets; `Driver` defaults to `ResetPolicy.FLUSH` with explicit `reset_policy` and `_on_reset()` customization.
- **Modern `Scoreboard` + ecosystem interop** — Generic multi-channel `Scoreboard` with `InOrderMatcher`, `WindowMatcher`, `KeyedMatcher`, `UnorderedMatcher`, reference-model wiring, and rich diffs, while `as_legacy_monitor()` plugs into legacy `cocotb_bus.scoreboard.Scoreboard`, unmodified `cocotb_bus.monitors.BusMonitor` plugs directly into `cocotb_bfm.Scoreboard`, and `AsyncSourceAdapter` / `as_legacy_bus()` interoperate cleanly with `cocotbext-*` (`cocotbext-axi`, `cocotbext-spi`, `cocotbext-uart`, etc.).

## Requirements

- **Python:** `>= 3.11` (3.11, 3.12, 3.13, and 3.14 tested in CI)
- **cocotb:** `>= 2.0.0` (cocotb 1.x is **not** supported; `cocotb-bfm` relies on cocotb 2.0+ typed handles such as `LogicObject`, `LogicArrayObject`, `PackedObject`, `LogicArray`, and `Task.cancel()`)
- **RTL Simulator (optional, for running examples & RTL tests):** Verilator 5.036+ (or any cocotb 2.0+ supported simulator)

```bash
pip install cocotb-bfm
# or for local development (includes cocotb-bus, pytest, mypy, ruff):
uv sync --extra dev
```

## Quick Example

```python
from dataclasses import dataclass
from cocotb.handle import LogicArrayObject, LogicObject
from cocotb.triggers import RisingEdge
from cocotb_bfm import Bus, Direction, Driver, Monitor, Reset, Scoreboard, signal


class StreamBus(Bus):
    valid: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    data: LogicArrayObject = signal(direction=Direction.INITIATOR)
    ready: LogicObject | None = signal(default=None, direction=Direction.TARGET)


@dataclass(frozen=True)
class StreamTx:
    data: int


class StreamDriver(Driver[StreamBus, StreamTx]):
    async def _drive_transaction(self, tx: StreamTx) -> None:
        self.bus.valid.value = 1
        self.bus.data.value = tx.data
        while True:
            await RisingEdge(self.clock)
            if self.bus.ready is None or int(self.bus.ready.value):
                break
        self.bus.valid.value = 0


class StreamMonitor(Monitor[StreamBus, StreamTx]):
    async def _monitor_loop(self) -> None:
        while True:
            await RisingEdge(self.clock)
            if int(self.bus.valid.value) and (
                self.bus.ready is None or int(self.bus.ready.value)
            ):
                self._emit(StreamTx(data=self.bus.data.value.to_unsigned()))
```

Bind to either a SystemVerilog `interface` or flat prefixed signals identically:

```python
rx_bus = StreamBus.from_interface(dut.rx_if)
tx_bus = StreamBus.from_prefix(dut, "tx")
rst = Reset(dut.rst_n, active_low=True, sync=True)

async with (
    StreamDriver(rx_bus, dut.clk, reset=rst) as drv,
    StreamMonitor(tx_bus, dut.clk, reset=rst) as mon,
    Scoreboard(name="loopback") as sb,
):
    ch = sb.add_channel("tx", mon)
    ch.expect(StreamTx(data=0xAB))
    await drv.send(StreamTx(data=0xAB))
```

## Examples

Runnable Verilator examples live in [`examples/`](./examples/README.md):

- [`examples/hello_valid_ready`](examples/hello_valid_ready/README.md) — minimal `Bus`, `Driver`, `Monitor`, and `Scoreboard` quickstart
- [`examples/sv_interface_and_reset`](examples/sv_interface_and_reset/README.md) — SystemVerilog `interface` binding, `connect_model()`, and `Reset` recovery
- [`examples/packed_struct_and_ooo`](examples/packed_struct_and_ooo/README.md) — `PackedStruct` bitfields and out-of-order `KeyedMatcher` scoreboarding
- [`examples/advanced_patterns`](examples/advanced_patterns/README.md) — `RegexResolver`, `MappingResolver`, `CompositeResolver`, interface arrays, custom `Component`, `Role`, `QueueAdapter`, `WindowMatcher`, and `UnorderedMatcher`
- [`examples/avalon_mm_csr`](examples/avalon_mm_csr/README.md) — full Avalon-MM `RequestDriver` and CSR bank
- [`examples/legacy_cocotb_bus_interop`](examples/legacy_cocotb_bus_interop/README.md) — interop with legacy `cocotb-bus` and `cocotbext-*`
