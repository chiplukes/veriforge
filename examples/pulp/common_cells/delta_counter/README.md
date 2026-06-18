# `common_cells/delta_counter`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream variable-delta counter structure directly while
fixing the import to a small deterministic width-4 wrapper.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/delta_counter.sv`

## Why This Is Useful

This example adds a small stateful non-stream primitive that future `counter`
and `max_counter` imports can reuse. It exercises:

- synchronous load and clear
- up-count and down-count paths with variable delta
- overflow/underflow behavior on the widened internal counter
- the behavioral difference between transient and sticky overflow tracking

## Local Layout

```text
examples/pulp/common_cells/delta_counter/
├── README.md
├── rtl/
│   └── delta_counter.sv
├── tb/
│   └── delta_counter_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates both `STICKY_OVERFLOW = 0` and
`STICKY_OVERFLOW = 1` variants at width 4 and drives a deterministic sequence.

The checks cover:

- reset state
- load to a fixed value
- increment without overflow
- increment with overflow
- decrement after overflow to distinguish transient from sticky overflow
- underflow on down-count
- synchronous clear resetting both variants

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/delta_counter/run_sim.py
```

Success is indicated by:

```text
PASS delta_counter deterministic checks
```
