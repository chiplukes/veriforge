# `common_cells/fifo_v3`

Sixth imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL is a focused adaptation of upstream `fifo_v3`. It keeps the core
single-clock queue semantics and interface while removing assertion includes and
type-parameter syntax that are not needed for a deterministic regression here.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/fifo_v3.sv`
- Upstream testbench: `test/fifo_tb.sv`

## Why This Is Useful

This is the first imported single-clock FIFO primitive from `common_cells`. It
adds coverage for:

- arbitrary-depth pointer wrap logic
- `full_o`, `empty_o`, and `usage_o` state tracking
- blocked pushes when the queue is full
- synchronous `flush_i`
- fall-through behavior when pushing into an empty queue
- simultaneous push and pop while preserving occupancy

## Local Layout

```text
examples/pulp/common_cells/fifo_v3/
├── README.md
├── rtl/
│   └── fifo_v3.sv
├── tb/
│   └── fifo_v3_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The upstream testbench randomizes pushes, pops, and flushes across multiple FIFO
configurations. The local version replaces that with a deterministic Python-driven
harness.

The wrapper file exposes four fixed configurations that cover the meaningful
behavior from the upstream bench:

- depth 3, non-fall-through
- depth 3, fall-through
- depth 1, non-fall-through
- depth 1, fall-through

The runner checks:

- reset state
- ordered push/pop behavior
- full detection and blocked pushes
- simultaneous push/pop occupancy preservation
- synchronous flush
- empty-queue fall-through visibility and consumption

## Running It

```text
uv run python examples/pulp/common_cells/fifo_v3/run_sim.py
```

Success is indicated by:

```text
PASS fifo_v3 python reference checks
PASS fifo_v3 python vm checks
PASS fifo_v3 python compiled checks
```
