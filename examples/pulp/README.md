# PULP Example Imports

This directory contains focused validation examples based on RTL from the
[`pulp-platform`](https://github.com/pulp-platform) ecosystem.

## Third-Party Attribution

The RTL files under `common_cells/` and `axi/` subdirectories are taken from:

- **pulp-platform/common_cells** — Copyright ETH Zurich and University of Bologna.
  Licensed under the [Solderpad Hardware License, Version 0.51](http://solderpad.org/licenses/SHL-0.51).
- **pulp-platform/axi** — Copyright ETH Zurich and University of Bologna.
  Licensed under the [Solderpad Hardware License, Version 0.51](http://solderpad.org/licenses/SHL-0.51).

Each RTL file carries its original copyright header. The Python testbench files
(`.py`) in `bench/` are part of the veriforge project (MIT License).

---

Initial targets:

- `common_cells`
- `axi`

The intent is not to copy entire upstream verification environments. Instead,
each example here should be a focused, reproducible validation target for
`veriforge`.

Rules for examples in this tree:

- keep each imported example self-contained
- prefer minimal local wrappers over large upstream testbenches
- validate behavior against Icarus or Verilator before relying on local simulation
- run local simulation in this order: reference, VM, compiled when applicable
- document the exact imported files and expected pass condition

Planned structure:

```text
examples/pulp/
├── common_cells/
└── axi/
```

The working plan is documented in `notes/plans/ex_pulp.md`.

Current imported `common_cells` examples:

- `popcount`
- `sub_per_hash`
- `stream_to_mem`
- `rr_arb_tree`
- `cdc_fifo`
- `fifo_v3`
- `spill_register`
- `fall_through_register`
- `stream_xbar`
- `stream_omega_net`
- `stream_xbar_typed`

`stream_xbar_typed` is the current high bug-yield stress target. Unlike the
flattened `stream_xbar` import, it intentionally preserves upstream-style
SystemVerilog constructs that are more likely to expose parser, elaboration,
and engine gaps:

- `parameter type`
- typedef-based packed structs
- typed unpacked-array ports
- generated local arrays and per-output subtrees
- type-parameterized child-module connections

This example now passes the reference, VM, and compiled engines. It exposed and
helped fix several issues across the stack, including typed unpacked-array port
parsing, typedef dimension preservation, nested indexed expressions,
generate-local instance signal prefixing, hierarchical struct-field resolution,
type-alias propagation into child modules, and compiled continuous assigns to
packed struct fields.

Current imported `axi` examples:

- `axi_lite_regs`
- `axi_lite_dw_converter`

`axi_lite_regs` is the second Wave 3 AXI-Lite import. The local example keeps
the upstream typed request/response surface in the imported RTL, while the
deterministic wrapper uses a flat executable shell to validate byte-level
register semantics, direct-load arbitration, read-only masking, and protection
checks.

This example currently passes the reference, VM, and compiled engines.

`axi_lite_dw_converter` is the first Wave 3 AXI-Lite import. The local example
keeps the upstream typed request/response surface in the imported RTL, while the
deterministic wrapper uses a reduced-width executable fixture to keep the local
regression small and reproducible.

This example currently passes the reference and VM engines. On Windows, the
compiled engine is skipped for this target because the generated MSVC build for
the local wrapper still trips backend constant-size limits.
