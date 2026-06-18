# `common_cells/binary_to_gray`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream combinational converter directly and fixes the
wrapper to a small 4-bit subset.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/binary_to_gray.sv`

## Why This Is Useful

This example adds a tiny self-contained combinational helper outside the stream
and counter families. It exercises:

- direct binary-to-Gray conversion
- low-bit transitions that only toggle one Gray bit at a time
- upper-bit transitions across the 4-bit subset

## Local Layout

```text
examples/pulp/common_cells/binary_to_gray/
├── README.md
├── rtl/
│   └── binary_to_gray.sv
├── tb/
│   └── binary_to_gray_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a fixed 4-bit converter and checks a
deterministic sequence of binary inputs against explicit Gray-code outputs.

The checks cover:

- zero and one
- adjacent low-bit transitions
- a mid-range transition
- upper-half and all-ones values

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/binary_to_gray/run_sim.py
```

Success is indicated by:

```text
PASS binary_to_gray deterministic checks
```
