# `common_cells/plru_tree`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream pseudo-LRU replacement behavior on a small
deterministic 4-entry subset while trimming the assertion and generic-tree
surface down to an explicit state machine.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/plru_tree.sv`

## Why This Is Useful

This example adds a compact stateful control primitive that exercises:

- one-hot state updates driven by a usage vector
- registered replacement-policy state
- combinational decode of the current replacement choice
- nested branch behavior without relying on stream interfaces

## Local Layout

```text
examples/pulp/common_cells/plru_tree/
├── README.md
├── rtl/
│   └── plru_tree.sv
├── tb/
│   └── plru_tree_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a deterministic `ENTRIES = 4` subset and checks:

- reset selecting entry 0 as the initial replacement candidate
- `used_i[0]` moving replacement to entry 2
- `used_i[2]` moving replacement to entry 1
- `used_i[1]` moving replacement to entry 3
- `used_i[3]` returning replacement to entry 0
- idle hold when no usage bit is asserted

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/plru_tree/run_sim.py
```

Success is indicated by:

```text
PASS plru_tree deterministic checks
```
