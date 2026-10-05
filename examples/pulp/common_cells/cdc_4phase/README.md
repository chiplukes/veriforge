# `common_cells/cdc_4phase`

Standalone imported PULP validation target for the 4-phase CDC.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream 4-phase handshake structure on a fixed 8-bit
payload while bundling already-proven local `sync` and `spill_register`
helpers. The checkpoint includes both the default decoupled mode and a
non-decoupled wrapper so the shared regression covers the key handshake
behavior difference directly.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/cdc_4phase.sv`

## Why This Is Useful

This example adds direct coverage for the 4-phase CDC handshake that sits
adjacent to the existing 2-phase CDC checkpoints and underpins the larger
reset-controller wave.

It exercises:

- decoupled mode reopening the source while the destination sink still stalls
- buffered destination hold and later drain through the destination spill-register path
- non-decoupled mode keeping the source blocked until the destination
  acknowledges the transfer
- blocked source updates not overwriting the in-flight destination payload

## Local Layout

```text
examples/pulp/common_cells/cdc_4phase/
├── README.md
├── rtl/
│   ├── cdc_4phase.sv
│   ├── spill_register.sv
│   ├── spill_register_flushable.sv
│   └── sync.sv
├── tb/
│   └── cdc_4phase_tb_local.sv
├── bench/
│   └── cdc_4phase_bench.py
└── run_sim.py
```

The high-level bench runs the source and destination at periods 10 and 14. It
checks that decoupled mode reopens the source while the destination stalls,
then verifies two beats arrive in order. The directed runner retains the
non-decoupled and edge-level checks.

## Running It

```text
uv run python examples/pulp/common_cells/cdc_4phase/run_sim.py
uv run python examples/pulp/common_cells/cdc_4phase/bench/cdc_4phase_bench.py
```
