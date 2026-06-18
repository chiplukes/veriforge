# `common_cells/edge_propagator_rx`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream receive-side edge propagator behavior on a
deterministic single-clock subset while bundling the small `sync` /
`sync_wedge` dependency locally.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/edge_propagator_rx.sv`

## Why This Is Useful

This example adds the receive-side half of the small control-handshake family.
It exercises:

- synchronized pulse generation from an incoming level
- acknowledgement generation from the synchronized level
- one-cycle receive pulse behavior
- clean re-arming for repeated requests

## Local Layout

```text
examples/pulp/common_cells/edge_propagator_rx/
├── README.md
├── rtl/
│   ├── edge_propagator_rx.sv
│   ├── sync.sv
│   └── sync_wedge.sv
├── tb/
│   └── edge_propagator_rx_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates the receive-side propagator and checks:

- reset-idle outputs
- no pulse before the first request
- exactly one `valid_o` pulse and one delayed `ack_o` pulse for the first input
- return to idle after the first request
- a second input producing exactly one more receive pulse and acknowledge pulse

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/edge_propagator_rx/run_sim.py
```

Success is indicated by:

```text
PASS edge_propagator_rx deterministic checks
```
