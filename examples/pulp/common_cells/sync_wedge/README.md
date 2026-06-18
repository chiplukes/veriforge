# `common_cells/sync_wedge`

Standalone imported PULP validation target for the synchronized edge detector.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the already-proven simplified `sync_wedge` subset used by
other checkpoints, bundling the standalone `sync` dependency locally so the
example stays self-contained.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/sync_wedge.sv`

## Why This Is Useful

This example adds direct coverage for synchronized rising/falling edge detection
and the sampled-output state that larger wrappers such as `edge_detect` build
on.

It exercises:

- async reset clearing `serial_o`, `r_edge_o`, and `f_edge_o`
- two-stage synchronized latency before a rising edge pulse
- one-cycle `r_edge_o` pulse followed by `serial_o` going high
- disabled hold with stable input and no spurious pulses
- two-stage synchronized latency before a falling edge pulse
- one-cycle `f_edge_o` pulse followed by `serial_o` returning low
- async reset reassertion clearing the sampled output immediately

## Local Layout

```text
examples/pulp/common_cells/sync_wedge/
├── README.md
├── rtl/
│   ├── sync.sv
│   └── sync_wedge.sv
├── tb/
│   └── sync_wedge_tb_local.sv
└── run_sim.py
```

## Running It

```text
uv run python examples/pulp/common_cells/sync_wedge/run_sim.py
```
