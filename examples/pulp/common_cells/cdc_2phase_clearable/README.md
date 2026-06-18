# `common_cells/cdc_2phase_clearable`

Standalone imported PULP validation target for the clearable 2-phase CDC.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream clearable 2-phase CDC structure on a fixed
8-bit subset while bundling already-proven local `sync`, `cdc_4phase_ctrl`,
and `cdc_reset_ctrlr` helpers.

The current checkpoint covers two local wrappers:

- `cdc_2phase_clearable_tb_local` with synchronous clear sequencing
  (`CLEAR_ON_ASYNC_RESET = 0`)
- `cdc_2phase_clearable_async_reset_tb_local` with deterministic startup plus
  mirrored source-side and destination-side async-reset-driven clear recovery
  (`CLEAR_ON_ASYNC_RESET = 1`)

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/cdc_2phase_clearable.sv`

## Why This Is Useful

This example is the first end-to-end ready/valid integration of the new reset
controller wave.

It exercises:

- source-side synchronous clear cancelling an in-flight transfer without
  leaving stale destination valid state behind
- mirrored clear-pending behavior across both domains
- destination-side synchronous clear recovering cleanly from a stalled visible
  payload
- async-reset-enabled startup clear sequencing before normal traffic begins
- source-side async reset withdrawing a stalled destination payload and
  reopening the channel cleanly after recovery
- destination-side async reset clearing visible stalled payload state
  immediately, propagating mirrored clear-pending back into the source domain,
  and reopening the channel cleanly after recovery
- fresh transfers working normally after each clear sequence completes

## Local Layout

```text
examples/pulp/common_cells/cdc_2phase_clearable/
├── README.md
├── rtl/
│   ├── cdc_2phase_clearable.sv
│   ├── cdc_4phase_ctrl.sv
│   ├── cdc_reset_ctrlr.sv
│   └── sync.sv
├── tb/
│   └── cdc_2phase_clearable_tb_local.sv
└── run_sim.py
```

## Running It

```text
uv run python examples/pulp/common_cells/cdc_2phase_clearable/run_sim.py
```
