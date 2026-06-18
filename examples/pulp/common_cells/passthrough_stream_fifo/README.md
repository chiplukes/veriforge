# `common_cells/passthrough_stream_fifo`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream pointer-based pass-through FIFO behavior while
fixing the import to a flat 8-bit, depth-3 subset and removing the assertion
and register-macro include surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/passthrough_stream_fifo.sv`

## Why This Is Useful

This example adds the no-timing-cut FIFO variant that can reuse a full buffer
entry more efficiently when pop and push happen in the same cycle. It
exercises:

- normal ordered fill and drain through a three-entry buffer
- full-buffer backpressure when `SAME_CYCLE_RW` is disabled
- full-buffer simultaneous pop/push acceptance when `SAME_CYCLE_RW` is enabled
- preserving FIFO order while replacing the drained tail on a full same-cycle
  exchange
- synchronous flush returning the wrapper to the empty ready state

## Local Layout

```text
examples/pulp/common_cells/passthrough_stream_fifo/
├── README.md
├── rtl/
│   └── passthrough_stream_fifo.sv
├── tb/
│   └── passthrough_stream_fifo_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the payload to 8 bits and the depth to 3 entries, and
uses two local tops to cover `SAME_CYCLE_RW = 1` and `SAME_CYCLE_RW = 0`.

The checks cover:

- initial empty/ready state after reset
- ordered fill to the full condition
- same-cycle full pop/push reopening only on the enabled wrapper
- ordered drain after the full same-cycle exchange
- flush clearing the queued state

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/passthrough_stream_fifo/run_sim.py
```

Success is indicated by:

```text
PASS passthrough_stream_fifo python reference checks
PASS passthrough_stream_fifo python vm checks
PASS passthrough_stream_fifo python compiled checks
```
