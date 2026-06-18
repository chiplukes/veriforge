# `common_cells/cdc_reset_ctrlr`

Standalone imported PULP validation target for the CDC reset controller.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream mirrored clear-sequence controller structure on
a fixed, parser-safe subset. It bundles a dedicated non-decoupled 4-phase
control transport and a local `sync` helper so the checkpoint stays
self-contained.

The current checkpoint covers two local wrappers:

- `cdc_reset_ctrlr_tb_local` with synchronous clear sequencing
  (`CLEAR_ON_ASYNC_RESET = 0`)
- `cdc_reset_ctrlr_async_reset_tb_local` with deterministic startup plus
  mirrored one-sided async-reset-driven clear recovery
  (`CLEAR_ON_ASYNC_RESET = 1`)

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files:
  - `src/cdc_reset_ctrlr.sv`
  - `src/cdc_reset_ctrlr_pkg.sv`

## Why This Is Useful

This example adds direct coverage for the control logic behind the clearable CDC
wave, without immediately pulling in the larger `cdc_2phase_clearable`
integration surface.

It exercises:

- one-sided synchronous clear requests asserting local isolate first
- mirrored isolate propagation into the opposite clock domain
- clear assertion only after both sides acknowledge isolation
- post-clear sequencing where clear drops before isolation is removed
- async-reset-enabled startup clear sequencing after reset release
- one-sided async reset on either domain asserting local isolate immediately,
  propagating mirrored isolate and clear into the opposite domain, and
  returning both domains to idle cleanly after acknowledgements
- symmetry by running the same sequence from both domains

## Local Layout

```text
examples/pulp/common_cells/cdc_reset_ctrlr/
├── README.md
├── rtl/
│   ├── cdc_4phase_ctrl.sv
│   ├── cdc_reset_ctrlr.sv
│   └── sync.sv
├── tb/
│   └── cdc_reset_ctrlr_tb_local.sv
└── run_sim.py
```

## Running It

```text
uv run python examples/pulp/common_cells/cdc_reset_ctrlr/run_sim.py
```
