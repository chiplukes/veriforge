# `common_cells/read`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream pass-through behavior while removing the
parameterized type surface by fixing the subset to an 8-bit payload.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/read.sv`

## Why This Is Useful

This example adds a tiny self-contained structural helper outside the stream and
counter families. It exercises:

- direct 8-bit input-to-output pass-through
- stable propagation across several representative values

## Local Layout

```text
examples/pulp/common_cells/read/
├── README.md
├── rtl/
│   └── read.sv
├── tb/
│   └── read_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a fixed 8-bit pass-through cell and checks a
deterministic sequence of input values against the output.

The checks cover:

- zero
- alternating-bit patterns
- upper-half and all-ones values

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/read/run_sim.py
```

Success is indicated by:

```text
PASS read deterministic checks
```
