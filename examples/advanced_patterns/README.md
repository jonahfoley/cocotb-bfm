# Example: Resolvers, Custom `Component`, `Role`, `QueueAdapter`, & Matchers

Focused tests covering the remaining user-facing `cocotb-bfm` primitives:

1. **`test_resolvers_and_interface_array`** — `RegexResolver` (`from_regex`), `MappingResolver` (`from_mapping`), `CompositeResolver`, `from_interface_array()`, and `from_interface_map()`.
2. **`test_component_backpressure_and_target_role`** — Subclassing `Component` directly for a standalone backpressure generator, plus `Role.TARGET` direction enforcement (`BusDirectionError`).
3. **`test_window_unordered_matchers_and_queue_adapter`** — Feeding `cocotb.queue.Queue` instances into `Scoreboard` via `QueueAdapter` with `WindowMatcher` and `UnorderedMatcher`.

## Run

```bash
python examples/advanced_patterns/run_example.py
```
