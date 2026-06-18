# `common_cells/stream_register`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_register` control structure while
fixing the payload to an 8-bit bus and replacing the macro-based register
include with the same explicit sequential logic. This keeps the deterministic
regression small without changing the handshake behavior we care about here.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_register.sv`
- Related upstream test: `test/stream_register_tb.sv`

## Why This Is Useful

This example fills the remaining gap between the already-imported
`fall_through_register` and `spill_register` primitives. It exercises the
simple one-stage stream register that:

- cuts combinational `valid`/`data` forwarding
- keeps default-ready behavior while empty
- blocks overwrite once the single output register is full
- allows drain-and-refill on the same clock edge
- supports synchronous clear through `clr_i`

## Local Layout

```text
examples/pulp/common_cells/stream_register/
├── README.md
├── rtl/
│   └── stream_register.sv
├── tb/
│   └── stream_register_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The upstream randomized class-based testbench is replaced with the same stepped
single-clock Python harness pattern already used for the other local
ready/valid imports.

The checks cover:

- reset state
- no combinational pass-through while empty
- capture into the single output register
- blocked overwrite while full
- simultaneous drain/refill on one edge
- synchronous clear of a buffered item

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_register/run_sim.py
```

Success is indicated by:

```text
PASS stream_register python reference checks
PASS stream_register python vm checks
PASS stream_register python compiled checks
```
