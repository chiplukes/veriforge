# `common_cells/rstgen`

Standalone imported PULP validation target for the thin reset-generator wrapper.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `rstgen` wrapper shape and its underlying
reset-synchronizer behavior while bundling the already-proven local
`rstgen_bypass` and `tc_clk_mux2` dependencies so the checkpoint stays
self-contained.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/rstgen.sv`

## Why This Is Useful

This example adds direct coverage for the common wrapper that exposes the
reset-synchronizer as a simpler functional reset primitive.

It exercises:

- functional reset holding both outputs low
- multi-cycle synchronized release in normal mode
- immediate wrapper-level bypass behavior when `test_mode_i` is enabled
- `init_no` forcing high in test mode even while reset stays asserted
- clean return from test mode after the synchronized path has refilled

## Local Layout

```text
examples/pulp/common_cells/rstgen/
├── README.md
├── rtl/
│   ├── rstgen.sv
│   ├── rstgen_bypass.sv
│   └── tc_clk_mux2.sv
├── tb/
│   └── rstgen_tb_local.sv
└── run_sim.py
```

## Running It

```text
uv run python examples/pulp/common_cells/rstgen/run_sim.py
```
