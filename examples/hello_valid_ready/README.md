# Example 01: Hello Valid/Ready

The simplest complete `cocotb-bfm` testbench: a 1-stage 8-bit `+1` pipeline (`adder_dut.sv`) verified with a typed `Bus`, `Driver`, `Monitor`, and `Scoreboard`.

## What This Demonstrates

- Declaring a typed `Bus` with `signal(width=..., direction=..., idle_value=...)`
- Binding flat prefixed ports (`in_valid`/`in_data`/`in_ready`) using `Bus.from_prefix()`
- Writing a minimal `Driver` and `Monitor`
- Starting and stopping components cleanly with `async with`
- Checking expected outputs with `Scoreboard`

## Run

```bash
python examples/hello_valid_ready/run_example.py
```
