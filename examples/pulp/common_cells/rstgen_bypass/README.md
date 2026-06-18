# `common_cells/rstgen_bypass`

Standalone imported PULP validation target for the reset synchronizer with test
mode bypass.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream reset-synchronizer and test-bypass behavior
while replacing the external clock-mux dependency and assertion surface with a
self-contained local subset.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/rstgen_bypass.sv`

## Why This Is Useful

This example adds direct coverage for reset release synchronization and explicit
test-mode reset bypass behavior, which is a realistic control primitive rather
than another data-path helper.

It exercises:

- functional reset holding both outputs low
- multi-cycle synchronized release after functional reset deassertion
- asynchronous reassertion of functional reset
- immediate test-mode bypass through `rst_test_mode_ni`
- returning from test mode back to the functional reset path

## Local Layout

```text
examples/pulp/common_cells/rstgen_bypass/
├── README.md
├── rtl/
│   ├── rstgen_bypass.sv
│   └── tc_clk_mux2.sv
├── tb/
│   └── rstgen_bypass_tb_local.sv
└── run_sim.py
```

## Running It

```text
uv run python examples/pulp/common_cells/rstgen_bypass/run_sim.py
```
