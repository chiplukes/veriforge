# `common_cells/spill_register_flushable`

Standalone imported PULP validation target for the flush-capable spill wrapper.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream two-stage spill-register control structure and
adds the explicit `flush_i` path, while removing type-parameter syntax and
assertion includes that are not needed for a deterministic regression here.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/spill_register_flushable.sv`
- Related upstream users: `spill_register`, `stream_fifo_optimal_wrap`,
  `stream_xbar`, and `stream_omega_net`

## Why This Is Useful

This example adds direct coverage for the flushable variant that several other
local imports already depend on.

It exercises:

- one-cycle registration of input data and `valid` when `Bypass = 0`
- two-slot buffering under downstream backpressure
- flush clearing both internal stages without accepting a simultaneous new input
- clean refill after a flush
- combinational pass-through when `Bypass = 1`, including flush being ignored on
  that path

## Local Layout

```text
examples/pulp/common_cells/spill_register_flushable/
├── README.md
├── rtl/
│   └── spill_register_flushable.sv
├── tb/
│   └── spill_register_flushable_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The wrapper file exposes two fixed tops:

- non-bypass mode to validate buffered capture, backpressure, flush clear, and
  post-flush refill
- bypass mode to validate transparent combinational pass-through with
  `flush_i` ignored

The Python runner checks:

- idle state after reset
- first stalled capture becoming visible after one clock edge
- second stalled capture filling the spill path and asserting backpressure
- flush clearing the queued state and blocking a simultaneous replacement write
- clean refill and final drain after flush
- bypass mode remaining purely combinational regardless of `flush_i`

## Running It

```text
uv run python examples/pulp/common_cells/spill_register_flushable/run_sim.py
```

Success is indicated by:

```text
PASS spill_register_flushable python reference checks
PASS spill_register_flushable python vm checks
PASS spill_register_flushable python compiled checks
```
