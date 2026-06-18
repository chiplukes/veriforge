Imported narrow checkpoint for `pulp-platform/common_cells` `isochronous_spill_register`.

This local subset keeps the upstream two-entry isochronous dual-clock spill-register
behavior on a fixed 8-bit payload while removing the upstream macro include and
parameterized type surface so the example stays inside the current parser subset.

Run:

```bash
uv run python examples/pulp/common_cells/isochronous_spill_register/run_sim.py
```
