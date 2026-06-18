# `axi/axi_lite_mailbox`

Next post-bridge AXI-Lite example after `axi_fifo`.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps a parser-friendly typed mailbox surface with the upstream
top-level module names, and the executable benches now drive the real
`axi_lite_mailbox` DUT through typed AXI-Lite request/response wrappers.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_lite_mailbox.sv`

## Why This Is Next

`axi_lite_mailbox` stays within AXI-Lite but adds two-sided stateful behavior:

- two AXI-Lite slave ports
- cross-port mailbox writes and reads
- per-port status and error registers
- basic interrupt-enable and pending behavior

## Local Layout

```text
examples/pulp/axi/axi_lite_mailbox/
├── README.md
├── rtl/
│   └── axi_lite_mailbox.sv
├── tb/
│   └── axi_lite_mailbox_tb.sv
└── run_sim.py
```

## Local Validation Approach

The upstream source depends on `fifo_v3`, `spill_register`, `addr_decode`, AXI
typedef macros, and `common_cells/registers.svh`. The local import keeps a typed
parser target for the mailbox surface, and the executable benches preserve that
typed request/response packing at the testbench boundary while exercising the
real imported mailbox DUT on a deterministic cross-engine subset.

Current checks focus on:

- reset status on both ports
- cross-port mailbox write/read in both directions
- status register behavior around empty and non-empty mailboxes
- error reporting for empty reads
- interrupt-enable, pending, and acknowledge behavior for the error IRQ
