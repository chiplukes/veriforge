Imported narrow checkpoint for `pulp-platform/common_cells` `isochronous_spill_register`.

This local subset keeps the upstream two-entry isochronous dual-clock spill-register
behavior on a fixed 8-bit payload while removing the upstream macro include and
parameterized type surface so the example stays inside the current parser subset.

The high-level bench infers separate source and destination clock/reset domains.
It fills the two-entry buffer while the destination stalls, then checks three
beats arrive in order after the stall clears. The directed runner retains the
edge-level and bypass-mode checks.

Run:

```bash
uv run python examples/pulp/common_cells/isochronous_spill_register/run_sim.py
uv run python examples/pulp/common_cells/isochronous_spill_register/bench/isochronous_spill_register_bench.py
```
