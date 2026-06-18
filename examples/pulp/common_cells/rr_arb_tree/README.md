# `common_cells/rr_arb_tree`

Fourth imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL copy is a focused adaptation of the upstream arbiter behavior that
keeps the same core request/grant contract while avoiding assertion-header and
type-parameter dependencies in the upstream source.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/rr_arb_tree.sv`
- Upstream testbench: `test/rr_arb_tree_tb.sv`

## Why This Is Useful

`rr_arb_tree` is the first imported arbiter in the PULP example wave. It adds a
stateful ready/valid selection pattern that is different from the earlier
buffering and combinational examples.

Current local checks focus on:

- deterministic round-robin rotation across active requesters
- lock-in behavior while the downstream grant is withheld
- priority reset through `flush_i`
- correct `idx_o`, `gnt_o`, and `data_o` routing for the selected requester

## Local Layout

```text
examples/pulp/common_cells/rr_arb_tree/
├── README.md
├── rtl/
│   └── rr_arb_tree.sv
├── tb/
│   ├── rr_arb_tree_tb_local.sv
│   └── rr_arb_tree_tb_vm_local.sv
└── run_sim.py
```

## Local Validation Approach

The upstream testbench is throughput-oriented and randomized. The local wrapper
replaces that with a deterministic sequence that still exercises the arbiter's
key state transitions.

The reference engine uses the timed Verilog wrapper. The VM and compiled engines
use a Python-stepped harness against a minimal wrapper module so the same state
checks can run without depending on long timed `initial` blocks.

The current wrapper uses a 4-input, 8-bit configuration and validates:

- repeated arbitration between two continuously active requesters
- output stability and fixed selection while `gnt_i` is low with `LockIn = 1`
- recovery to the initial priority after `flush_i`
- correct payload selection for a single active requester

## Running It

```text
uv run python examples/pulp/common_cells/rr_arb_tree/run_sim.py
```

Success is indicated by:

```text
PASS rr_arb_tree deterministic checks
PASS rr_arb_tree python vm checks
PASS rr_arb_tree python compiled checks
```
