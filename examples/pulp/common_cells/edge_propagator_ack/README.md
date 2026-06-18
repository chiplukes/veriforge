# `common_cells/edge_propagator_ack`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream edge-propagation-with-acknowledge behavior on
a deterministic dual-clock slice while bundling the small `sync` /
`sync_wedge` dependency locally.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/edge_propagator_ack.sv`

## Why This Is Useful

This example adds a compact cross-domain control primitive outside the
ready/valid family. It exercises:

- edge-to-level conversion in the source domain
- synchronized pulse generation in the destination domain
- acknowledgement returning across the opposite clock domain
- clean re-arming after the round-trip handshake completes

## Local Layout

```text
examples/pulp/common_cells/edge_propagator_ack/
├── README.md
├── rtl/
│   ├── edge_propagator_ack.sv
│   ├── sync.sv
│   └── sync_wedge.sv
├── tb/
│   └── edge_propagator_ack_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates the handshake bridge with independent transmit
and receive clocks and checks:

- reset-idle outputs
- no pulse before the first transmitted edge
- one receive pulse and one acknowledge round-trip for the first request
- return to idle after the first handshake completes
- a second transmitted edge producing exactly one more receive pulse

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/edge_propagator_ack/run_sim.py
```

Success is indicated by:

```text
PASS edge_propagator_ack deterministic checks
```
