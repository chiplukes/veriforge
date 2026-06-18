# `common_cells/cdc_2phase`

Standalone imported PULP validation target for the basic two-phase CDC bridge.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the already-proven `cdc_2phase` subset used by the
`cdc_fifo` checkpoint, but promotes it into a standalone example so the
single-transfer CDC semantics are validated directly.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/cdc_2phase.sv`

## Why This Is Useful

This example adds direct coverage for a compact dual-clock ready/valid transfer
primitive rather than only testing it indirectly through `cdc_fifo`.

It exercises:

- reset-idle source ready and destination idle state
- one in-flight transfer becoming visible in the destination clock domain
- source backpressure while that transfer is still pending
- blocked overwrite attempts not replacing the in-flight payload
- destination hold while `dst_ready_i` stays low
- ready reopening after the acknowledge returns to the source domain
- a second clean transfer after the first completes

## Local Layout

```text
examples/pulp/common_cells/cdc_2phase/
├── README.md
├── rtl/
│   └── cdc_2phase.sv
├── tb/
│   └── cdc_2phase_tb_local.sv
└── run_sim.py
```

## Running It

```text
uv run python examples/pulp/common_cells/cdc_2phase/run_sim.py
```
