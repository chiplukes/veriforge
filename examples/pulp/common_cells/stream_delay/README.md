# `common_cells/stream_delay`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_delay` state-machine shape while fixing
the import to a small fixed-delay, 8-bit payload subset and dropping the random
stall and generic payload-type surfaces.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_delay.sv`

## Why This Is Useful

This example adds a distinct stateful timing-oriented ready/valid primitive. It
exercises:

- delayed output-valid assertion after an input request
- no immediate bypass even when downstream is already ready
- stable delayed output while the sink stalls
- clean return to idle after the delayed transfer is accepted

## Local Layout

```text
examples/pulp/common_cells/stream_delay/
├── README.md
├── rtl/
│   └── stream_delay.sv
├── tb/
│   └── stream_delay_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the payload to 8 bits and the delay to two cycles, so
the regression stays focused on the handshake timing semantics without pulling
in the upstream counter or random-stall dependencies.

The checks cover:

- idle state after reset
- two-cycle delay before `valid_o` asserts
- stalled output remaining valid until the sink is ready
- same two-cycle delay even when `ready_i` is already high

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_delay/run_sim.py
```

Success is indicated by:

```text
PASS stream_delay python reference checks
PASS stream_delay python vm checks
PASS stream_delay python compiled checks
```
