# `axi/axi_lite_regs`

Second imported PULP AXI-Lite validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL copy preserves the upstream typed AXI-Lite request/response surface,
and the local benches now reach the real `axi_lite_regs` DUT through a small
flat-to-typed wrapper so the stepped regression can stay reproducible.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_lite_regs.sv`
- Upstream testbench: `test/tb_axi_lite_regs.sv`

## Why This Is Next

`axi_lite_regs` stays within AXI-Lite but exercises more stateful behavior than
the width converter:

- typed request and response structs
- byte-level register storage
- direct-load arbitration against AXI writes
- read-only byte masking
- AXI protection checks on read and write paths
- partial final-chunk reads over a non-word-aligned register-file size

## Local Layout

```text
examples/pulp/axi/axi_lite_regs/
├── README.md
├── rtl/
│   ├── axi_pkg.sv
│   └── axi_lite_regs.sv
├── tb/
│   └── axi_lite_regs_tb.sv
└── run_sim.py
```

## Local Validation Approach

The upstream testbench depends on AXI interfaces, verification drivers, random
traffic generation, and register macros from `common_cells`. The local import
keeps the typed DUT surface, and the local benches bridge the stepped flat
driver signals into typed request/response structs while still instantiating the
real `axi_lite_regs` module.

Current checks focus on:

- reset-state byte layout
- direct loads into the register array
- stalling AXI writes when a writable targeted byte is being directly loaded
- mixed writable and read-only writes
- full read-only write rejection
- valid reads across the first word and the final partial chunk
- out-of-range read rejection
- privileged and secure protection gating on both reads and writes

Current local RTL adaptations:

- replaced upstream include-macro dependencies with a self-contained local copy
- flattened reset values into a packed parameter instead of upstream byte-array parameters
- kept the typed DUT in the executable path while using a small flat-to-typed
  harness so the stepped tests can drive deterministic scalar signals
