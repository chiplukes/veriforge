# `common_cells/cdc_fifo_gray`

Standalone imported PULP validation target for the gray-pointer CDC FIFO.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream gray-pointer FIFO structure on a fixed 8-bit,
depth-2 subset while bundling the already-proven local `sync`, gray-code, and
spill-register helpers so the checkpoint stays self-contained.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/cdc_fifo_gray.sv`

## Why This Is Useful

This example adds direct coverage for a more realistic CDC FIFO architecture
than `cdc_2phase`, including gray-coded pointer synchronization and a
memory-backed asynchronous buffer.

It exercises:

- reset-idle source ready and destination idle state
- one-item transfer becoming visible in the destination clock domain
- full detection after two queued items
- ordered drain through the destination-side spill-register path
- source-side ready recovery after the FIFO drains

## Local Layout

```text
examples/pulp/common_cells/cdc_fifo_gray/
├── README.md
├── rtl/
│   ├── binary_to_gray.sv
│   ├── cdc_fifo_gray.sv
│   ├── gray_to_binary.sv
│   ├── spill_register.sv
│   ├── spill_register_flushable.sv
│   └── sync.sv
├── tb/
│   └── cdc_fifo_gray_tb_local.sv
├── bench/
│   └── cdc_fifo_gray_bench.py
└── run_sim.py
```

The high-level bench uses source and destination clock periods 10 and 14. It
fills the gray FIFO and destination spill stage under backpressure, then checks
that a fifth beat follows the first four in order when the destination resumes.
The directed runner retains the signal-level CDC checks.

## Running It

```text
uv run python examples/pulp/common_cells/cdc_fifo_gray/run_sim.py
uv run python examples/pulp/common_cells/cdc_fifo_gray/bench/cdc_fifo_gray_bench.py
```
