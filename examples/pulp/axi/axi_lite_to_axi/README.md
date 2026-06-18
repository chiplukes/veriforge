# `axi/axi_lite_to_axi`

First resumed PULP AXI bridge import after the compiled wide-signal cleanup.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream typed AXI-Lite-to-AXI bridge module for parser
coverage, and the executable benches now reach that real bridge through the
typed scalar wrapper instead of the older local flat shell.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_lite_to_axi.sv`

## Why This Is Next

`axi_lite_to_axi` is a small bridge target that stays deterministic locally but
still exercises the important typed-adapter behavior:

- AXI-Lite write-address, write-data, and read-address passthrough
- fixed AXI burst and size insertion
- cache-field propagation on read and write address channels
- write and read response mapping back into AXI-Lite

## Local Layout

```text
examples/pulp/axi/axi_lite_to_axi/
├── README.md
├── rtl/
│   ├── axi_lite_to_axi.sv
│   └── axi_pkg.sv
├── tb/
│   └── axi_lite_to_axi_tb.sv
└── run_sim.py
```

## Local Validation Approach

The upstream module uses typed AXI request/response records. The local import
keeps that module intact for parsing, and the executable testbench now wraps the
same typed bridge so the regression keeps the scalar stepped surface while
exercising the real upstream combinational mapping on all three engines.

Current checks focus on:

- forward write-channel mapping into AXI
- forward read-channel mapping into AXI
- fixed `size`, `burst`, and `last` insertion
- write and read response propagation back into AXI-Lite
- combinational update when request, cache, and response inputs change
