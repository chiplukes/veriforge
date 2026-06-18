# `common_cells/counter`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream wrapper structure directly and bundles the
already-imported `delta_counter` dependency locally.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/counter.sv`

## Why This Is Useful

This example adds the fixed-delta wrapper on top of `delta_counter`. It
exercises:

- synchronous load and clear
- one-step up-count and down-count behavior
- overflow and underflow through the wrapped delta-counter path
- the difference between transient and sticky overflow behavior

## Local Layout

```text
examples/pulp/common_cells/counter/
├── README.md
├── rtl/
│   ├── counter.sv
│   └── delta_counter.sv
├── tb/
│   └── counter_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates both `STICKY_OVERFLOW = 0` and
`STICKY_OVERFLOW = 1` variants at width 4.

The checks cover:

- reset state
- load to a fixed value
- multiple increment steps
- overflow on increment from the maximum value
- decrement after overflow to distinguish transient from sticky overflow
- synchronous clear
- underflow on decrement from zero

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/counter/run_sim.py
```

Success is indicated by:

```text
PASS counter deterministic checks
```
