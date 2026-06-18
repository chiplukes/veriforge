# `axi/axi_fifo`

First post-bridge AXI import after the two focused AXI adapter examples.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps a parser-friendly typed AXI FIFO surface, and the
fixed-width executable benches now reach the real `axi_fifo` DUT through the
local `axi_fifo_typed_tb` bridge.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_fifo.sv`

## Why This Is Next

`axi_fifo` is the smallest remaining later-wave AXI target that still exercises
full AXI channel buffering:

- depth-0 passthrough behavior
- depth-1 per-channel buffering
- independent FIFOs for `AW`, `W`, `B`, `AR`, and `R`
- buffering on both request and response channels

## Local Layout

```text
examples/pulp/axi/axi_fifo/
├── README.md
├── rtl/
│   └── axi_fifo.sv
├── tb/
│   └── axi_fifo_tb.sv
└── run_sim.py
```

## Local Validation Approach

The upstream source depends on typed channel structs, `fifo_v3`, and interface
macros. The local import keeps the typed top-level parser surface, while the
depth-0 and depth-1 executable benches collapse the stepped scalar ports into
typed request and response structs before instantiating the real `axi_fifo`
DUT. This keeps the example deterministic and cross-engine friendly without a
separate behavioral shell.

Current checks focus on:

- depth-0 combinational passthrough
- depth-1 buffering of `AW`, `W`, and `AR` requests when the master side stalls
- depth-1 buffering of `B` and `R` responses when the slave side stalls
- ID, data, response, and `last` preservation through the buffered channels
