# `common_cells/credit_counter`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream state-machine structure while removing the
macro include and type-parameter surface by fixing the subset to three credits.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/credit_counter.sv`

## Why This Is Useful

This example adds a small counter-like control primitive that tracks available
credits and exposes status flags. It exercises:

- reset-to-full and reset-to-empty behavior
- give and take updates
- same-cycle give/take hold behavior
- `credit_init_i` priority over normal updates
- `credit_left_o`, `credit_crit_o`, and `credit_full_o` flag behavior

## Local Layout

```text
examples/pulp/common_cells/credit_counter/
├── README.md
├── rtl/
│   └── credit_counter.sv
├── tb/
│   └── credit_counter_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates two fixed three-credit variants:

- one that resets and reinitializes full
- one that resets and reinitializes empty

The checks cover:

- reset state for both variants
- legal take transitions from full to critical and down to one credit
- legal give transitions from empty to critical and full
- same-cycle give/take preserving the current credit count
- `credit_init_i` priority over concurrent give/take activity
- `credit_left_o`, `credit_crit_o`, and `credit_full_o` flag transitions

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/credit_counter/run_sim.py
```

Success is indicated by:

```text
PASS credit_counter deterministic checks
```
