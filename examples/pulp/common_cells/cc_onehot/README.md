# `common_cells/cc_onehot`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream one-hot detection behavior while trimming the
parameterized tree structure down to a fixed 4-bit subset that stays inside the
current parser and simulation surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/cc_onehot.sv`

## Why This Is Useful

This example adds a slightly richer combinational helper outside the stream and
counter families. It exercises:

- rejecting zero
- accepting exactly one asserted bit
- rejecting multiple asserted bits

## Local Layout

```text
examples/pulp/common_cells/cc_onehot/
├── README.md
├── rtl/
│   └── cc_onehot.sv
├── tb/
│   └── cc_onehot_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a fixed 4-bit detector and checks a deterministic
sequence of input vectors against explicit one-hot expectations.

The checks cover:

- zero input
- each of the four one-hot cases
- representative multi-bit non-one-hot cases

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/cc_onehot/run_sim.py
```

Success is indicated by:

```text
PASS cc_onehot deterministic checks
```
