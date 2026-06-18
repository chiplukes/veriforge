# `axi/axi_xbar`

Focused later-wave AXI import covering a small full-AXI crossbar subset.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The parser/elaboration path keeps the typed local `axi_xbar` module with
package typedefs and typed request/response ports. The active stepped bench
`axi_xbar_exec_tb` now wraps `axi_xbar_typed_exec_tb`, so the shared
cross-engine check reaches the real local DUT while still preserving the
deterministic typed `2x2` testbench shell around the target responders.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_xbar.sv`
- Reference bench: `test/tb_axi_xbar.sv`

## Local Validation Approach

The upstream module depends on typed AXI request and response structs, macros,
ID widening, and a larger helper stack. The local import keeps the typed
top-level parser surface, while the executable harness narrows runtime behavior
to a fixed `2x2` single-beat AXI subset with local typed request/response
packing, ID reflection, and local target responders around the real DUT path.

Current checks focus on:

- parallel routing to two decoded target regions
- decode-error responses for unmapped addresses
- response ID reflection back to the slave ports
- deterministic same-target write arbitration between two slave ports
