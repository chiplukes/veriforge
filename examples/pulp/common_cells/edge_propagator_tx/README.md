# `common_cells/edge_propagator_tx`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream source-side edge propagator behavior on a
deterministic single-clock subset.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/edge_propagator_tx.sv`

## Why This Is Useful

This example adds a compact handshake/control primitive that exercises:

- edge-to-level latching in the source domain
- two-stage acknowledgement return timing
- hold-until-ack behavior
- clean re-arming for a second request

## Local Layout

```text
examples/pulp/common_cells/edge_propagator_tx/
├── README.md
├── rtl/
│   └── edge_propagator_tx.sv
├── tb/
│   └── edge_propagator_tx_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates the source-side propagator and checks:

- reset-idle output
- first request latching `valid_o`
- hold while `ack_i` remains low
- clear after the acknowledgement round-trip delay
- a second request re-arming and clearing cleanly

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/edge_propagator_tx/run_sim.py
```

Success is indicated by:

```text
PASS edge_propagator_tx deterministic checks
```
