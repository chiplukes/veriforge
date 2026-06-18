# `common_cells/isochronous_4phase_handshake`

Standalone imported PULP validation target for the isochronous four-phase handshake.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream handshake state machine and reset behavior while
expanding the register macros into explicit sequential logic so the checkpoint
stays self-contained.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/isochronous_4phase_handshake.sv`
- Related upstream users: `isochronous_spill_register` and other isochronous CDC
  wrappers

## Why This Is Useful

This example adds direct coverage for a small isochronous clock-domain handshake
primitive that is not just another ready/valid buffer.

It exercises:

- source-side request toggling only when `src_valid_i && src_ready_o`
- destination-side request visibility after the destination clock observes the toggle
- hold behavior while the destination is not ready
- destination acknowledge toggling only after a completed destination handshake
- source ready reopening only after the acknowledge returns across the source clock

## Local Layout

```text
examples/pulp/common_cells/isochronous_4phase_handshake/
├── README.md
├── rtl/
│   └── isochronous_4phase_handshake.sv
├── tb/
│   └── isochronous_4phase_handshake_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The wrapper uses fixed 2:1 source/destination clocks and checks:

- reset-idle outputs
- first request dropping `src_ready_o`
- delayed destination visibility on the first request
- stable destination hold while `dst_ready_i` stays low
- acknowledge-driven clear at the destination
- source-side ready recovery after the acknowledge returns
- a second clean request/acknowledge round-trip

## Running It

```text
uv run python examples/pulp/common_cells/isochronous_4phase_handshake/run_sim.py
```

Success is indicated by:

```text
PASS
```
