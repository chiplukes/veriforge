# `common_cells/cdc_fifo`

Fifth imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL is a focused adaptation of upstream `cdc_fifo_2phase` plus its
`cdc_2phase` dependency. It keeps the dual-clock ready/valid FIFO behavior while
removing assertion includes and type-parameter syntax that are not needed for a
small deterministic regression.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files: `src/cdc_fifo_2phase.sv`, `src/cdc_2phase.sv`
- Upstream testbench: `test/cdc_fifo_tb.sv`

## Why This Is Useful

This is the first imported dual-clock PULP example. It adds:

- two independent clock domains
- separate source and destination resets
- pointer transport across clock domains
- FIFO full and empty behavior under asynchronous scheduling

## Local Layout

```text
examples/pulp/common_cells/cdc_fifo/
├── README.md
├── rtl/
│   ├── cdc_2phase.sv
│   └── cdc_fifo_2phase.sv
├── tb/
│   └── cdc_fifo_tb_local.sv
├── bench/
│   └── cdc_fifo_bench.py
└── run_sim.py
```

## Local Validation Approach

The upstream testbench randomizes clock periods, stalls, and mailbox-based
checking. The local version replaces that with a deterministic Python-driven
dual-clock harness.

All three engines use the same stepped validation flow. The runner schedules a
source clock and a destination clock directly from Python, then checks:

- idle state after reset release
- one-item transfer from source to destination
- FIFO full behavior after two queued writes
- ordered drain of queued data across the destination clock domain
- source-side ready reopening after the queued data drains

The high-level bench runs the source and destination clocks at periods 10 and
14, fills the FIFO under destination backpressure, then checks that three
beats cross in order. The directed runner retains the detailed pointer and
reset checks.

## Running It

```text
uv run python examples/pulp/common_cells/cdc_fifo/run_sim.py
uv run python examples/pulp/common_cells/cdc_fifo/bench/cdc_fifo_bench.py
```

Success is indicated by:

```text
PASS cdc_fifo python reference checks
PASS cdc_fifo python vm checks
PASS cdc_fifo python compiled checks
```
