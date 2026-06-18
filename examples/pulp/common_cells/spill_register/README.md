# `common_cells/spill_register`

Seventh imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL is a focused adaptation of upstream `spill_register` and
`spill_register_flushable`. It keeps the core two-stage spill behavior and the
`Bypass` option while removing type-parameter syntax and assertion includes that
are not needed for a deterministic regression here.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files: `src/spill_register.sv`, `src/spill_register_flushable.sv`
- Related upstream tests: `test/stream_register_tb.sv`, isochronous crossing tests that instantiate `spill_register`

## Why This Is Useful

This example adds coverage for a small but important ready/valid primitive that
completely cuts combinational interface paths when `Bypass = 0`.

It exercises:

- one-cycle registration of input data and `valid`
- two-slot buffering under downstream backpressure
- blocked third input when both internal stages are occupied
- ordered drain after a spill into the second stage
- pure combinational pass-through when `Bypass = 1`

## Local Layout

```text
examples/pulp/common_cells/spill_register/
├── README.md
├── rtl/
│   ├── spill_register_flushable.sv
│   └── spill_register.sv
├── tb/
│   └── spill_register_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The upstream implementation is very small, so the local version keeps the same
control structure rather than wrapping a larger upstream verification setup.

The wrapper file exposes two fixed tops:

- non-bypass mode to validate the real spill-register behavior
- bypass mode to validate the transparent pass-through option

The Python runner checks:

- idle state after reset
- no combinational pass-through in non-bypass mode
- first capture becoming visible after one clock edge
- spill into the second internal stage while the output is stalled
- backpressure once both stages are full
- ordered drain of buffered data
- immediate combinational propagation in bypass mode

## Running It

```text
uv run python examples/pulp/common_cells/spill_register/run_sim.py
```

Success is indicated by:

```text
PASS spill_register python reference checks
PASS spill_register python vm checks
PASS spill_register python compiled checks
```
