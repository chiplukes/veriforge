# `common_cells/sync`

Standalone imported PULP validation target for the basic multi-stage
synchronizer.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream synchronizer behavior while staying inside the
current parser subset with a small self-contained wrapper setup.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/sync.sv`

## Why This Is Useful

This example adds direct coverage for a very common synchronizer primitive that
many larger CDC and control wrappers build on.

It exercises:

- async reset forcing the output to the configured reset value
- three-stage propagation latency for a rising input
- three-stage propagation latency for a falling input
- non-default `RESET_VALUE = 1'b1` behavior draining back to zero after reset release
- async reset reassertion restoring the configured reset value immediately

## Local Layout

```text
examples/pulp/common_cells/sync/
├── README.md
├── rtl/
│   └── sync.sv
├── tb/
│   └── sync_tb_local.sv
└── run_sim.py
```

## Running It

```text
uv run python examples/pulp/common_cells/sync/run_sim.py
```
