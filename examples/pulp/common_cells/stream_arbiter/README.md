# `common_cells/stream_arbiter`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_arbiter` structure while fixing the
import to a flat `4x8-bit` subset and bundling the already-proven local
`rr_arb_tree` subset as the dependency instead of reopening the upstream typed
parameter surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files:
  - `src/stream_arbiter.sv`
  - `src/stream_arbiter_flushable.sv`

## Why This Is Useful

This example extends the small ready/valid stream slice with a stateful
many-to-one arbitration primitive. It exercises:

- round-robin routing across multiple valid inputs
- one-hot ready returning only to the granted input
- stable selected data while the output is stalled
- clean priority advance once the stalled transaction is finally accepted

## Local Layout

```text
examples/pulp/common_cells/stream_arbiter/
├── README.md
├── rtl/
│   ├── rr_arb_tree.sv
│   ├── stream_arbiter_flushable.sv
│   └── stream_arbiter.sv
├── tb/
│   └── stream_arbiter_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the design to four 8-bit inputs and checks the
sequential ready/valid behavior with a Python-driven stepped clock harness.

The checks cover:

- idle state after reset
- round-robin rotation across two active inputs
- lock-while-stalled behavior with stable output data
- single-requester routing after arbitration state updates

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_arbiter/run_sim.py
```

Success is indicated by:

```text
PASS stream_arbiter python reference checks
PASS stream_arbiter python vm checks
PASS stream_arbiter python compiled checks
```
