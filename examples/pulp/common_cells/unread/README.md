# `common_cells/unread`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream sink-cell behavior while removing the
tool-specific guarded path so the subset stays inside the current parser and
simulation surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/unread.sv`

## Why This Is Useful

This example adds the smallest structural sink helper outside the stream and
counter families. It exercises:

- instantiation of an input-only helper
- deterministic driving of the consumed input
- clean cross-engine simulation of a no-output marker cell

## Local Layout

```text
examples/pulp/common_cells/unread/
├── README.md
├── rtl/
│   └── unread.sv
├── tb/
│   └── unread_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates the sink cell, drives a deterministic sequence on
its input, and checks that the wrapper reaches the pass marker without
simulation errors.

The checks cover:

- zero input
- asserted input
- returning low after a toggle

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/unread/run_sim.py
```

Success is indicated by:

```text
PASS unread deterministic checks
```
