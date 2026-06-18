# `axi/axi_lite_xbar`

Focused later-wave AXI import covering a small AXI-Lite crossbar subset.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The parser/elaboration path includes the typed local `axi_lite_xbar` module
with package typedefs and typed request/response ports. The active stepped
bench `axi_lite_xbar_exec_tb` now wraps `axi_lite_xbar_typed_exec_tb`, so the
shared cross-engine check reaches the real local DUT while still preserving the
deterministic typed `2x2` testbench shell around the target responders.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_lite_xbar.sv`
- Reference bench: `test/tb_axi_lite_xbar.sv`

## Local Validation Approach

The upstream module depends on typed AXI request/response structs, macros, and a
larger helper stack. The local import keeps the typed top-level parser surface,
while the executable harness narrows runtime behavior to a fixed `2x2` AXI-Lite
crossbar subset with local typed request/response packing and local target
responders around the real DUT path.

Current checks focus on:

- routing writes and reads to two different decoded target regions
- returning decode errors for unmapped addresses
- serializing same-target traffic with deterministic slave-port arbitration
- validating the subset on reference, VM, and compiled engines
