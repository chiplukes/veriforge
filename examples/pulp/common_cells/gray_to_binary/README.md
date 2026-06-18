# `common_cells/gray_to_binary`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream combinational Gray-to-binary behavior while
fixing the wrapper to a small 4-bit subset.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/gray_to_binary.sv`

## Why This Is Useful

This example adds the natural counterpart to `binary_to_gray` as another tiny
self-contained combinational helper outside the stream and counter families. It
exercises:

- direct Gray-to-binary conversion
- low-bit transitions back to adjacent binary values
- upper-half and all-ones Gray patterns

## Local Layout

```text
examples/pulp/common_cells/gray_to_binary/
├── README.md
├── rtl/
│   └── gray_to_binary.sv
├── tb/
│   └── gray_to_binary_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a fixed 4-bit converter and checks a
deterministic sequence of Gray-code inputs against explicit binary outputs.

The checks cover:

- zero and one
- adjacent low-bit transitions
- a mid-range transition
- upper-half and all-ones values

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/gray_to_binary/run_sim.py
```

Success is indicated by:

```text
PASS gray_to_binary deterministic checks
```
