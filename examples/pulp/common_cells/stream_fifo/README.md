# `common_cells/stream_fifo`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_fifo` wrapper shape while fixing the
import to a flat 8-bit subset and bundling the already-imported local `fifo_v3`
dependency.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_fifo.sv`

## Why This Is Useful

This example adds the standard ready/valid FIFO wrapper on top of the already
proven `fifo_v3` storage primitive. It exercises:

- non-fall-through buffering and backpressure when full
- ordered drain and refill through the ready/valid wrapper
- fall-through pass-through behavior when empty
- flush clearing buffered state through the wrapper interface

## Local Layout

```text
examples/pulp/common_cells/stream_fifo/
├── README.md
├── rtl/
│   ├── fifo_v3.sv
│   └── stream_fifo.sv
├── tb/
│   └── stream_fifo_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the payload to 8 bits and checks two local wrappers:
depth-3 non-fall-through and depth-3 fall-through.

The checks cover:

- reset state and ready/valid defaults
- fill, full backpressure, drain, and flush for the non-fall-through wrapper
- immediate pass-through when empty in fall-through mode
- correct storage and drain after a stalled fall-through transfer

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_fifo/run_sim.py
```

Success is indicated by:

```text
PASS stream_fifo python reference checks
PASS stream_fifo python vm checks
PASS stream_fifo python compiled checks
```
