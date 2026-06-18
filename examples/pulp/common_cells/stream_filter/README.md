# `common_cells/stream_filter`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_filter` logic directly. This import
stays on the tiny combinational drop-or-pass ready/valid primitive rather than
opening any wider dependency surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_filter.sv`

## Why This Is Useful

This example adds the smallest remaining standalone ready/valid primitive. It
exercises:

- pass-through valid/ready behavior when `drop_i` is low
- forced downstream-valid suppression when `drop_i` is high
- forced upstream-ready assertion when `drop_i` is high
- immediate combinational switching between pass-through and drop modes

## Local Layout

```text
examples/pulp/common_cells/stream_filter/
├── README.md
├── rtl/
│   └── stream_filter.sv
├── tb/
│   └── stream_filter_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset keeps the design fully combinational and checks the routing
directly with a Python-driven harness.

The checks cover:

- idle behavior with `drop_i = 0`
- normal pass-through valid/ready coupling
- drop mode suppressing `valid_o`
- drop mode forcing `ready_o` high regardless of `ready_i`

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_filter/run_sim.py
```

Success is indicated by:

```text
PASS stream_filter python reference checks
PASS stream_filter python vm checks
PASS stream_filter python compiled checks
```
