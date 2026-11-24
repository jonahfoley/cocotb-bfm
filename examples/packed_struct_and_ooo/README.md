# Example: `PackedStruct` & Out-of-Order `KeyedMatcher` Scoreboard

Demonstrates packing/unpacking a SystemVerilog `struct packed` with `IntEnum` fields and verifying out-of-order tagged responses using `KeyedMatcher`.

## What This Demonstrates

- Mirroring a SystemVerilog `typedef struct packed` using `PackedStruct`, `bitfield()`, and `IntEnum`
- Reading and writing packed vectors with `Packet.from_handle()` and `pkt.write_handle()`
- Verifying out-of-order responses across transaction IDs (while preserving per-ID FIFO order) with `Scoreboard` + `KeyedMatcher(key=lambda p: p.tid)`

## Run

```bash
python examples/packed_struct_and_ooo/run_example.py
```
