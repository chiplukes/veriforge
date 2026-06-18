# `common_cells/onehot_to_bin`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream combinational decode behavior while removing
the upstream assertion include and derived-parameter surface, fixing the
wrapper to an 8-bit one-hot subset.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/onehot_to_bin.sv`

## Why This Is Useful

This example adds another tiny self-contained combinational helper outside the
stream and counter families. It exercises:

- zero-input decode to zero
- one-hot decode across low, middle, and high bit positions
- stable binary index generation from the one-hot mask

## Local Layout

```text
examples/pulp/common_cells/onehot_to_bin/
├── README.md
├── rtl/
│   └── onehot_to_bin.sv
├── tb/
│   └── onehot_to_bin_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a fixed 8-bit decoder and checks a deterministic
sequence of one-hot inputs against explicit 3-bit binary outputs.

The checks cover:

- zero input
- bit 0 and bit 1
- mid-range positions
- the highest valid bit

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/onehot_to_bin/run_sim.py
```

Success is indicated by:

```text
PASS onehot_to_bin deterministic checks
```
