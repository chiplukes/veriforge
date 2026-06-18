# `common_cells/stream_fifo_optimal_wrap`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_fifo_optimal_wrap` wrapper shape while
fixing the import to a flat 8-bit subset and bundling the already-proven local
`spill_register_flushable`, `stream_fifo`, and `fifo_v3` dependencies.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_fifo_optimal_wrap.sv`

## Why This Is Useful

This example checks the wrapper that chooses the smaller depth-2 spill-register
implementation and the standard fifo-backed implementation for deeper queues. It
exercises:

- the depth-2 spill-register path with no combinational valid bypass
- depth-2 backpressure after two queued words
- ordered drain and flush on the depth-2 path
- the depth-3 fifo path with fill, full backpressure, pop, and flush

## Local Layout

```text
examples/pulp/common_cells/stream_fifo_optimal_wrap/
├── README.md
├── rtl/
│   ├── fifo_v3.sv
│   ├── spill_register_flushable.sv
│   ├── stream_fifo.sv
│   └── stream_fifo_optimal_wrap.sv
├── tb/
│   └── stream_fifo_optimal_wrap_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the payload to 8 bits and checks two local wrappers:
`Depth=2` for the spill-register path and `Depth=3` for the fifo path.

The depth-2 wrapper intentionally leaves `usage_o` outside the checked surface,
matching the upstream module's unsupported usage signal on that path.

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_fifo_optimal_wrap/run_sim.py
```

Success is indicated by:

```text
PASS stream_fifo_optimal_wrap python reference checks
PASS stream_fifo_optimal_wrap python vm checks
PASS stream_fifo_optimal_wrap python compiled checks
```
