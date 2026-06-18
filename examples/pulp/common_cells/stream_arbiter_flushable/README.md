# `common_cells/stream_arbiter_flushable`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_arbiter_flushable` structure while
fixing the import to a flat `4x8-bit` subset and bundling the already-proven
local `rr_arb_tree` subset so the regression can focus on flush behavior.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_arbiter_flushable.sv`

## Why This Is Useful

This example extends the new `stream_arbiter` import with explicit flush
control. It exercises:

- round-robin routing before a stall
- lock-while-stalled output stability
- flush clearing the locked selection and restoring reset priority
- clean restart after flush with the same request set

## Local Layout

```text
examples/pulp/common_cells/stream_arbiter_flushable/
├── README.md
├── rtl/
│   ├── rr_arb_tree.sv
│   └── stream_arbiter_flushable.sv
├── tb/
│   └── stream_arbiter_flushable_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the design to four 8-bit inputs and checks the
sequential ready/valid behavior with a Python-driven stepped clock harness.

The checks cover:

- initial arbitration from reset priority
- stable locked output while downstream stalls
- flush resetting both the lock and arbitration priority
- routing a single remaining requester after the flushed transaction drains

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_arbiter_flushable/run_sim.py
```

Success is indicated by:

```text
PASS stream_arbiter_flushable python reference checks
PASS stream_arbiter_flushable python vm checks
PASS stream_arbiter_flushable python compiled checks
```
