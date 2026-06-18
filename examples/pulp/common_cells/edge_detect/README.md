# `common_cells/edge_detect`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream wrapper structure and bundles small local
`sync` / `sync_wedge` helpers, replacing the upstream clock-gating cell with the
equivalent `en_i`-guarded clocked update.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/edge_detect.sv`

## Why This Is Useful

This example adds a small sequential helper outside the counter family. It
exercises:

- two-stage synchronization before edge detection
- one-cycle rising-edge pulse generation
- one-cycle falling-edge pulse generation
- no repeated pulses while the input level remains stable

## Local Layout

```text
examples/pulp/common_cells/edge_detect/
├── README.md
├── rtl/
│   ├── sync.sv
│   ├── sync_wedge.sv
│   └── edge_detect.sv
├── tb/
│   └── edge_detect_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper drives a deterministic low-to-high-to-low-to-high input
sequence.

The checks cover:

- reset outputs idle
- two-cycle latency before the synchronized rising-edge pulse
- pulse clearing on the following cycle
- two-cycle latency before the synchronized falling-edge pulse
- another rising pulse after returning low

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/edge_detect/run_sim.py
```

Success is indicated by:

```text
PASS edge_detect deterministic checks
```
