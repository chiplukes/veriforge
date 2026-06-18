# `axi/axi_to_axi_lite`

Second resumed PULP AXI bridge import after `axi_lite_to_axi`.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps a parser-friendly typed bridge surface with the upstream top
module names, and the executable benches now reach that real bridge through the
typed scalar wrapper instead of the older flat deterministic shell.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_to_axi_lite.sv`

## Why This Is Next

`axi_to_axi_lite` complements `axi_lite_to_axi` and exercises the opposite
adapter direction:

- full AXI write and read address cropping into AXI-Lite
- ID reflection from full AXI responses back onto AXI `B` and `R`
- single outstanding write and read flow control in the local deterministic path
- fixed `last` insertion on the read-response side

## Local Layout

```text
examples/pulp/axi/axi_to_axi_lite/
├── README.md
├── rtl/
│   ├── axi_pkg.sv
│   └── axi_to_axi_lite.sv
├── tb/
│   └── axi_to_axi_lite_tb.sv
└── run_sim.py
```

## Local Validation Approach

The upstream source depends on additional AXI helpers (`axi_atop_filter`,
`axi_burst_splitter`, `fifo_v3`, and interface macros). The local import keeps a
typed parser target for the top-level bridge and ID-reflect stage, but omits the
interface wrapper, assertions, and full helper chain so the example stays
self-contained.

The executable benches validate the deterministic subset currently covered here,
still keeping the regression on the single-beat, non-atomic slice that is
easiest to validate with the current simulator infrastructure:

- one supported single-beat write with ID reflection on `B`
- one supported single-beat read with ID reflection on `R`
- AXI-Lite request-channel passthrough on address, protection, data, and strobe
- response-side backpressure until a reflected ID is pending

Burst splitting, atomics filtering, and deeper outstanding-transaction behavior
remain deferred to future higher-fidelity AXI work.
